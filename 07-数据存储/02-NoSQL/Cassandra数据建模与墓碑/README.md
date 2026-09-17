# Cassandra 数据建模与墓碑

用 188 + 260 行 Python 与 300 + 99 行 Go,把 Cassandra 的**查询驱动建模**与**墓碑生命周期**两条主线拆成可跑的最小模型:
前者回答"表怎么建",后者回答"删掉的数据什么时候真的消失"。

## 一、简介

Cassandra 是宽列(wide-column)分布式数据库,它的设计哲学与关系库**正好相反**:不是先设计范式化的表再想查询,
而是**先列出查询,再为每个查询建一张表**。官方文档原文:

> In Cassandra, data modeling is query-driven.

这条规则带来两个后果:① 反范式(同一份数据写进多张表)是常态,不是坏味道;
② 删除不是"移除字节",而是**写入一条带时间戳的墓碑(tombstone)**——官方原文:

> Cassandra treats a deletion as an insertion, and inserts a time-stamped deletion marker called a tombstone.

墓碑要等到宽限期(`gc_grace_seconds`,默认 **864000 秒 = 十天**)之后才可能被压缩清除,
而清除还要求"所有含该分区更旧数据的 SSTable 都参与同一次压缩"。于是"删了但没消失"成了
Cassandra 最经典的运维事故:**磁盘涨、读变慢、被删的数据又回来了**。

本 demo 把这两条线都做成可验证的模型,而不是文字描述。

## 二、原理详解

### 2.1 主键解析:谁决定数据落在哪个节点

`PRIMARY KEY` 的**第一个分量**(可以是 `(a, b)` 形式的复合分区键)被哈希,决定数据落在哪个节点;
**其余分量是聚簇键(clustering keys)**,决定分区内部的排序。官方原文:

> the first field or component of a primary key is hashed to generate the partition key
> and the remaining fields or components are the clustering keys

`parse_primary_key()` 实现这个拆分,含括号深度扫描与空白规范化:

```python
parse_primary_key("(id1,id2),c1,c2")   # → (["id1","id2"], ["c1","c2"])
parse_primary_key("( id1 , id2 ), c1") # → (["id1","id2"], ["c1"])  括号内空白被去掉
```

**分区键没有全部等值绑定**的查询,无法定位到单个分区,必须扫全集群或加 `ALLOW FILTERING`——
`Schema.needs_filtering(eq)` 就是这个判断。

### 2.2 分区规模:官方给的两把尺子

官方建模指引给出两条硬线(注意都写"below",即**恰好达线不算超**):

| 指标 | 阈值 | 代码常量 |
|---|---|---|
| 分区内值个数 | 100,000 | `MAX_PARTITION_VALUES` |
| 分区磁盘大小 | 100 MB | `MAX_PARTITION_BYTES` |

超过不是不能跑,而是会拖慢读修、压缩与流式传输。`PartitionStats.warnings()` 返回告警码
(`partition_values_over_100k` / `partition_disk_over_100mb`),便于在 CI 里当断言用。

### 2.3 墓碑的三条清除条件

墓碑被真正丢弃,官方要求**同时**满足:

1. 墓碑年龄已超过 `gc_grace_seconds`;
2. 本次压缩**覆盖了所有**含该分区更旧数据的 SSTable(否则那些数据可能"复活");
3. 若开启 `only_purge_repaired_tombstones`,该墓碑必须已跨副本修复过。

`purgeable()` 逐条返回失败原因,便于区分"还没到时间"和"这次压缩不够"——这两种情况的运维动作完全不同。

**僵尸复活(resurrection)的最小复现**在 `delete_with_tombstone` / `repair` / `purge_everywhere` 三个函数里:
三节点中一个节点宕机 → 删除只写进两个节点 → 该节点在宽限期内被**当作修复补数据回集群** →
墓碑与"比它更旧"的值谁赢,取决于墓碑是否已被另一条路径清掉。这正是"宽限期内不要轻易修复"的原因。

**fully expired SSTable**:整张 SSTable 只剩墓碑或已过 TTL 的数据时,官方允许直接丢弃整个文件。
但只要有别的 SSTable 里还留着该分区的数据,它就是被**阻塞(blocked)**的——
`sstableexpiredblockers()` 返回 `{expired 文件: [阻塞它的文件]}` 映射。

### 2.4 压缩策略:空间与写放大的三角权衡

| 策略 | 触发条件 | 特点 |
|---|---|---|
| **STCS**(SizeTiered) | 攒够 4 个"大小相近"的 SSTable,分桶区间 `[avg×0.5, avg×1.5]` | 写放大最低;空间放大最高(同一 key 散在多文件) |
| **LCS**(Leveled) | 每层是上一层的 **10 倍**;L0 超过 **32** 个 SSTable 时先在 L0 走一次 STCS | 读放大与空间放大最低;写放大高 |
| **TWCS**(TimeWindow) | 按时间窗口分桶,窗口结束后该窗口不再压缩 | 时序场景最优;**最怕乱序写入**——乱序数据会落在"已经关窗"的桶里,导致该桶被反复重写 |

`stcs_trigger()` 严格按官方的"分桶 + 阈值"两步做:先桶内不满 `min_threshold` 个就返回 `None`
(演示"4 个但大小悬殊 → 不触发"),再判是否够数。`lcs_l0_failsafe()` 返回 `stcs_in_l0` 或 `lcs`。

## 三、对比

| 维度 | Cassandra | 关系库(PostgreSQL) | MongoDB |
|---|---|---|---|
| 表设计起点 | 查询驱动,一个查询一张表 | 范式化,查询靠 JOIN | 文档内嵌 vs 引用 |
| 删除语义 | 写墓碑,延迟十天量级才真正消失 | 标记 dead tuple,`VACUUM` 回收 | 直接删除文档 |
| 分区/分片键 | 主键首分量强制决定 | 无强制(分区表可选) | 分片键可选 |
| 跨分区事务 | 仅 LWT,官方明确要求"最小化" | 完整事务 | 多文档事务(4.0+) |
| 空间回收 | 压缩 + 墓碑过期 | `VACUUM` | `compact` |

## 四、环境

- Python **3.10+**(用到 `X | None` 与 `dataclass`,无第三方依赖)
- Go **1.18+**(无第三方依赖)
- 本仓库在有 Go 工具链的机器上执行 `go run`;无工具链时用 `syntax_sanity.py` 做结构体检

## 五、运行方式

```bash
# Python 自检(68 条断言)
cd python && python cassandra_check.py

# Go 自检(同 68 条断言,输出格式一致)
cd go && go run cassandra_model.go cassandra_model_check.go
```

## 六、关键代码

```python
# 查询驱动:每个访问模式独立建表,而不是 JOIN
tables = design_tables("magazine", [
    AccessPattern("按 publisher 查", eq=["publisher"], order_by=["id"]),
    AccessPattern("按 category 查", eq=["category"], order_by=["published_at"]),
])
```

```python
# 墓碑清除的判定:三条硬条件,失败原因要能区分
ok, why = purgeable(tomb, now, "P1",
                    compacting=[old], all_sstables=[old, other],
                    repaired=True, only_purge_repaired=False,
                    gc_grace=DEFAULT_GC_GRACE_SECONDS)
# 宽限期内        → (False, "within_grace_period")
# 遮蔽文件没进本轮 → (False, "shadowing_sstable_outside_compaction: other")
# 三条都满足      → (True,  "purgeable")
```

## 七、性能边界

- 分区上限是**软约束**:超过后写入仍成功,但读修与压缩成本非线性上升。
- 墓碑的代价是**读路径**的:一次读要合并所有 SSTable 里该 key 的版本,墓碑越多合并越慢。
- `gc_grace_seconds` 是**修复窗口**:设短了,未被修复的节点会带着旧数据回来(复活);
  设长了,墓碑堆积(官方建议依赖常规修复而非调大该值)。
- STCS 的空间放大在最坏情况下接近"每个 key 一份";LCS 把空间放大压到 ~10% 但写放大约 10 倍。

## 八、注意事项与常见坑

1. **`PRIMARY KEY (id)` 与 `PRIMARY KEY ((id))` 都是单分区键**,但 `PRIMARY KEY (a, b)` 里
   `a` 是分区键、`b` 是聚簇键——解析器必须区分"顶层逗号"与"括号内的逗号"。
2. **TTL 过期 ≠ 墓碑**。过期的单元格靠 `ts + ttl <= now` 判定不可见,但它**没有墓碑**,
   所以 `ExpiredOnly` 要能识别"整文件只剩过期 TTL 数据"这一形态。
3. **同一分区的不同聚簇键互不影响**。墓碑只遮挡同分区同聚簇键下更早的版本,
   断言里专门留了一条来防这个"过度删除"的错解。
4. **`gc_grace` 边界是 `<=`**:`now - tomb_ts == gc_grace` 仍算宽限期内(官方用 "older than")。
5. **LWT 是性能陷阱不是特性**:官方原文要求 "should be kept to the minimum",
   `lwt_budget()` 对每张表的 LWT 操作计数并给出告警,防止把 Paxos 路径当普通写用。
6. **物化视图(materialized view)在 4.0 仍是实验特性**,`mv_risk()` 对它返回风险标记;
   生产上优先"应用层双写"。
7. Go 侧断言函数用**变参** `detail ...string`,避免固定参数个数写错导致编译失败
   (本仓库无 Go 工具链时的唯一人工拦截点)。

## 九、参考资料

- Apache Cassandra 文档源文件(trunk,与官网同源):
  - `doc/developing/data-modeling/intro.adoc` — 查询驱动原则、主键分量语义、分区规模两条线、LWT 最小化
  - `doc/managing/operating/compaction/tombstones.adoc` — 删除即插入、`gc_grace_seconds` 默认值、三条清除条件、三节点复活示例、fully expired SSTable 与阻塞
  - `doc/managing/operating/compaction/overview.adoc` / `stcs.adoc` / `lcs.adoc` / `twcs.adoc` — STCS 分桶与阈值 4、LCS 每层 10 倍与 L0 32 个的 STCS 兜底、TWCS 时间窗口与乱序风险
- `github.com/apache/cassandra`(blob 页精读,官方站 `cassandra.apache.org` 本机不可达)
