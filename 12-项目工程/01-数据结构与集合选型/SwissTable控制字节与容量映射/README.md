# Swiss table：Abseil 与 hashbrown 的「同族不同参」

> 「swiss table」不是一份实现，而是一族实现。同一个想法（控制字节 + 分组 SIMD 匹配 + 三角探测）
> 在 Abseil 和 Rust hashbrown 里落地成了**两套互不兼容的字节约定**：
> Abseil 的 `kEmpty = 0x80` 在 hashbrown 里是 `DELETED`，Abseil 的 `kSentinel = 0xFF`
> 在 hashbrown 里是 `EMPTY`。再加上 Abseil 的容量是 `2^k − 1` 而 hashbrown 的桶数是 `2^k`，
> 任何"照着记忆写"的移植都会在同一个地方翻车。本 demo 把两派的每个常量与函数**并排复刻**。

代码：`python/swiss_table.py`（模型）、`python/selfcheck_swiss_table.py`（784 条断言，实跑全绿）、
`python/main.py`、`go/`（四项静态检查通过）。

## 一、控制字节：两派的哨兵正好互换

| 语义 | Abseil (`ctrl_t`) | hashbrown (`Tag`) |
| --- | --- | --- |
| 空槽 | `kEmpty = -128` = **0x80** | `EMPTY = 0xFF` |
| 已删 | `kDeleted = -2` = **0xFE** | `DELETED = 0x80` |
| 表尾 | `kSentinel = -1` = **0xFF** | 没有 sentinel（靠多分配 `WIDTH` 个控制字节） |
| 有值 | `int8_t >= 0`，即 0x00..0x7F | `b & 0x80 == 0`，即 0x00..0x7F |

两派对"是不是有值"的判据**完全一致**（最高位为 0），差别只在两个特殊字节谁是谁。
源码里那串 `static_assert` 解释了为什么必须是这几个值：

```cpp
static_assert(ctrl_t::kEmpty == static_cast<ctrl_t>(-128), "... to make the SIMD check ... efficient");
static_assert(ctrl_t::kDeleted == static_cast<ctrl_t>(-2), "...");
static_assert(ctrl_t::kSentinel == static_cast<ctrl_t>(-1), "... to elide loading it from memory ...");
static_assert((~kEmpty & ~kDeleted & kSentinel & 0x7F) != 0, "... scalar test for MaskEmptyOrDeleted()");
```

最后一条就是那个精妙的小技巧：`kEmpty(0x80)` 与 `kDeleted(0xFE)` 在第 0 位上**都没置位**，
而 `kSentinel(0xFF)` 在第 0 位上置位——于是 `~e & ~d & s & 0x7F` 非零。
hashbrown 的 `special_is_empty` 用的就是这条：`self.0 & 0x01 != 0` ⇒ 是 EMPTY。
（demo 对全部 256 个字节值断言了两派 `is_full` 的一致性。）

**Go 1.24 的 map 用的是 Abseil 那一套**（`ctrlEmpty = 0x80` / `ctrlDeleted = 0xFE`，见本目录 662），
所以"从 hashbrown 抄常量到 Go"必然错。

## 二、哈希切分：谁拿哪 7 位

| | H1（选起始位置） | H2（控制字节） |
| --- | --- | --- |
| Abseil | `H1(hash) = hash`（低位） | `hash >> 57`，**最高** 7 位 |
| hashbrown | `h1(hash) = hash as usize`（低位） | `hash >> 57`，**最高** 7 位 |
| Go 1.24 | `h >> 7`（高位） | `hash & 0x7f`，**最低** 7 位 |

两派的 tag 方向一致，但**H1 不同**：Abseil/hashbrown 用低位当起始偏移（因为容量就是掩码），
Go 用 `h >> 7`——把已经被 tag 用掉的低 7 位丢掉，避免"起始位置"与"tag"强相关。

方向差异在小 hash 上看不出来（demo 里 `0x80` 时三派都是 0x00），
但在 `1 << 57` 上暴露无遗：Abseil/hashbrown 得到 `0x01`，Go 得到 `0x00`。

## 三、容量形状：`2^k − 1` vs `2^k`

Abseil 的**槽位数恒为 `2^k − 1`**，因为末尾额外放一个 `kSentinel` 让迭代器知道到头了
（顺带省掉"当前是否越界"的判断）。于是：

```cpp
constexpr bool IsValidCapacity(size_t n) { return ((n + 1) & n) == 0 && n > 0; }
constexpr size_t NormalizeCapacity(size_t n) { return n ? ~size_t{} >> countl_zero(n) : 1; }
constexpr size_t NextCapacity(size_t n) { return n * 2 + 1; }   // 2^k-1 -> 2^(k+1)-1
```

`2^63 − 1` 这个数字对第一次读的人是反直觉的：**8 不是合法容量，7 才是**。

hashbrown 走的是另一条路：**桶数就是 `2^k`**，靠"多分配 `Group::WIDTH` 个控制字节并把
前 `WIDTH` 个字节复制到尾部"来避免越界判断（源码注释在 `raw.rs` 的 `num_ctrl_bytes` 处）。
于是 `capacity_to_buckets` 的结果永远是 2 的幂。

两家的小表下限也不同：

| | 小表规则 |
| --- | --- |
| Abseil | `capacity < kWidth - 1` 时**一个空槽都不留**（整张表在一个组里，探测不会越界） |
| hashbrown | `cap < 15` 时按 `(Group::WIDTH, elem_size)` 查下限：`(16, ≤1B) → 14`、`(16, ≤3B) 或 (8, ≤1B) → 7`、其余 `→ 3`，再取 4 / 8 / 16 个桶 |

hashbrown 那张下限表是为了避免「`ctrl_align` 比 `buckets * size` 还大」造成的巨大浪费——
源码注释里给了那张触目惊心的表：3 个元素在 16 字节对齐下要 36 字节（12 字节/元素）。

## 四、装载率：同样的 7/8，不同的写法

Abseil：

```cpp
constexpr inline size_t kMaxCapacityForLoadFactorOne = Group::kWidth * 4 - 1;   // kWidth=16 → 63
constexpr size_t CapacityToGrowth(size_t capacity) {
  if (capacity <= kMaxCapacityForLoadFactorOne)
    return capacity - (capacity >= Group::kWidth - 1);   // 小表最多留一个空槽
  return capacity - capacity / 8;                        // 大表 7/8
}
```

实测（`kWidth = 16`）：

```text
capacity=3    growth=3     (1.000)   ← 小于 kWidth-1 = 15，一个空槽都不留
capacity=7    growth=7     (1.000)
capacity=15   growth=14    (0.933)   ← 从 15 开始留一个
capacity=63   growth=62    (0.984)
capacity=127  growth=112   (0.882)   ← 超过 63 后走 7/8
capacity=1023 growth=896   (0.875)
```

hashbrown：

```rust
fn bucket_mask_to_capacity(bucket_mask: usize) -> usize {
    if bucket_mask < 8 { bucket_mask }                       // 桶数 ≤ 8：capacity = 桶数 - 1
    else { ((bucket_mask + 1) / 8) * 7 }                     // 大表 7/8
}
```

`1024` 个桶 → `896`，与 Abseil 的 `capacity = 1023 → 896` **数值相同**（因为 1023/8 截断后
`1023 - 127 = 896`，而 `(1024/8)*7 = 896`）。两条不同的公式在小数字上会分叉：
Abseil `3 → 3`、hashbrown `4 桶 → 3`；Abseil `7 → 7`、hashbrown `8 桶 → 7`——到这里都一样，
差别只在**容量本身的取值集合**上。

## 五、`SizeToCapacity`：`7/8` 的反函数不是 `8/7`

```cpp
int leading_zeros = absl::countl_zero(size + (size >= Group::kWidth / 2));
if (size < kMaxCapacityForLoadFactorOne) return (~size_t{}) >> leading_zeros;
size_t kLast3Bits = size_t{7} << (sizeof(size_t) * 8 - 3);
size_t max_size_for_next_capacity = kLast3Bits >> leading_zeros;
leading_zeros -= static_cast<int>(size > max_size_for_next_capacity);
return (~size_t{}) >> leading_zeros;
```

`(7 << 61) >> leading_zeros` 是 `(7/8) * (~0 >> leading_zeros)` 的等价变形——
**在移位之前就把 7/8 乘进去**，避免先算 `2^k` 再乘带来的溢出。
`+ (size >= kWidth/2)` 这一项是因为"超过半个组的表至少要留一个空槽"。
demo 对 `size = 1..199` 全量断言「结果是合法容量」且「`CapacityToGrowth(结果) >= size`」。

## 六、探测序列：两派的组起点完全一致

```cpp
// Abseil probe_seq
probe_seq(ProbeCapacity capacity, size_t hash) : capacity_(capacity.capacity), offset_(hash & capacity_) {}
void next() { index_ += Width; offset_ += index_; offset_ &= capacity_; }
```

```rust
// hashbrown ProbeSeq
fn move_next(&mut self, bucket_mask: usize) {
    self.stride = self.stride.wrapping_add(Group::WIDTH);
    self.pos = self.pos.wrapping_add(self.stride) & bucket_mask;
}
```

两者都是"步长每次 +`Width`"的三角推进，只是 Abseil 用 `index`、hashbrown 用 `stride`。
从 `h1 = 0`、掩码 127、`Width = 16` 起，两派得到同一个序列（demo 断言相等）：

```text
[0, 16, 48, 96, 32, 112, 80, 64]
```

手算一遍就明白：步长依次是 16、32、48、64、80、96、112，`(96 + 64) & 127 = 32` 这一跳
就是三角数在 `Z/(2^m)` 上是双射的直观体现。

## 七、选型结论

| 场景 | 该知道的 |
| --- | --- |
| 跨语言移植 swiss table | 先确认 EMPTY/DELETED 的字节值与容量形状（2^k−1 还是 2^k），这两处一定不同 |
| 极小的表（≤ 14 个元素） | 两派都会把容量抬到一个下限，且**不留/只留一个**空槽；此时"负载因子 7/8"并不适用 |
| 需要 sentinel 的迭代器 | Abseil 用 `kSentinel = 0xFF` 终止；hashbrown 靠"尾部复制 `WIDTH` 个控制字节" |
| 32 位平台 | hashbrown 的 `Tag::full` 位移量变成 `4*8-7 = 25`（`MIN_HASH_LEN` 取 `min(size_of::<usize>(), 8)`），tag 来源不同 |

## 参考资料（实际读过）

- `https://raw.githubusercontent.com/abseil/abseil-cpp/master/absl/container/internal/hashtable_control_bytes.h`
  （20 KB；`enum class ctrl_t : int8_t` 三个哨兵与六条 `static_assert`、
  `GroupSse2Impl::kWidth = 16`、generic `kWidth = 8`）
- `https://raw.githubusercontent.com/abseil/abseil-cpp/master/absl/container/internal/raw_hash_set.h`
  （176 KB；`IsValidCapacity`、`NormalizeCapacity`、`NextCapacity`、`PreviousCapacity`、
  `kMaxCapacityForLoadFactorOne`、`CapacityToGrowth`、`SizeToCapacity`、`H1`/`H2`、
  `probe_seq` 与 `probe_h1`、`HashtableCapacityStorageMode`）
- `https://raw.githubusercontent.com/rust-lang/hashbrown/main/src/control/tag.rs`
  （经 GitHub API `contents` 接口取；`Tag::EMPTY = 0xFF`、`Tag::DELETED = 0x80`、
  `is_full`/`is_special`/`special_is_empty`、`Tag::full` 的 `MIN_HASH_LEN` 处理）
- `https://raw.githubusercontent.com/rust-lang/hashbrown/main/src/raw.rs`
  （197 KB；`h1`、`capacity_to_buckets` 的下限表与注释里那张 3/7/14/28 的字节/元素表、
  `bucket_mask_to_capacity`、`ProbeSeq`、`fn probe_seq`）
- 目录结构经 `api.github.com/repos/rust-lang/hashbrown/contents/src` 确认：
  hashbrown 的 `src/raw/mod.rs` 已重构为 `src/raw.rs` + `src/control/{mod,tag,bitmask}.rs`
