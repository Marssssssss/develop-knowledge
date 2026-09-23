# 数据结构与集合选型

> 不是「背算法题」，而是回答：**这个场景该用哪个容器，代价到底是什么**。
> 渐近复杂度只是第一层，常数因子、内存布局、摊销尖峰、并发语义往往才是线上差异的来源。

## 核心研究主题

- **选型代价模型**：数组（连续内存、随机访问 O(1)、中间插入 O(n)）、链表（插入 O(1) 但缓存不友好）、
  哈希（平均 O(1) 但无序、有 rehash 尖峰）、平衡树/跳表（O(log n) 且有序）、堆（只保证极值）
- **缓存局部性与内存布局**：指针追逐 vs 连续内存、AoS vs SoA、一次 cache line 能装几个元素；
  为什么 1e5 元素的链表遍历可能比数组慢一个数量级
- **摊销复杂度**：`vector` 倍增扩容、`HashMap` rehash 与树化的瞬时尖峰；摊销 O(1) 在延迟敏感路径上不可用
- **有序 vs 无序**：需要区间查询/前驱后继时才值得为 O(log n) 付费
- **不可变与持久化数据结构**：写时复制、结构共享；什么时候不可变反而更快（避免深拷贝）
- **并发容器**：锁分段、CAS 无锁队列、读写锁与 copy-on-write 读多写少场景
- **标准库实现差异**：Python `dict`/`list`、Go `map`/`slice`、Java `HashMap`（树化阈值与负载因子）、
  C++ `std::vector` 增长因子——同名的容器行为并不相同

## 已完成 demo（首批 5 个，2026-09-24）

| # | demo | 关键结论 |
| --- | --- | --- |
| 660 | [CPythonDict紧凑布局与开放寻址](./CPythonDict紧凑布局与开放寻址/) | 紧凑布局的 `dk_indices`/`dk_entries` 分离、`USABLE_FRACTION`、`GROWTH_RATE` 与 `calculate_log2_keysize`、perturb 探测的真实下标序列、删除不回增 `dk_usable` |
| 661 | [JavaHashMap树化与扩容拆分](./JavaHashMap树化与扩容拆分/) | `tableSizeFor` 的 `-1 >>> nlz(cap-1)`、`hash = h ^ (h>>>16)`、`resize()` 三分支、`treeifyBin` 两道门（8 与 64）、`TreeNode.split` 三种结局 |
| 662 | [GoMap可扩展散列与SwissTable](./GoMap可扩展散列与SwissTable/) | Go 1.24+ `internal/runtime/maps`、`maxTableCapacity=1024`、`maxAvgGroupLoad=7`、`h1 = h>>7`、三角探测 `probeSeq`、rehash→grow/split、`pruneTombstones` 10% 门槛 |
| 663 | [SwissTable控制字节与容量映射](./SwissTable控制字节与容量映射/) | Abseil（`kEmpty=-128`/`kDeleted=-2`、容量 `2^k−1`、`H2 = hash>>57`）vs hashbrown（`EMPTY=0xFF`/`DELETED=0x80`、桶数 `2^k`）两派对照 |
| 664 | [动态数组增长因子实读取证](./动态数组增长因子实读取证/) | 六家增长公式源码级对比、CPython 复现官方注释序列、旧块复用判据 `r^i(2−r) >= 1` 与黄金比例 φ |

## 待研究

- [ ] 建立可比对的代价模型：同一操作在 1e3 / 1e6 量级下，常数与 cache miss 如何盖过渐近复杂度
- [x] `vector` 倍增增长 vs 1.5 倍增长的内存碎片与搬移成本差异（各标准库实际取值的**实读取证**）→ 见 664
- [x] Java `HashMap` 树化阈值与退化阈值（扩容后从树退回链表）的实际数值与动机 → 见 661
- [ ] C++ `std::unordered_map` 的 max_load_factor 与 bucket_count 增长序列（libstdc++ 素数表 vs libc++ 2 的幂）
- [ ] 有序容器选型：B-tree 作为 `map` 底层（Rust `BTreeMap`、Go 1.24 `maps` 之外的第三方）的缓存优势
- [ ] 跳表 vs 平衡树的常数因子实测（同数据量下指针追逐次数的可比口径）
- [ ] 跳表 vs 平衡树：为何工业实现（Redis zskiplist、LevelDB）偏爱跳表
- [ ] 布隆过滤器的假阳率公式与位数组 sizing；何时该换成 cuckoo filter（支持删除）
- [ ] 持久化数据结构的结构共享在 GC 语言 vs 手动内存语言下的收益差
- [ ] 并发队列的 false sharing 与 padding 代价
