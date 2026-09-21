# Hermes 的 64 位值表示与 HBC 字节码格式

> 目录：`04-移动开发/03-跨平台/React-Native/Hermes值与字节码/`
> 语言：Python（`python/main.py` + `python/selfcheck_hermes.py`，**71 断言实跑全绿**）/ Go（`go/hermes.go` + `go/main.go` 人工审查 + bracket_check + go_sanity + go_crossref）

## 一、简介

Hermes 是 React Native 的默认 JS 引擎。它的两个底层设计决定了「RN 上 JS 值的开销」：

1. **HermesValue**：一个 JS 值就是 **一个 64 位字**，用 NaN-boxing 把类型塞进高 16 位；
2. **HBC（Hermes Bytecode）**：预编译字节码文件的布局，文件头刻意做成 32 字节对齐，函数头还带一个「溢出」逃生舱。

本 demo 逐位复刻这两件事，并断言了几个反直觉的边界（例如 `ETag` 编码下数据位其实只有 47 位）。

## 二、原理

### 2.1 NaN-boxing：高 16 位当标签

IEEE754 的 quiet NaN 有整整 51 位尾数可以借。Hermes 借用**高 16 位**（`kNumTagExpBits = 16`），留下 **48 位数据位**（`kNumDataBits = 48`，`kDataMask = 2^48 - 1`）。

源码注释写明标签区间是 **`[0xfff9 .. 0xffff]`**，而 `0xfff8` 对应规范 quiet NaN —— 也就是说：

```cpp
inline bool isDouble() const { return raw_ < ((uint64_t)Tag::First << kNumDataBits); }
```

判定阈值就是 `0xFFF9000000000000`，**严格小于**：`0xFFF8...` 是 double，`0xFFF9...` 开始是标签。

七个基础标签（`llvh::SignExtend32<8>` 得到的是负数）：

| Tag | 值 | 16 进制 | 含义 |
| --- | --- | --- | --- |
| `EmptyInvalid` | `-7` | `0xfff9` | 空槽 / （slow debug 下）非法值 |
| `UndefinedNull` | `-6` | `0xfffa` | undefined / null |
| `BoolSymbol` | `-5` | `0xfffb` | 布尔 / SymbolID |
| `NativeValue` | `-4` | `0xfffc` | 原生值 |
| `Str` | `-3` | `0xfffd` | 字符串指针（指针区起点） |
| `BigInt` | `-2` | `0xfffe` | BigInt 指针 |
| `Object` | `-1` | `0xffff` | 对象指针，同时也是 `Last` |

### 2.2 扩展标签：把前四个标签再向右借一位

48 位数据位对 `SymbolID`（32 位）之类太浪费。于是**前四个标签各扩一位**，变成 4 位宽的 ETag（`ETag = 2×Tag` 或 `2×Tag+1`）：

```cpp
constexpr HermesValue(uint64_t val, Tag  tag)  : raw_(val | ((uint64_t)tag  << 48)) {}
constexpr HermesValue(uint64_t val, ETag etag) : raw_(val | ((uint64_t)etag << 47)) {}
```

| 值 | raw | 说明 |
| --- | --- | --- |
| `undefined` | `0xFFFA000000000000` | `ETag::Undefined = -12` |
| `null` | `0xFFFA800000000000` | 同一个基础标签，bit47 = 1 |
| `true` | `0xFFFB000000000001` | 布尔值放数据位 |
| `empty` | `0xFFF9000000000000` | |
| `object@0x1234` | `0xFFFF000000001234` | 指针原样放低 48 位 |

用基础 `Tag` 编码的值，读成 ETag 时落在**偶数那一档**（`Tag::Object = -1` → `ETag::Object1 = -2`）。

> **bit47 的代价**：ETag 编码时标签占了 bit47，可用数据位实际只有 **47 位**。若 `val ≥ 2^47`，会直接把标签改掉 —— 本 demo 的 C14 断言到 `undefined` 被写成 `null`。

### 2.3 指针：48 位刚好够

```cpp
static void validatePointer(const void *ptr) {
  assert((reinterpret_cast<uintptr_t>(ptr) & ~kDataMask) == 0 && "Pointer top bits are set");
}
```

x86-64 要求 bit47 符号扩展到高 16 位，ARM64 同理，而用户态堆地址高位恒为 0，所以 48 位正好放下。`isPointer()` 的阈值是 `Tag::FirstPointer << 48 = 0xFFFD000000000000`（**大于等于**）。

原生指针是例外：**它不参与 NaN-boxing**，源码要求调用方自己保证「这个指针的位模式恰好是一个合法 double」，否则 GC 扫描时会把它当数字跳过（Android 上 MTE 把标记放在 56~59 位，正好不影响低 48 位与 NaN 判定）。

### 2.4 NaN 判定要掩掉符号位

```cpp
uint64_t kMask = llvh::maskLeadingZeros<uint64_t>(1);   // 0x7FFF...FF
return (this->raw_ & kMask) == (encodeNaNValue().raw_ & kMask);
```

所以 **负的 quiet NaN（`0xFFF8...`）也算 NaN**。

### 2.5 HBC 文件头：128 字节，32 的整数倍

```cpp
const static uint64_t MAGIC = 0x1F1903C103BC1FC6;   // "Hermes" 的古希腊语 Ἑρμῆ
const static uint64_t DELTA_MAGIC = ~MAGIC;          // delta 形态，不可执行
static constexpr size_t BYTECODE_ALIGNMENT = alignof(uint32_t);  // 4
```

`MAGIC` 按 UTF-16BE 拆开是四个码元 `0x1F19 0x03C1 0x03BC 0x1FC6` → **Ἑρμῆ**。

头部字段：`magic(8) + version(4) + sourceHash(20) + 20 个 uint32 计数/偏移(80) + options(1) + padding(19) = 128`，源码用 `static_assert(sizeof(BytecodeFileHeader) % 32 == 0)` 钉住。尾部 `BytecodeFileFooter` 只有 20 字节的 `fileHash`（「正常执行时不会读，以免破坏局部性」）。

### 2.6 变长结构的两个哨兵

- `SmallStringTableEntry`：`isUTF16:1 / offset:23 / length:8`，正好一个 uint32。`INVALID_OFFSET = 1<<23`、`INVALID_LENGTH = 255`；`isOverflowed()` 判据是 **`length == INVALID_LENGTH`**（长度溢出才去查溢出表，offset 溢出只是写不进这个位域）。
- `SmallFuncHeader`：放不下的字段走 `overflowed` 逃生舱 —— 把 32 位的大函数头偏移拆成 `offset(低16)` 与 `infoOffset(高16)`，还原时 `(infoOffset << 16) | offset`。

## 三、对比

| 维度 | V8（pointer tagging） | Hermes |
| --- | --- | --- |
| 值宽度 | 32 位（压缩指针 + 标签） | **64 位 NaN-boxing** |
| double 表示 | 装箱成 HeapNumber | **直接存位模式，零装箱** |
| 标签位置 | 低 2 位（指针对齐） | **高 16 位（借 NaN 空间）** |
| 类型扩展 | smi / heap object 两类 | 7 个 Tag + 14 个 ETag |
| 指针限制 | 32 位压缩 + 基址 | 48 位原样 |

## 四、环境

- Python 3.13（标准库）
- Go 1.21+（无本机工具链，Go 版只做人工审查与静态检查）

## 五、运行

```bash
cd python && python main.py                # 打印标签表、编码结果、HBC 头信息
cd python && python selfcheck_hermes.py    # 71 项断言
```

## 六、关键代码

| 文件 | 对应源码 |
| --- | --- |
| `python/main.py:hv_with_tag / hv_with_etag` | `HermesValue.h:556-560` |
| `python/main.py:is_double / is_pointer` | `HermesValue.h:416-420` |
| `python/main.py:is_nan` | `HermesValue.h:424-430` |
| `python/main.py:HEADER_FIELDS / header_size` | `BytecodeFileFormat.h:68-92` |
| `python/main.py:SmallFuncHeader` | `BytecodeFileFormat.h:308-350` |

## 七、性能边界

- **double 零装箱**是 NaN-boxing 最大的收益：数值不产生堆对象，GC 压力小。
- 代价是**每个值都占 8 字节**（32 位平台上另有 `SmallHermesValue` 适配层提供相同 API）。
- 文件头 128 字节 + `padding[19]` 的刻意填充，是为了让紧随其后的函数头**不跨 cache line**；`SmallFuncHeader` 也被 `static_assert(32 % sizeof == 0)` 约束。
- Footer 里的 `fileHash` 在正常执行路径上不读。

## 八、坑

1. **`isDouble` 是严格小于**：`0xFFF9...` 已经是标签，只有 `0xFFF8...` 及以下才是 double。
2. **ETag 编码的数据位只有 47 位**，传 ≥ 2^47 的 val 会**改掉标签**。
3. **负 NaN 也是 NaN**（判定前先掩符号位）。
4. **`isPointer` 是大于等于** `0xFFFD000000000000`，`undefined/null/bool` 都不算指针。
5. **原生指针是「伪装的 double」**，不能 NaN-box，GC 也看不见它。
6. **指针必须 ≤ 48 位**，否则触发断言（源码注释提到可以靠 8 字节对齐再省 3 位，但当前没做）。
7. `DELTA_MAGIC = ~MAGIC` 的文件**不能执行**，只用于 OTA 差分。
8. `isOverflowed()` 只看 `length`，**`offset` 溢出与否不影响这个标志**（两者都要小于各自哨兵才会直接写进位域）。
9. `SmallFuncHeader` 溢出时**其余位域全部失效**，只剩 `flags` 与偏移可用。

## 九、参考资料（实际读过）

- `facebook/hermes@main` — `include/hermes/VM/HermesValue.h`、`include/hermes/VM/SmallHermesValue.h`、`include/hermes/BCGen/HBC/BytecodeFileFormat.h`、`include/hermes/Support/SHA1.h`、`include/hermes/BCGen/HBC/Bytecode.h`
  （经 `cdn.jsdelivr.net/gh/facebook/hermes@main/...` 抓取；jsDelivr 对整个仓库报 `Package size exceeded 50 MB`，因此按单文件取）
- W3C / ECMA-402 无关；IEEE754 quiet NaN 的位模式约定见 `HermesValue.h` 顶部注释（canonical quiet NaN = `0xfff8`）
