# 声明式分区与分区裁剪

## 简介

分区表的价值不在"存得下",而在**用不上就不用看**。`WHERE logdate >= '2008-01-01'`
在一张 5 分区的表上,理想情况只该扫 1 个分区 —— 这就是**分区裁剪(partition pruning)**。

但裁剪有**三个不同的时机**,而其中两个在 `EXPLAIN` 里**看不出痕迹**:

- 计划期裁掉的:根本不出现在计划里;
- 执行期初始化裁掉的:只在输出里留一行 `Subplans Removed: N`(**但仍会加锁**);
- 执行期逐次裁掉的:要靠 `EXPLAIN ANALYZE` 的 `loops` 差异才能发现,完全没跑的子计划
  显示 `(never executed)`。

本 demo 把这套机制做成可执行模型,并把三种分区方式(RANGE / LIST / HASH)在裁剪能力上的
**硬差异**摆到台面上:HASH 分区等值查询能定位,但**范围查询一个都裁不掉**。

## 原理详解

### 1. 边界语义(文档 5.12)

| 方式 | 定义 | 边界 |
| --- | --- | --- |
| RANGE | 键的区间,分区间不重叠 | **含下界、不含上界** |
| LIST | 显式列出键值 | 可配 DEFAULT 兜底 |
| HASH | modulus + remainder | `hash(key) % modulus == remainder` |

文档原话:

> if one partition's range is from 1 to 10, and the next one's range is from 10 to 20,
> then **value 10 belongs to the second partition not the first**.

`BETWEEN 1 AND 10` 是闭区间,所以它会**同时**命中这两个分区 —— 这条在 demo 里有断言。

### 2. 裁剪由"分区键的隐式约束"驱动,与索引无关

> partition pruning is driven only by the constraints defined implicitly by the partition
> keys, **not by the presence of indexes**. Therefore it isn't necessary to define indexes
> on the key columns.

是否建索引取决于"扫描这个分区时是要扫一大部分还是一小部分",与裁剪无关。

### 3. 三个裁剪阶段

| 阶段 | 用得上什么值 | 在 EXPLAIN 里的痕迹 |
| --- | --- | --- |
| 计划期 | 字面量 | 被裁的分区**不出现** |
| 执行期初始化 | `PREPARE` 参数、子查询值 | `Subplans Removed: N` |
| 执行期逐次 | 嵌套循环参数(每次变化) | `loops` 差异 / `(never executed)` |

两个坑:

1. **初始化期裁掉的分区仍然会在执行开始时被加锁** —— 裁剪省的是扫描,不省锁。
2. 执行期裁剪**每次参数变化都要重算一遍**,所以不同子计划的 `loops` 会不一样。

`enable_partition_pruning = off` 会同时关掉这三个阶段(退化为全分区扫描)。

### 4. DEFAULT 分区是裁剪的"天花板"

只要存在 DEFAULT 分区,任何基于"值属于某集合"的推理都**无法排除它** —— 因为 DEFAULT
意味着"可能是任何值"。demo 里 `city = 'BJ'` 的裁剪结果是 `east + other`,而不是只有
`east`。这也是文档建议在 `ATTACH PARTITION` 时给 DEFAULT 分区加互斥 CHECK 约束的原因
(否则每次挂载都要全扫 DEFAULT,`ACCESS EXCLUSIVE` 锁)。

### 5. `partprune.c` 里的匹配状态

```c
typedef enum PartClauseMatchStatus {
    PARTCLAUSE_NOMATCH, PARTCLAUSE_MATCH_CLAUSE, PARTCLAUSE_MATCH_NULLNESS,
    PARTCLAUSE_MATCH_STEPS, PARTCLAUSE_MATCH_CONTRADICT, PARTCLAUSE_UNSUPPORTED,
} PartClauseMatchStatus;

typedef enum PartClauseTarget {   /* 裁剪阶段 */
    PARTTARGET_PLANNER, PARTTARGET_INITIAL, PARTTARGET_EXEC,
} PartClauseTarget;
```

`MATCH_CONTRADICT` 是最好用的一种:`x > 15 AND x < 5` 直接把**所有分区**裁掉。

## 对比:三种分区方式的裁剪能力

| 查询 | RANGE | LIST | HASH |
| --- | --- | --- | --- |
| `=` | 1 个 | 1 个(+DEFAULT) | 1 个 |
| `BETWEEN` / 范围 | 少数几个 | 视键集合 | **全部(裁不掉)** |
| `IS NULL` | 无下界那个 | 显式含 NULL 的 / DEFAULT | 按散列值 |
| 数据分布 | 可能倾斜 | 手动控制 | 天然均匀 |

## 环境与运行

- Python 3(仅标准库)、Go 1.22(仅标准库)

```bash
python python/main.py
python python/selfcheck_prune.py   # 52 项断言
cd go && go run .
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/bounds.py` | `RangePartition` / `ListPartition` / `HashPartition`、`prune`(算子 → 命中分区)、`combine_and` / `combine_or` / `detect_contradiction` |
| `python/execmodel.py` | `PrunePlan`:`planner_prune` / `initial_prune` / `exec_prune` / `render` |
| `go/bounds.go` `go/execmodel.go` `go/main.go` | 同一模型的 Go 转写 |

## 性能边界与注意事项

- **本机不跑 PostgreSQL**:本 demo 是**裁剪判定模型**,不含真实的代价估算与计划选择。
- **分区数不是越多越好**:文档说规划器一般能应付"几千个分区",前提是**典型查询能裁到
  只剩少数几个**;裁完剩下的越多,规划时间与内存开销越大。
- **闭区间转半开是本模型的口径**:`BETWEEN a AND b` 按 `[a, b+1)` 处理,这只对**离散可比较**
  的键(整数、日期)严格成立;连续域上是近似。真实规划器用的是各类型自己的比较函数。
- **HASH 分区别指望范围裁剪**:等值点查很合适,范围扫描会退化成全分区。
- **更新分区键会搬行**:文档明确写了"Updating the partition key of a row will cause it to
  be moved into a different partition",这是一次 DELETE + INSERT,代价不低。
- **分区表上建索引不能用 `CONCURRENTLY`**:会长时间持锁;文档建议先在父表上
  `CREATE INDEX ON ONLY`(标为 invalid),再逐分区建。
- **唯一约束必须包含分区键**:这是选择分区键时最硬的约束之一。

## 参考资料

- PostgreSQL 18 官方文档《Table Partitioning》(5.12),尤其是 5.12.4 Partition Pruning
  与 5.12.6 Best Practices —— <https://www.postgresql.org/docs/current/ddl-partitioning.html>
- PostgreSQL 源码 `src/backend/partitioning/partprune.c`
  (`PartClauseMatchStatus`、`PartClauseTarget`、`PartClauseInfo`)
  —— 经 `cdn.jsdelivr.net/gh/postgres/postgres@master/...` 实读
- PostgreSQL 18 官方文档 GUC:`enable_partition_pruning`(默认 on)、
  `enable_partitionwise_join`、`enable_partitionwise_aggregate`(均默认 off)
  —— <https://www.postgresql.org/docs/current/runtime-config-query.html>
