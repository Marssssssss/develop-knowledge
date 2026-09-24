# objc4 类布局：`class_data_bits_t` 的 FAST 位域与 `class_rw_t` / `class_rw_ext_t` 脏内存分离

> 主题来自 `04-移动开发 / 01-iOS / 运行时`。一个 Objective‑C 类的「方法、属性、协议」在磁盘上是只读的 `class_ro_t`；
> 只有真的需要改类（挂分类、加方法、运行时改布局）时，runtime 才额外分配一份 `class_rw_ext_t` 作为**脏内存**。
> 判断「现在指向的是 ro 还是 rw」这件事，被压缩进 `class_data_bits_t` 那一个字的空余位里。
> 本 demo 把这条链路——位打包 → 判据 → 分配 → 列表搬运——完整复现出来。

## 一、实读的权威来源

全部来自 `apple-oss-distributions/objc4` 的 main 分支，逐字节抓取后本地解析：

| 文件 | 大小 | 本 demo 用到的位置 |
| --- | --- | --- |
| `runtime/objc-runtime-new.h` | 115042 B | 118–180 行 `FAST_*`、76–112 行 `RW_*`、1598 行 `class_ro_t`、2195–2360 行 `class_rw_ext_t` / `class_rw_t` / `methodAlternates`、2024–2135 行 `list_array_tt::attachLists`、2364 行起 `class_data_bits_t` |
| `runtime/objc-runtime-new.mm` | 339258 B | 1581–1625 行 `class_rw_t::extAlloc` |
| `runtime/PointerUnion.h` | 7097 B | 70–165 行两类型的 `PointerUnion` |

## 二、一个字里塞了什么

`class_data_bits_t` 的全部成员只有一个 `explicit_atomic<uintptr_t> bits`。它同时承担三件事：

1. 存 `class_ro_t *` 或 `class_rw_t *`；
2. 存 3 个（32 位是 2 个）FAST 标志；
3. 存 1 个「这是 rw 指针」的标记位。

之所以能这么塞，是因为三类位两两不相交（自检里逐条验证过）：

| 常量 | LP64 iPhone 真机 | LP64 非真机 | ILP32 |
| --- | --- | --- | --- |
| `FAST_DATA_MASK` | `0x0f00007ffffffff8` | `0x0f007ffffffffff8` | `0xfffffffc` |
| `FAST_FLAGS_MASK` | `0x7` | `0x7` | `0x3` |
| `FAST_IS_RW_POINTER` | `1<<63` | `1<<63` | `0` |
| 指针实际保留的位 | bit3–38、bit56–59 | bit3–46、bit56–59 | bit2–31 |

真机与非真机的差别只有一处：非真机多留 bit39–46 共 8 位（差集恰为 `0x7f8000000000`），
真机掩码是非真机掩码的真子集。三个 FAST 标志是
`FAST_IS_SWIFT_LEGACY=1<<0`、`FAST_IS_SWIFT_STABLE=1<<1`、`FAST_HAS_DEFAULT_RR=1<<2`。

## 三、`has_rw_pointer` 在 32/64 位下是两套判据

```cpp
static bool has_rw_pointer(uintptr_t bits) {
#if FAST_IS_RW_POINTER
    return (bool)(bits & FAST_IS_RW_POINTER);        // 64 位：只看 bit63
#else
    return bits != 0 && (flags(bits) & RW_REALIZED); // 32 位：去看 class_rw_t->flags
#endif
}
```

32 位没有那个标记位，只能退化成「指针非空 **且** 目标 `class_rw_t` 的 flags 里已经置了 `RW_REALIZED`」。
推论有点反直觉：32 位下 `setData()` 一个还没置 `RW_REALIZED` 的 `class_rw_t`，
**调用完之后 `has_rw_pointer()` 仍然是假**——因为 `setData` 里 `| FAST_IS_RW_POINTER` 在 32 位是个空操作。
`safe_ro()` 接着就会把 `class_rw_t*` 当成 `class_ro_t*` 解出来，正是源码那段注释警告的情形。

## 四、`setData` 只保留旧字的 FAST 标志位

```cpp
uintptr_t newBits = ((authedBits & FAST_FLAGS_MASK) | (uintptr_t)newData | FAST_IS_RW_POINTER);
```

旧字的指针部分被整个丢掉，只有低 3 位（32 位是低 2 位）留下来。另外 `setData` 开头有
`ASSERT(!has_rw_pointer() || (newData->flags & (RW_REALIZING | RW_FUTURE)))`——
已经指向 rw 之后再写一个 rw，必须带 `RW_REALIZING` 或 `RW_FUTURE`，否则就是并发 realization 出错。

改标志走的是另一条路：`setAndClearBits` 先把字 auth 出裸值、`(authBits | set) & ~clear`、重新 sign，
最后用 `compare_exchange_weak` 弱循环。它要求 `has_rw_pointer()` 已经成立，且 `set` 与 `clear` 不相交。

## 五、`flags(bits)` 故意不验签

```cpp
// This intentionally DOES NOT check the signatures
static uint32_t flags(uintptr_t bits) {
    pflags = ptrauth_strip((const uint32_t *)bits, CLASS_DATA_BITS_RO_SIGNING_KEY);
    pflags = (const uint32_t *)((uintptr_t)pflags & FAST_DATA_MASK);
    return *pflags;
}
```

它靠 `static_assert(offsetof(class_rw_t, flags) == 0 && offsetof(class_ro_t, flags) == 0)` 保证
「不管指向 ro 还是 rw，偏移 0 那个 uint32 就是 flags」，然后用 `ptrauth_strip`（xpacd）剥掉签名——
注释解释了为什么可以无条件用 RO 密钥：两个密钥都会生成 xpacd 指令，strip 不校验。
后果是：一个签名被篡改的字，`data()` 会验签失败，但 `flags(bits)` 照样读出正确的 flags。
本 demo 用「翻转一个 PAC 位」做了成对用例。

## 六、`safe_ro` 为什么强调「只 load 一次」

```cpp
uintptr_t bitsValue = bits.load(std::memory_order_relaxed);
if (has_rw_pointer(bitsValue)) return data()->ro();
```

注释说得很直白：如果分两次 load，可能先看到 `!has_rw_pointer`，随后并发地被人存进 `class_rw_t*`，
于是你拿 RO 的签名方案去解一个 RW 指针。本 demo 在模型里放了探针，断言两条路径互斥且只走其一。

## 七、`ro_or_rw_ext` 是最低位标签的 `PointerUnion`

```cpp
using ro_or_rw_ext_t = objc::PointerUnion<const class_ro_t, class_rw_ext_t,
                                          PTRAUTH_STR("class_ro_t"), PTRAUTH_STR("class_rw_ext_t")>;
```

`PointerUnion.h` 明说这是 ABI：**T1 原样存，T2 把最低位置 1**，取值时 `auth(_value) & ~1`。
所以「有没有 rwe」就是一次 `& 1`。顺带一个边角：`isNull()` 判的是整个字为零，
而一个「打了标签的空 rwe 指针」值是 1——`isNull()` 为假、但 `operator bool()` 也为假，两者并不一致。

分配 rwe 的那一步 `set_ro_or_rwe(rwe, ro)` 先写 `rwe->ro = ro` 再用 release 屏障存指针，
注释说这是为了让无锁读者能看到 `rwe->ro` 的初始化。

## 八、`extAlloc`：深拷贝只对方法生效

```cpp
rwe->version = (ro->flags & RO_META) ? 7 : 0;
```

三个列表的搬运并不对称：

- **方法**：`method_list_t` 时 `deepCopy` 就 `duplicate()`；`relative_list_list_t` 时逐个 `duplicate()` 再
  **逐个** `attachLists`。因为 `attachLists` 每次都把新列表插到最前，逐个挂的结果是**顺序被反转**。
- **属性与协议**：源码注释写着 "property lists and protocol lists historically have not been deep-copied /
  This is probably wrong and ought to be fixed some day"。所以 `deepCopy` 对它们无效——
  既不复写对象，也不会反转顺序。

本 demo 用同一份 ro 跑 `deep=false` 与 `deep=true`，断言方法顺序变成 `[base2', base1', base0']`
而属性仍是 `[prop0, prop1]`。`rwe->version` 则断言元类得 7、非元类得 0。

## 九、`attachLists` 的新老排布

四种存储形态：`null` / 单个 `List` / `array_t` / `relative_list_list_t`。规则是
**新列表放在数组最前，老的整块后移 `addedCount`**——这就是「分类方法覆盖主类方法」的布局根源。
另外 `addedCount == 0` 时直接 return，`attachListList` 只允许挂到空存储，
`copyListList` 只取前 `numLoaded` 个且 `numLoaded == 1` 时退化成单个 list。

## 十、`demangledName` 是一次 CAS

```cpp
if (!CompareAndSwap<const char *>(nullptr, de ?: mangled, &rwe->demangledName)) { if (de) free(de); }
```

只在当前为 `nullptr` 时写入；解修饰失败（`de` 为空）就用原始 mangled 名兜底；
写入失败且 `de` 非空则由调用方释放。

## 运行

```bash
cd python && python selfcheck_rwbits.py   # 111 条断言，实跑全绿
cd python && python main.py               # 演示入口
cd go     && go run .                     # 同题 Go 实现（本机无工具链，走四项静态检查）
```

Go 侧无本机工具链，按仓库惯例走人工审查 +
`bracket_check`（6 个文件全 BALANCED）/ `go_sanity`（6 文件通过）/ `go_crossref`（无重名、无未定义调用）/
`syntax_sanity`（0 问题）。

## 口径与注意事项

- **PAC 位**：真实 arm64e 的签名位由硬件决定，源码不可见。本 demo 用确定性的 `mix()` 占位，
  只保证「签名位落在 `FAST_DATA_MASK` 之外」这一条从源码可证的性质
  （`data()` 先 auth 再 `& FAST_DATA_MASK`；`has_rw_pointer()` 与 `flags()` 又要求签名不影响 bit63 与低 3 位）。
  凡涉及具体 PAC 数值的结论一律未作断言。
- **判别子**：`ptrauth_blend_discriminator(&bits, ...)` 里的地址，模型里用 `ClassDataBits` 的身份代替。
- `ListArray` 的 0/1/array/rel 四态、`PointerUnion` 的标签位、`extAlloc` 的深拷贝不对称，
  都是照源码逐分支转写，没有为了好测而简化。
