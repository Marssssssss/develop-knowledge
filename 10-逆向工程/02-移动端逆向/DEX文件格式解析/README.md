# DEX 文件格式解析

## 简介

`.dex`（Dalvik Executable）是 Android 运行时（Dalvik / ART）实际加载的字节码容器：一个 classes.dex 里装着全部类定义、字符串池、方法原型、方法索引表与字节码流。逆向时它是**静态分析的第一入口** —— jadx / apktool / baksmali / dexlib2 全部建立在对这份格式的解析上。理解 header_item 的字段落位、LEB128 的变长编码、MUTF-8 与普通 UTF-8 的差异、以及 `method_idx_diff` 的差分语义，是读懂 smali 与自行构造/修补 dex 的前提。

本目录按 AOSP 官方《Dalvik 可执行文件格式》实现：

- `dex_leb128.py` — LEB128（uleb128 / sleb128 / uleb128p1）与 MUTF-8 编解码
- `dex_build.py` — 按官方 file layout 构造一个结构完整的 039 版 dex（含 adler32 / SHA-1）
- `dex_format.py` — 常量表与解析器（header_item → map_list → string/type/proto/method/class_def → class_data → code_item）
- `selfcheck_dex.py` — 与官方原文逐条对拍的自检
- `dex_format.go` / `dex_selfcheck.go` — 同构的 Go 实现（本机无 Go 工具链，走人工审查 + 括号配平 + 交叉引用校验）

## 原理详解

### 1. 文件骨架

官方把文件切成若干区段：

| 区段 | 内容 | 对齐 |
| --- | --- | --- |
| header | `header_item`（0x70 字节，v40 及以下） | 4 |
| string_ids | `string_id_item[]`（每项 4 字节：一个 `string_data_off`） | 4 |
| type_ids | `type_id_item[]`（4 字节 `descriptor_idx`） | 4 |
| proto_ids | `proto_id_item[]`（12 字节） | 4 |
| field_ids | 8 字节 | 4 |
| method_ids | 8 字节 | 4 |
| class_defs | 32 字节 | 4 |
| data | 变长项（string_data / type_list / class_data / code_item / map_list …） | 按项要求 |

三个最容易踩的点：

- **header_size 是常量**：v40 及以下必须为 `0x70`（112），v41 起因为新增 `container_size` / `header_offset` 变成 `0x78`（120）。它不是"随便填的长度"。
- **type_ids / proto_ids 至多 65535 项**（`method_id_item` 的 class_idx/name_idx 是 ushort），超过就得多 dex。
- **data 区段的项没有统一索引**，只有 `map_list` 能"遍历整个文件"：它把每个类型（含偏移与数量）列出，按偏移升序且互不重叠，同一类型最多出现一次。

### 2. 两套变长编码

`uleb128` / `sleb128` 借鉴 DWARF3，仅用于 32 位数，1–5 字节；除最后一个字节外每字节最高位置 1，其余 7 位是载荷、低字节在前。官方给的四行示例表是最可靠的判据：

| 编码 | sleb128 | uleb128 | uleb128p1 |
| --- | --- | --- | --- |
| `00` | 0 | 0 | -1 |
| `01` | 1 | 1 | 0 |
| `7f` | -1 | 127 | 126 |
| `80 7f` | -128 | 16256 | 16255 |

`uleb128p1` 是"uleb128 值减 1"，目的是让 `-1`（无符号 `0xffffffff`）编码成单字节 `0x00`，用于 `NO_INDEX`。注意它**只能**表示非负数和 -1，`-2` 就展开成 5 字节。

### 3. MUTF-8 ≠ UTF-8

官方明确说"比 UTF-8 更接近 CESU-8"：

- `U+0000` 编成 `c0 80`（所以字符串里能含 NUL，同时还能当 C 风格终止串用）；
- 增补平面字符**不编成 4 字节 UTF-8**，而是拆成两个 UTF-16 代理码元、各编 3 字节（共 6 字节）；
- 因此 `string_data_item.utf16_size` 是 **UTF-16 码元数**，与字节长度经常不相等（`"😀"` 是 2 与 6）。

副作用也写在官方原文里：对两个 MUTF-8 串调用 C 的 `strcmp()` 结果**不一定**是正确的带符号序；要排序必须逐码元解码再比。另外 string_ids 必须"使用 UTF-16 码位值按字符串内容排序（不采用语言区域敏感方式）"，本目录的 `string_sort_key()` 就实现这一条。

### 4. checksum 与 signature 的覆盖口径

- `signature`（20 字节）= 除 **magic、checksum、signature** 之外的 SHA-1；
- `checksum`（4 字节）= 除 **magic、checksum** 之外的 adler32 —— 也就是**包含 signature**。

所以构造顺序必须是"先写 signature，再算 checksum"，反过来两者互相破坏。这是本目录实测踩到的第一个坑（第一版先算 checksum 再填 signature，回读校验直接失败）。

### 5. class_data_item 的差分索引

`encoded_method` 里的 `method_idx_diff` 是**与列表中前一个元素的索引之差**，不是绝对索引。于是 direct_methods / virtual_methods 列表必须按 method_idx **升序**排列，否则差分为负、uleb128 根本无法编码。解析侧必须做累加还原。

`code_off` 为 0 表示 abstract / native —— 这类方法**照样要登记在 class_data 里**，只是没有 code_item。

### 6. code_item 与 padding

`code_item`：`registers_size` / `ins_size` / `outs_size` / `tries_size` / `debug_info_off` / `insns_size` / `insns[]`（以 16 位代码单元计）。官方写明 padding（2 字节）**只有 `tries_size` 非零且 `insns_size` 为奇数时才存在**，用来让 tries 四字节对齐。`code_item_units()` 实现了这条判据。

## 对比：DEX 与 class 文件

| 维度 | DEX | Java class |
| --- | --- | --- |
| 组织方式 | 单文件容纳所有类，共享全局常量池 | 一文件一类，各有常量池 |
| 字符串编码 | MUTF-8，代理对拆开编 | 同样用改型 UTF-8，但结构不同 |
| 索引压缩 | 大量 uleb128 + 差分（`method_idx_diff`） | 主要是定长 u2/u4 常量池索引 |
| 寄存器模型 | 基于寄存器的字节码（寄存器数写在 code_item 里） | 基于操作数栈（max_stack / max_locals） |
| 全局可遍历性 | 有 `map_list` | 靠属性表逐项解析 |

## 环境

- Python 3.8+（仅用标准库：`struct` / `hashlib` / `zlib`）
- Go 1.21+（本机无工具链，未实跑，仅人工审查 + 静态校验）

## 运行方式

```bash
python selfcheck_dex.py        # 109 项断言，全部通过
# Go（需本机有 go 工具链时）
go run .                        # dex_format.go + dex_selfcheck.go 同包
```

## 关键代码

```python
# 1) LEB128：最后一字节的最高位是终止标志，其余 7 位是载荷
def uleb128_decode(buf, pos=0):
    result = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):          # 最高位清除 → 序列结束
            return result & 0xFFFFFFFF, pos
        shift += 7

# 2) sleb128 的符号位在最后一字节的第 6 位（不是第 7 位）
if byte & 0x40:
    result -= 1 << shift

# 3) MUTF-8：NUL 写成 c0 80，增补字符拆代理对各编 3 字节
if cp == 0x00:
    out += b"\xc0\x80"

# 4) checksum 覆盖 signature，故顺序是 signature → checksum
body[12:32] = hashlib.sha1(bytes(body[32:])).digest()
checksum = zlib.adler32(bytes(body[12:])) & 0xFFFFFFFF

# 5) method_idx_diff 是差分，构造侧必须升序、解析侧必须累加
entries.sort(key=lambda e: e[0])
prev += diff
```

## 性能与边界

- LEB128 单值最多 5 字节 / 32 位；`uleb128_encode` 对 >0xFFFFFFFF 直接断言失败（DEX 不用 64 位 LEB128）。
- type_ids 与 proto_ids 上限 65535；字符串表按 UTF-16 码位排序是**格式要求**而非建议，顺序错了 ART 的二分查找会失败。
- `code_item` 的 `insns_size` 以 16 位代码单元计，`insns` 数组本身按 ushort 读；字节序交换只在单个 ushort 上进行，不在更大的内部结构上进行（官方原文注释）。
- 本目录构造的是"结构完整的最小 dex"，**不保证能通过 ART 的 verifier**：真实 dex 还有指令校验、寄存器类型推断、try/catch 覆盖等约束。

## 注意事项与常见坑

1. **先算 signature 再算 checksum** —— 反过来会让两者都不匹配（本目录实测）。
2. **`utf16_size` 不是字节数** —— 含增补平面字符或 NUL 时两者必然不等，用 `len(bytes)` 当 utf16_size 会解析崩。
3. **`method_idx_diff` 是差分** —— 列表不按 method_idx 升序就编码不出（第一版构造时被打乱，直接触发断言）。
4. **abstract/native 方法也要登记** —— 只是 `code_off = 0`；把它整个从 class_data 里剔掉会让方法凭空消失。
5. **padding 只在 tries_size ≠ 0 且 insns_size 为奇数时出现** —— 别在无 try 的方法后面多读/少读 2 字节，否则后续所有 code_item 都会错位。
6. **`NO_INDEX = 0xffffffff` 而不是 0** —— 0 是一个合法索引；`superclass_idx` 为 NO_INDEX 才是"根类（如 Object）"。
7. **map_list 不是"可选索引"** —— 它是官方推荐的遍历入口；同一类型最多一项、按偏移升序、不重叠，任何一条不满足都说明这个 dex 被动过手脚。

## 参考资料（实际读过）

- [Dalvik 可执行文件格式 — AOSP 官方（source.android.google.cn 中文镜像）](https://source.android.google.cn/docs/core/dalvik/dex-format) —— LEB128 示例表、header_item / string_data_item / MUTF-8 / map_list 类型代码表 / code_item / encoded_method 全部取自此页
- [Dalvik 字节码 — AOSP 官方](https://source.android.google.cn/docs/core/dalvik/dalvik-bytecode) —— 本节 opcode 与 payload 伪运算码（相邻 demo 用）
- [Dalvik 指令格式 — AOSP 官方](https://source.android.google.cn/docs/core/dalvik/instruction-formats) —— 格式 ID 命名规则与位布局
