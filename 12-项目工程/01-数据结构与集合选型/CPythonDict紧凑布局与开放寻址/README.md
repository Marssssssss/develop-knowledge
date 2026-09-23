# CPython dict：紧凑布局、perturb 探测与「删除不腾位」

> 为什么 `dict` 的迭代顺序是插入顺序？为什么删掉一半元素之后再 `insert` 反而会触发一次
> 比原表更小的 rehash？为什么一个只装 170 个元素的 dict，索引表要用 2 字节一格？
> 这三个问题的答案都藏在 `Objects/dictobject.c` 的几个宏里。本 demo 把 CPython main 分支
> 的 dict 实现**逐宏复刻**成一个可以跑的模型，让「扩容点 / 探测序 / 索引宽度 / 压实」
> 都变成可断言的数字。

代码：`python/cpython_dict.py`（模型）、`python/selfcheck_cpython_dict.py`（302 条断言，实跑全绿）、
`python/main.py`（演示入口）、`go/`（同口径 Go 实现，四项静态检查通过）。

## 一、布局：索引表存的是「entries 下标」，不是指针

```text
PyDictKeysObject
  ├── dk_indices[]   定长 1/2/4/8 字节一格，值是 entries 数组下标
  │                  DKIX_EMPTY = -1 / DKIX_DUMMY = -2
  └── dk_entries[]   PyDictKeyEntry{me_hash, me_key, me_value} 按插入顺序追加
```

（`Include/internal/pycore_dict.h` 第 185-188 行定义三个哨兵，`dk_indices` 与 `dk_entries`
的字段注释在第 200-239 行。）

两个直接推论：

- **迭代顺序 = 插入顺序**——迭代走的是 `dk_entries`，不碰 `dk_indices`；
- **索引表是"紧凑"的**——一格只放下标，不需要放下标 + 指针，所以一次 cache line
  能扫过 64 个槽（1 字节/槽时）。

索引宽度由 `get_log2_bytes(log2_size)` 决定，四档：

| `log2_size` | `dk_size` | 字节/槽 | 容量 `USABLE_FRACTION` |
| --- | --- | --- | --- |
| 3 ~ 7 | 8 ~ 128 | 1 | 5 ~ 85 |
| 8 ~ 15 | 256 ~ 32768 | 2 | 170 ~ 21845 |
| 16 ~ 31 | 65536 ~ 2³¹ | 4 | 43690 ~ … |
| ≥ 32 | ≥ 2³² | 8 | … |

注意 `log2_size = 8`（`dk_size = 256`）时容量只有 170，下标范围 `0..169` 明明一个字节放得下，
但源码的产物是 **2 字节**——因为它直接用 `log2_bytes = log2_size + 1` 分档，不按容量算。
这就是「索引表 512 字节装 170 个元素」的来源。

## 二、探测序列：`5*j+1` 加一个不断右移的 perturb

```c
#define PERTURB_SHIFT 5
size_t perturb = (size_t)hash;
size_t i = (size_t)hash & mask;      // 起始位不加 perturb
for (;;) {
    perturb >>= PERTURB_SHIFT;
    i = mask & (i*5 + perturb + 1);
}
```

（`Objects/dictobject.c` 第 338 行定义 `PERTURB_SHIFT`，探测循环见 `do_lookup` 第 1112-1153 行、
`find_empty_slot` 第 1877-1889 行、`build_indices_*` 第 2137-2163 行——四处用的是同一套递推。）

源码注释里那张表在 `dk_size = 8`、`hash = 0` 时是
`0 -> 1 -> 6 -> 7 -> 4 -> 5 -> 2 -> 3 -> 0`，本 demo 复现完全一致。

三个容易写反的点：

1. **起始位不加 perturb**，第二轮才开始加。写成 `i = (hash*5 + hash + 1) & mask` 全盘错位。
2. **`perturb >>= 5` 在使用之前**，不是之后。
3. `perturb` 是无符号，右移够多轮必然归零（64 位 hash 需 **13 轮**），此后退化成纯 `5*j+1`；
   而 `5*j+1` 递推在 `2**i` 上生成**全部**整数，所以只要留了一个空槽就一定找得到。
   demo 里对 `k = 3..11` 逐个断言「探测序是槽位的一个排列」。

选 5 而不是 4 或 6 是 Tim Peters 做实验的结果（注释第 409 行原文），4 与 6 只是"没有显著更差"。

## 三、装载率、扩容点与那个反直觉的 GROWTH_RATE

```c
#define USABLE_FRACTION(n)  (((n) << 1)/3)     /* 最大装载率 2/3 */
#define GROWTH_RATE(d)      ((d)->ma_used*3)   /* 注意是 used*3，不是 size*2 */
```

`calculate_log2_keysize(minsize)` 是 `bit_length(minsize - 1)`（先 `Py_MAX` 到 8）——
减 1 这一步保证 `minsize = 8` 得到 3 而不是 4，正好落在 2 的幂上。

无删除时的扩容轨迹（demo 实跑）：

```text
start      size=8   capacity=5
第  6 个插入 size 8  -> 16   (GROWTH_RATE(5)=15  -> bit_length(14)=4)
第 11 个插入 size 16 -> 32   (GROWTH_RATE(10)=30 -> bit_length(29)=5)
第 22 个插入 size 32 -> 64   (GROWTH_RATE(21)=63 -> bit_length(62)=6)
第 43 个插入 size 64 -> 128  (GROWTH_RATE(42)=126 -> bit_length(125)=7)
```

「`used*3` 等价于翻倍」不是巧合：令 `used = USABLE_FRACTION(2^k) = ⌊2^(k+1)/3⌋`，
则 `3·used = 2^(k+1) − r`（`r = 2^(k+1) mod 3 ∈ {1,2}`），落在 `[2^k, 2^(k+1))` 里，
`bit_length(·−1)` 恰好得 `k+1`。demo 对 `k = 3..19` 全部断言了这个恒等式。

选用 `used*3` 而不是 `used*2` 的动机写在注释里：**删除量与插入量相当时留更多余量**
（历史上 3.2 之前是 `used*4`、3.3 是 `used*2`、3.4~3.6 是 `used*2 + capacity/2`）。

## 四、删除：腾出了位置，但没腾出"配额"

```c
dictkeys_set_index(mp->ma_keys, hashpos, DKIX_DUMMY);
...
/* We can't dk_usable++ since there is DKIX_DUMMY in indices */
```

（`delitem_common` 第 2955-2987 行；第 5079 行是 `popitem` 里的同款注释。）

删除做的是：索引位改 `DKIX_DUMMY`、`me_key`/`me_value` 置 `NULL`、`ma_used` 减一。
**`dk_usable` 不回增，`dk_nentries` 也不动。** 于是：

```text
插满 5 个 (size=8) : usable=0  nentries=5  used=5  dummies=0
删掉 2 个          : usable=0  nentries=5  used=3  dummies=2   ← usable 还是 0
再插 1 个          : size=16   nentries=4  used=4  dummies=0   ← 照样走了 dictresize
```

这是本 demo 最值得记住的一条：**"删一半再插"不会比"直接插"更省**，反而多付一次 rehash。
（顺带：`dict.copy()` 的快路径条件 `ma_used >= dk_nentries*2/3` 之所以存在，
就是因为 `'del' operation does not resize dicts`——见第 4531 行注释。）

## 五、dictresize 会把表"压实变小"

`dictresize` 的注释第一句就是 *"When entries have been deleted, the new table may actually
be smaller than the old one."* 压实逻辑是 `while (ep->me_value == NULL) ep++;`
（第 2281-2286 行），跳过所有洞，把存活条目**保持相对顺序**搬到新 entries 数组头部，
然后 `build_indices_*` 重建索引。收尾两行（第 2337-2338 行）：

```c
STORE_KEYS_USABLE(mp->ma_keys, mp->ma_keys->dk_usable - numentries);
STORE_KEYS_NENTRIES(mp->ma_keys, numentries);
```

即新表 `dk_usable = USABLE_FRACTION(newsize) − used`、`dk_nentries = used`——**dummy 全部消失**。

demo 复现：64 槽表装满 42 个，删掉 36 个（`used = 6`、`nentries = 42`、`dummies = 36`），
再插一个 → `GROWTH_RATE(6) = 18` → `bit_length(17) = 5` → **新表 32 槽，比原来的 64 还小**，
`nentries` 从 42 掉到 7，dummy 归零，剩下的 7 个条目顺序不变。

## 六、预分配：`estimate_log2_keysize`

```c
static inline uint8_t estimate_log2_keysize(Py_ssize_t n) {
    return calculate_log2_keysize((n*3 + 1) / 2);
}
```

它是 `USABLE_FRACTION` 的反函数：给 n 算一张**装 n 个不用再扩**的表。
注意不是 `n*3/2` 而是 `(n*3+1)/2`——`n = 1` 时前者给 1、后者给 2，都会被引到 `MINSIZE`，
但 `n = 5` 时前者给 7、后者给 8，结果一样；差别出现在更大的 n 上。
demo 对 `n = 1..199` 全量断言「`USABLE_FRACTION(2^k) >= n`」成立。

## 七、选型结论

| 场景 | 该知道的 |
| --- | --- |
| 延迟敏感路径上批量建 dict | 用 `dict.fromkeys` / 预估大小，避开 `6/11/22/43/86…` 这些扩容点的一次性 rehash |
| 长期存活、频繁增删的 dict | 删除不返还配额，`nentries` 只增不减，直到一次 rehash 才压实；内存不会随删除回落 |
| 超小 dict（≤5 个键） | 起始就 8 槽，索引 1 字节，索引表只有 8 字节——`dict` 比"自己写个线性数组"贵不了多少 |
| 内存敏感的百万级 dict | 索引宽度在 `dk_size` 跨 256 / 65536 / 2³² 时**跳档**，一次跳档索引表翻倍 |

## 参考资料（实际读过）

- `https://raw.githubusercontent.com/python/cpython/main/Objects/dictobject.c`
  （258 KB；读了 `PERTURB_SHIFT`、`USABLE_FRACTION`、`GROWTH_RATE`、`calculate_log2_keysize`、
  `estimate_log2_keysize`、`get_log2_bytes`、`new_keys_object`、`init_keys_object`、
  `insertion_resize`、`insert_combined_dict`、`do_lookup`、`lookdict_index`、
  `find_empty_slot`、`build_indices_generic/unicode`、`dictresize`、`delitem_common`、
  `dict` 迭代快路径的 `ma_used >= dk_nentries*2/3` 判据）
- `https://raw.githubusercontent.com/python/cpython/main/Include/internal/pycore_dict.h`
  （16 KB；`DKIX_EMPTY/DKIX_DUMMY/DKIX_ERROR/DKIX_KEY_CHANGED`、`PyDictKeysObject` 字段布局、
  `DK_SIZE`/`DK_MASK`/`DK_ENTRIES` 宏）
