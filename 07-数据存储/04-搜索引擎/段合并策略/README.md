# Lucene 索引段合并与删除回收策略

## 简介

Apache Lucene 的"段（segment）"是其索引的基本数据单元——一个 segment 是一组**不可变**的文件集合，包含倒排表、正排表、doc-values、norms 等。新增文档写到新 segment，更新 = 新增 segment + 老 segment 的 bitset 标记删除。

这带来一个根本性的工程问题：

- **磁盘与内存压力**：被删除的文档 bitset 仍占着位置（内存里 norms、doc values 还活着）；
- **搜索吞吐**：每次查询都得扫描 deleted bitset，对热门词多余的开销；
- **段数膨胀**：不合并的话，每次 refresh 都来一个新段，几十万段会把句柄耗光、`fsync` 巨慢。

**段合并**就是定期把一组小段重写成大段，过程中真正抛弃 `del_docs`，回收磁盘与内存。Lucene 的合并策略决定了"何时合并哪些段"——本 demo 围绕 ES 5.0+ 默认的 **TieredMergePolicy** 展开。

- **关键概念**
  - **TieredMergePolicy**：把段按 size 分层（tier）；每个 tier 最多 `segments_per_tier` 个（默认 10）。优先合并评分低（=size 小 · 删除比低）的同 tier 段。
  - **`segments_per_tier`**：默认 10，控制每层段数上限；大值 → 写放大低但搜索段多；小值 → 反之。
  - **`max_merged_segment`**：默认 5 GB；超过此大小的段不再参与与其他段合并（一次性合并）。
  - **`floor_segment`**：默认 2 MB；小于此的段几乎总是触发合并——把"长尾小段"清掉。
  - **`reclaim_deletes_weight`**：默认 2.0；删除比高 → 评分上升 → 优先被合并。
  - **forceMerge / forcemerge**：手动触发"压到 1 个段"，仅适合不再写入的索引（如已冻结时间序列）。
- **历史背景**：Lucene 历史上有 LogByteSize / LogDoc / Tiered / NoMerge / Dummy 五种策略；TieredMergePolicy 是 Mike McCandless 在 2011 年提出，自 Lucene 4.x 引入后逐渐成为默认。

## 原理详解

### 1. segment 不可变性的代价

```
document 1 ──写入──▶ Segment A  (live)
                   ▲ 删除 ──▶ A 内部 bitset[D=1]  （占位仍在！）
                          ╲
                           ▼
document 1 替换 ──写入──▶ Segment B
```

- 删除 = 在 A 的 bitset 上标记 1；live docs 数减 1；
- 更新 = mark-delete in A + add to B；
- **空间浪费**直至下一次合并把 A 真正清理掉。

查询路径上 "Lucene 跳过 delete bitset 上的文档" 是有开销的：每段每次查询都要查这个 bitset。

### 2. TieredMergePolicy 评分

合并候选评分（**越低越优先合并**）：

```
score(seg) ∝ size_bytes · (1 + reclaim_deletes_weight · del_ratio)
```

直觉：

- `del_ratio = 0` → 评分 = size（"自然增长"路径）；
- `del_ratio = 0.5, weight = 2.0` → 评分 = 2 × size（被回收的好处多，被选中的优先级拉高）；
- `del_ratio = 1, weight = 2.0` → 评分 = 3 × size（极端，全删 → 浪费磁盘）。

### 3. Tier 与 segments_per_tier

`floor_segment = 2 MB` 是 tier 0 段大小上限；每个上层 tier 的上限是下层的 2 倍（直到 `max_merged_segment = 5 GB`）。

```
                       5 GB ─── max_merged_segment
                        ▲
                    ────┴────
                     2.5 GB      tier 9
                    ────────
                      ...
                    ────────
                    320 MB       tier 6
                    ────────
                     160 MB      tier 5
                    ────────
                      80 MB      tier 4
                    ────────
                      40 MB      tier 3
                    ────────
                      20 MB      tier 2
                    ────────
                      10 MB      tier 1
                    ────────
                       2 MB ─── floor_segment  ←── tier 0
```

每个 tier 内最多 `segments_per_tier = 10` 个段；超过则触发层内合并。

### 4. 合并流程

```
TIERED-MERGE-POLICY.findMerges(segments):
    # 按 tier 分组
    for each tier t with segs:
        # 评分选 < segments_per_tier 个最近的段
        merges[t] = top-M of segs(by score)
    # 回报给 MergeScheduler 的 findMerges()

MERGE-SCHEDLER:
    选下一个准备执行的合并 → IndexWriter.merge(...)
    rate-limit to 20 MB/s  (ES 默认, store.throttle.type=node)
    异步线程数 max_thread_count (默认 = max(1, core/2))
```

### 5. forceMerge(1) 与它何时该用

强制合并到 1 个段，**只能**用于"不再写入"的索引——典型场景：

- ILM 流程的 cold phase → `forcemerge` action → segment count 降到 1；
- 临时冻结的 time-series 数据；
- 数据准备重新索引后替换。

如果在写入仍在进行时 forceMerge，合并完会立刻被新一轮 refresh 出来的段打乱——付出写放大代价但得不到 segment 数稳定的好处。

### 6. 核心可配置项（Lucene + ES）

| 参数 | Lucene 默认 | ES index setting |
|------|-------------|------------------|
| `segments_per_tier` | 10 | `index.merge.policy.segments_per_tier` |
| `max_merged_segment` | 5 GB | `index.merge.policy.max_merged_segment` |
| `floor_segment` | 2 MB | `index.merge.policy.floor_segment` |
| `reclaim_deletes_weight` | 2.0 | `index.merge.policy.reclaim_deletes_weight` |
| `max_merge_at_once` | 10 | `index.merge.policy.max_merge_at_once` |
| `force_merge_deletions_pct_allowed` | 10% | `index.merge.policy.expunge_deletes_allowed`（forceMerge 时删除 > 该比视为可清除） |
| Merge scheduler | ConcurrentMergeScheduler | `index.merge.scheduler.max_thread_count` 默认 = `max(1, cpus/2)` |
| IO 节流 | — | `index.store.throttle.type` 默认 `node` (20 MB/s)，写量大时改 `none` 走 SSD |

## 对比 / 选型

| 维度 | **TieredMergePolicy（默认）** | LogByteSizeMergePolicy（Lucene 7-） | LogDocMergePolicy（已 deprecated） | NoMerge |
|------|--------------------------------|-------------------------------------|-----------------------------------|--------|
| 核心单位 | size + tier | size (log) | doc count | — |
| 处理小段 | ✅ floor_segment + tiered 兼顾 | 偏向大片，对小段友好度一般 | 偏向文档数 | ❌ |
| 删除回收 | reclaim_deletes_weight | partial | partial | ❌ |
| ES 默认 | 8.x+ 是 | 5.x 是 | 2.x 是（旧索引仍在） | 仅特殊场景 |

**实战选择**：

- 日志型索引（删除/更新频繁）→ 调小 `max_merged_segment`（如 2 GB）+ 适度调高 `reclaim_deletes_weight`（如 4.0）；
- 一次性写入流（logstash inputs）→ 默认即可；
- 全文检索为主的只读库 → `forceMerge` 到 1 个段，减少句柄、提高 hits/s。
- SSD + 不重 IO 的归档 → `throttle.type=none`。

## 环境准备

- Python ≥ 3.8（仅标准库 `dataclasses`）
- Go ≥ 1.20（仅标准库）

## 运行方式

```bash
# Python
python3 python/merge_demo.py

# Go
cd go && go run merge_demo.go
```

两个程序演示**完全相同的 4 段仿真**：

1. 一个 shard 内 8 个段（含 1 个 80% 删除的小段）；
2. TieredMergePolicy 的 score 计算与下一个候选；
3. forceMerge(1) 的语义；
4. 20 MB/s 节流与"日新增量"的关系。

## 关键代码片段

Python 版（`python/merge_demo.py`）合并评分：

```python
def merge_score(seg, reclaim_weight=2.0):
    """Lucene TieredMergePolicy 评分：score ∝ size · (1 + reclaim · del_ratio)"""
    return seg.size_bytes * (1.0 + reclaim_weight * seg.del_ratio)

# 删了 80% 的小段反而被优先合并（评分乘 1 + 2·0.8 = 2.6）
# 这就是为什么"删除文档不会无限堆积"
```

强制合并会真正回收 deleted 文档（合并时彻底从磁盘移除，不是仅 bitset）：

```python
def merge_one(segments):
    return Segment(
        size_bytes=sum(s.size_bytes for s in segments),
        num_docs=sum(s.live_docs for s in segments),  # ← 关键：del_docs 没了
    )
```

## 性能与边界

- **合并 IO 占磁盘**：默认 20 MB/s 在 HDD 上是安全值；在 NVMe + SSD 上可以提到 `none` 提速 5-10×。
- **写放大**：合并 N 个 segment 会重写每个文档；持续高写入会触发 Lucene 的 `now throttling indexing` 警告（限流 1 个写入线程）。
- **删除比门槛**：`reclaim_deletes_weight = 2.0` 让删除比达到 50% 的段优先于无删除段的合并；上线时建议 1.0-4.0 区间试。

## 注意事项与常见坑

1. **大量小段堆在索引里** → 现象：`_cat/segments?v` 显示 segment count 几百甚至几千。原因：合并跟不上写入，或 `floor_segment`/disk throttle 太小。规避：调小 `max_merged_segment`、换 SSD、改 `store.throttle.type`。
2. **合并一直不释放空间** → 现象：删除大量 doc 后磁盘没变小。原因：合并没被触发（删除比阈值不够、合并 IO 被限）。规避：调高 `reclaim_deletes_weight`、或对不再写入的索引 `forceMerge`。
3. **写入量大于合并 IO 能力** → 现象：ES 日志 `now throttling indexing`。原因：节流 +1 已生效。原因 2 选 1：扩 IO（throttle.type=none）、或加节点。
4. **对写入中的索引 forceMerge** → 收益被后续 refresh 抵消；并且 forceMerge 是单线程大 IO，可能抢写入并发。规避：用 ILM 流程在 cold phase 才发起。
5. **segments_per_tier = 1 的诱惑** → 把 tier 压平到 1 = 几乎每次都是全合并 → 写放大 100%+。生产不建议。
6. **`expunge_deletes_allowed`**：forceMerge 时，segment 删除比 ≤ 该阈值的，直接丢弃，不参与合并。默认 10% 太激进，官方说"dangerous to increase too much"——上调会让 forceMerge 处理删除更激进，但也可能保留未受控的 segment 数。

## 参考资料（实际阅读过的权威来源）

- [Lucene's Handling of Deleted Documents | Elastic Blog](https://www.elastic.co/blog/lucenes-handling-of-deleted-documents) — 删除 bitset 的语义、`reclaim_deletes_weight` + 50% 阈值、官方 max_merged_segment 默认 5 GB 实验
- [Performance Considerations for Elasticsearch Indexing | Elastic Blog](https://www.elastic.co/blog/performance-considerations-elasticsearch-indexing) — 合并与索引吞吐的 IO 制约、20 MB/s 节流由来、`now throttling indexing` 触发条件
- [Apache Lucene 10 release highlights | Elastic Search Labs Blog](https://www.elastic.co/search-labs/blog/apache-lucene-10-release-highlights) — Lucene 10 的 logical partitioning、forceMerge 在并行性上的新姿势
- [Data stream lifecycle settings | Elasticsearch 8.x Reference](https://www.elastic.co/guide/en/elasticsearch/reference/8.19/data-stream-lifecycle-settings.html) — data stream lifecycle target merge policy/floor_segment 默认 16 / 100 MB
- [TieredMergePolicy (Lucene source)](https://github.com/apache/lucene/blob/main/lucene/core/src/java/org/apache/lucene/index/TieredMergePolicy.java) — Apache Lucene 主分支源码（findMerges、setSegmentsPerTier 完整实装）
