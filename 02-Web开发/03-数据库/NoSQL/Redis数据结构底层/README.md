# Redis 数据结构底层

## 简介

- Redis 的"快"来自为每种数据规模选一套**内存布局**：小集合用紧凑的 listpack / intset，越过阈值就自动转成 hashtable / skiplist，并靠**双表渐进式 rehash** 把扩容开销摊到每次操作里。
- 关键概念：
  - **有序集合 = 字典 + 跳表**：字典给 O(1) 的分数查询，跳表给 O(log N) 的范围操作；
  - **渐进式 rehash**：扩容不是一次性搬完，而是"每次查找/插入顺带搬一个桶"，用两张表过渡；
  - **跳表层数几何分布**：每层晋升概率 P = 1/4，期望层数 1.33，上限 32 层；
  - **span（跨度）**：每个指针记录跨过多少元素，使 `ZRANK`/`ZRANGE` 也能 O(log N)；
  - **紧凑编码阈值**：`zset-max-listpack-entries 128` / `-value 64` 等，越界即转换。
- 历史背景：跳表由 William Pugh 在 *Skip Lists: A Probabilistic Alternative to Balanced Trees* 提出；Redis 的实现在源码注释里明确写了相对论文的三处修改 —— 允许重复 score、比较键是 (score, 卫星数据)、level 0 带 back 指针。

## 原理详解

### 一、字典：两张表 + 渐进式 rehash

1. **两张表**：`d->ht_table[0/1]`、`d->ht_size_exp[0/1]`、`d->ht_used[0/1]`，外加 `d->rehashidx`（-1 = 没有在 rehash）。
2. **容量永远是 2 的幂**（`_dictNextExp()`），索引 = `hash & (size - 1)`；碰撞用链地址法。
3. **触发扩容**（`dictExpandIfNeeded()`）：元素/桶达到 **1:1** 且允许 resize 时扩容；即使 resize 被"回避"，达到 `dict_force_resize_ratio`（源码内 **= 4**）仍会强制扩容。
4. **触发收缩**（`dictShrinkIfNeeded()`）：低于 **1:8**（`HASHTABLE_MIN_FILL`）收缩；回避状态下降级为 **1:32**。
5. **渐进式 rehash**：`_dictResize()` 注释写明 "Prepare a second hash table for incremental rehashing"；`dictRehash(d, n)` 一步 = 搬一个桶，但**最多探望 n×10 个空桶**（否则耗时无上界），全部搬完后 `ht_used[0] == 0` → 把 ht[1] 拷回 ht[0]、`rehashidx = -1`。
6. **谁来推进**：`_dictRehashStep()` 由查找/更新操作触发（"so that the hash table automatically migrates from H1 to H2 while it is actively used"）；unstable 分支还有 `_dictRehashStepIfNeeded(d, idx)`，优先搬"本次访问到的那个桶"以获得更好的缓存局部性。
7. **rehash 期间的读**：查找遍历**两张表**，并 `if (table == 0 && idx < rehashidx) continue;` 跳过已经迁移的区间；插入一律进**新表**（`dictFindLinkForInsert` 注释：the bucket is always returned in the context of the second (new) hash table）。
8. **迭代**：安全迭代器调用 `dictPauseRehashing()` 冻结搬运，避免边遍历边搬造成漏读/重复读。

### 二、跳表：span 记账与 ZRANGE

1. **层数**：`level = 1; while (random() < 0.25) level++;` 之后截断到 32 —— 期望 1/(1−0.25) = **1.33** 层/节点，比平衡树省指针。
2. **插入**（`zslInsertNode()`）：用 `update[32]` 记每层前驱、`rank[32]` 记 0-based 排名，然后按源码公式改 span：
   - 新节点在第 i 层 `span = update[i].span − (rank[0] − rank[i])`；
   - `update[i]` 的新 `span = (rank[0] − rank[i]) + 1`；
   - 新节点没覆盖到的更高层，`span` 各 +1。
3. **排名**：`zslGetRank()` 沿 span 累加得到 **1-based** rank（"due to the span of zsl->header to the first element"）；`zslGetElementByRank()` 是其逆运算，两者互逆。
4. **删除**（`zslUnlinkNode()`）：逐层 `incr/decr span`，并在顶层为空时把 `zsl->level` 降下来。
5. **反向遍历**：backward 指针只在 level 0，形成"仅第 1 层的双向链表"，这正是 `ZREVRANGE` 不需要从头再走一遍的原因。
6. **重复 score**：只按 score 比较会让同分行永远定不到游标，所以比较键是 `(score, ele)`，ele 兼作 tie-breaker。

### 三、紧凑编码阈值（Redis ≥ 7.0）

| 类型 | 指令 | 默认值 | 越界后的编码 |
| --- | --- | --- | --- |
| Hash | `hash-max-listpack-entries` / `-value` | 512 / 64 | hashtable |
| ZSet | `zset-max-listpack-entries` / `-value` | 128 / 64 | skiplist |
| Set（纯整数） | `set-max-intset-entries` | 512 | listpack → hashtable |
| Set（7.2+） | `set-max-listpack-entries` / `-value` | 128 / 64 | hashtable |

官方说明："If a specially encoded value overflows the configured max size, Redis will automatically convert it into normal encoding."（转换对 API 完全透明）

## 环境准备

- Python 3.9+（仅标准库）；Go 1.18+（可选对照）。demo 不依赖 redis-server。

## 运行方式

### Python

```bash
cd python
python3 dict_rehash.py     # 字典演示
python3 skiplist.py        # 跳表演示
python3 checks.py          # 47 条断言（含编码阈值）
```

### Go

```bash
cd go && go run .          # 三个文件同属 package main
```

## 关键代码片段

```python
# 渐进式 rehash：一步搬一个桶，最多探望 n*10 个空桶
while n > 0 and self.used[0] != 0:
    while self.rehashidx < self.size(0) and not self.ht[0][self.rehashidx]:
        self.rehashidx += 1
        empty_visits -= 1
        if empty_visits == 0:
            return True
    ...
return not self._check_completed()        # used[0] == 0 → ht[1] 拷回 ht[0]
```

```python
# 跳表插入：span 记账（与 zslInsertNode 同构）
node.span[i] = update[i].span[i] - (rank[0] - rank[i])
update[i].span[i] = (rank[0] - rank[i]) + 1
```

```go
// 查找同时看两张表，跳过已迁移区间
for table := 0; table < tables; table++ {
	idx := d.index(key, table)
	if table == 0 && idx < d.rehashidx {
		continue
	}
	...
}
```

## 性能与边界

- 字典：平均 O(1) 查找/插入；负载因子在 **1:1 扩容、1:8 收缩**之间摆动，因此内存会在"刚好够用"与"翻倍"之间台阶式变化。
- 渐进式 rehash 的单次代价上界由 `n*10` 个空桶决定 —— 这是"操作延迟不出现长尾"的硬保证。
- 跳表：插入/删除/按分定位 O(log N)；`ZRANK` 依赖 span，仍是 O(log N)（若无 span 则退化为 O(N)）。
- 空间：每节点平均 1.33 个 forward 指针；本 demo 500 节点实测 1.27。compact 编码（listpack）在阈值内可省下约 5 倍内存（官方口径："up to 10 times less memory ... 5 times less memory used being the average saving"）。
- 阈值是**一次性判决**：listpack 越界会转成 skiplist/hashtable，且**不会**因为后来元素变少而自动转回（除显式转换路径）。

## 注意事项与常见坑

- **把 rehash 当成"后台线程"**：它由正常读写驱动；`pauserehash` 期间（例如迭代中）搬运完全停止，大字典可能长时间维持"两张表"的内存占用。
- **遍历时不能搬桶**：安全迭代器会冻结 rehash，普通迭代器则要求使用者自己保证；这也是 demo 里 `keys_safe()` 要成对增减 `pauserehash` 的原因。
- **收缩阈值与扩容阈值不对称**：1:1 扩、1:8 缩，避免在阈值附近来回抖动（同一思路见哈希表的负载因子抖动问题）。
- **只看 score 做游标**：`ZRANGEBYSCORE` 场景里同分元素很多时，缺 tie-breaker 的实现会反复返回同一批同行（Python 版 `keyset_page_by_score_only` 复现了这个退化）。
- **把「元素数」当成唯一阈值**：单元素长度也是条件（`-value 64`），一个 100 字节的成员会让 20 个元素的小 zset 直接走 skiplist。
- **不要照抄常量出处**：`DICT_HT_INITIAL_SIZE` / `HASHTABLE_MIN_FILL` 的数值定义在 `dict.h`，本 demo 未读取该文件，故取 4 / 8 复现 1:8 与 1:32 的语义，并在代码注释中标注 —— 引用时请以源码为准。

## 参考资料（实际阅读过的权威来源）

- [redis/redis — src/dict.c（unstable 分支）](https://raw.githubusercontent.com/redis/redis/unstable/src/dict.c) — 双表结构、`_dictResize`/`dictExpandIfNeeded`/`dictShrinkIfNeeded` 判定、`dict_force_resize_ratio = 4`、`dictRehash` 的 n*10 空桶上界、`dictCheckRehashingCompleted` 的表切换、`_dictRehashStep(IfNeeded)`、查找跳过已迁移桶、插入进 ht[1]、`dictNext` 的 pause 语义。
- [redis/redis — src/t_zset.c（unstable 分支）](https://raw.githubusercontent.com/redis/redis/unstable/src/t_zset.c) — zset 头注释（字典 + 跳表、共享 SDS、对 Pugh 论文的三处修改）、`zslRandomLevel`、`zslInsertNode` 的 update/rank 与 span 公式、`zslGetRank`/`zslGetElementByRank`、`zslUnlinkNode` 的层数回落、listpack↔skiplist 转换路径。
- [redis/redis — src/server.h（unstable 分支）](https://raw.githubusercontent.com/redis/redis/unstable/src/server.h) — `ZSKIPLIST_MAXLEVEL 32` / `ZSKIPLIST_P 0.25` / `ZSKIPLIST_MAX_SEARCH 10`，以及 zskiplistNode/zskiplist/zset 的字段布局。
- [Redis 官方文档 — Memory optimization](https://redis.io/docs/staging/DOC-5680/operate/oss_and_stack/management/optimization/memory-optimization) — 紧凑编码阈值默认值（hash 512/64、zset 128/64、intset 512、7.2+ 的 set-listpack 128/64）、越界自动转换、内存节省量级。
