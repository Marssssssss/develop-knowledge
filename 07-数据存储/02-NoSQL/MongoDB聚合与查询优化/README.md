# MongoDB 聚合管道与查询优化 — 优化器重排/合并 + ESR 索引选择

> 权威来源(全部实际读过):
> - [MongoDB manual: Aggregation Pipeline](https://www.mongodb.com/docs/manual/core/aggregation-pipeline/) — 阶段语义、`$out`/`$merge`/`$geoNear` 唯一性约束
> - [MongoDB manual: Aggregation Pipeline Optimization](https://www.mongodb.com/docs/manual/core/aggregation-pipeline-optimization/) — R1~R9 全部官方示例原文
> - [MongoDB manual: Aggregation Pipeline Limits](https://www.mongodb.com/docs/manual/core/aggregation-pipeline-limits/) — 1000 阶段、100 MB、16 MiB、`allowDiskUseByDefault`
> - [MongoDB manual: The ESR (Equality, Sort, Range) Guideline](https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-guideline/) — 等值/排序/范围排布、`$in` 的 201 分界
> - 本地全文抓取(mongodb.com 官方页):`aggregation-pipeline-limits` 原文经 `curl` 下载后提取

## 简介

MongoDB 的聚合管道是**阶段序列**,每条文档依次流过;但真正执行的管道不是你写的那条 —— 优化器会先**重排**(把过滤器前移到能命中索引的位置)再**合并**(把相邻阶段折叠成一个),官方称后者为 coalescence。本 demo 用可运行的模型复现这套重写规则与索引选择,并用官方文档里给出的每个示例逐条断言。

关键概念(每条 1 句):
- **阶段流水线**:`$match` 过滤、`$group` 聚合、`$project` 重塑;同一阶段可重复出现,例外是 `$out` / `$merge` / `$geoNear`
- **阻塞型阶段**:必须读完所有输入才能输出第一条(`$group` `$sort` `$bucket` `$bucketAuto` `$setWindowFields` `$sortByCount`),因此受 100 MB 内存限制
- **投影下推(R1)**:不依赖投影计算值的 `$match` 过滤器被前移,常因此获得可用索引
- **coalescence**:`$sort+$limit` 合到 `limit` 进 `$sort`(`$skip` 会把 limit 抬高)、`$match+$match` 合成 `$and`、`$lookup+$unwind(+$match)` 合进 `$lookup`
- **ESR 规则**:复合索引里等值字段必须最先,然后是排序字段,最后是范围字段 —— 反之(ERS)会引入内存排序
- **`$in` 的 201 分界**:少于 201 个元素按等值谓词处理,达到 201 个就退化为范围谓词,其后的索引字段不能再提供排序

## 原理详解

### 1. 优化顺序:先重排、后合并

官方原文:"When possible, the optimization phase coalesces a pipeline stage into its predecessor. Generally, coalescence occurs *after* any sequence reordering optimization." —— 顺序不能颠倒:先把 `$match` 挪到 `$sort` 前,才可能出现新的相邻对(例如 `$match`+`$match`)被合并。

### 2. R1 投影下推:依赖分析是核心

官方示例(输入):

```text
{$addFields: {maxTime: {$max: "$times"}, minTime: {$min: "$times"}}}
{$project: {_id:1, name:1, times:1, maxTime:1, minTime:1, avgTime: {$avg:["$maxTime","$minTime"]}}}
{$match: {name:"Joe Schmoe", maxTime:{$lt:20}, minTime:{$gt:5}, avgTime:{$gt:7}}}
```

优化器把 `$match` 拆成 4 个单键过滤器,各自向左穿过"不影响它"的投影阶段:`name` 穿到最前(两个投影都不影响它)、`maxTime`/`minTime` 只能穿到 `$project` 之前(被 `$addFields` 计算,不能越过它)、`avgTime` 依赖 `$project` 的计算结果,原地不动:

```text
{$match: {name:"Joe Schmoe"}}
{$addFields: {...}}
{$match: {maxTime:{$lt:20}, minTime:{$gt:5}}}
{$project: {...}}
{$match: {avgTime:{$gt:7}}}
```

额外收益:前移后的 `{name}` 位于管道首阶段,聚合于是能用 `name` 上的索引。

### 3. coalescence 五条

| 规则 | 触发 | 结果 |
| --- | --- | --- |
| R5 | `$sort` 后（可夹中性阶段）出现 `$limit` | `limit` 并入 `$sort`；夹 `$skip` 时 `limit += skip` |
| R6 | `$limit` 紧接 `$limit` | 取两者**较小值** |
| R7 | `$skip` 紧接 `$skip` | 取两者**之和** |
| R8 | `$match` 紧接 `$match` | 用 `$and` 合并成一个 |
| R9 | `$lookup` 紧接作用于其 `as` 字段的 `$unwind` | `$unwind` 折进 `$lookup`，长出 `pipeline`/`unwinding` 两个内部字段 |

R5 的例外是"中间有改变文档数的阶段"(`$unwind`/`$group`),此时不能合并 —— 本 demo 对 `$group` 夹在中间的情况有专门断言。

### 4. ESR:为什么等值必须最先

索引键序决定"能用的界"。等值前缀之后的第一个字段才可能承担排序;一旦遇到范围字段,它之后的键就再也不能提供界。官方 movies 示例:

```text
查询: {directors:"David Lynch", runtime:{$lt:130}}  排序: {year:1}
ESR 最优索引: {directors:1, year:1, runtime:1}   ← 等值 → 排序 → 范围,免内存排序
ERS 排布:     {directors:1, runtime:1, year:1}   ← 范围在排序前,允许内存排序但扫描更少
```

官方对 ERS 的适用条件写得很明确:"If your range predicate in the query is very selective, then put it before sort fields (ERS)" —— 两者不是对错,是取舍。

## 环境

- Python 3.13(纯标准库,无第三方依赖)
- Go 1.18+（用到 `any`）；本机无 Go 工具链，Go 版走人工审查 + `_docs/tools/syntax_sanity.py` 结构检查
- 无需 MongoDB 实例:本 demo 是优化器/计划器的**模型**,不连数据库

## 运行方式

```bash
cd python && python3 mongo_pipeline_check.py      # 67 条断言
go run ../go/*.go                                 # 有 Go 工具链时
```

## 关键代码

```python
# 每个过滤器向左穿过"不影响它"的连续投影阶段
for _label, fspec in split_match_filters(stages[i].spec):
    fset = filter_fields(fspec)
    pos = i
    for idx in range(i - 1, run_start - 1, -1):
        if any(effs[idx].affects(f) for f in fset):
            break
        pos = idx
    (groups if pos < i else stay).append(fspec)
```

```python
# ESR:等值前缀 → 排序(方向可全反) → 范围
while pos < len(names) and kinds.get(names[pos]) == "equality":
    eq_fields.append(names[pos]); pos += 1
rest = names[pos:]
sort_by_index = len(rest) >= len(sf) and rest[:len(sf)] == sf and dir_ok
```

## 性能边界

- **R5 让排序只保留前 n 条**:官方原文 "this allows the sort operation to only maintain the top `n` results ... MongoDB only needs to store `n` items in memory";即使 `allowDiskUse: true` 且 n 超过内存限制,该优化仍然适用
- **阻塞型阶段的上限是 100 MB**:6.0 起由 `allowDiskUseByDefault` 决定是落盘还是直接报错;`$search` 因在独立进程运行不受此限
- **`$sort` 想用索引就不能被 `$project`/`$unwind`/`$group` 挡在前面**;`$match` 想用索引必须是(优化后的)首阶段
- **`DISTINCT_SCAN` 比 `IXSCAN` 快的前提**:同一个索引键值对应多个文档,官方建议在 `explain` 里直接找 `IXSCAN`/`DISTINCT_SCAN` 判断是否走了索引

## 注意事项与常见坑

- **`$unset` 也是"投影阶段",但把它当 `$project` 处理会错**:`{$match:{x:5}}` 前移到 `$unset:[x]` 之前,语义从"匹配不到任何文档"变成"匹配 x==5 的文档"。本 demo 采取保守口径(不跨越 `$unset` 删除的字段),官方未给出该情形的判定细节
- **R3(`$redact`+`$match`)官方只说 "sometimes"**,未给完整判定条件;本 demo 以官方示例的可观察行为反推(只前移纯等值过滤器),并在代码注释里标注为自定口径
- **`$in` 的行为变更是版本相关风险**:官方明确 "The `$in` behavior change at 201 array elements is not guaranteed to stay the same for all MongoDB versions"
- **优化规则随版本变化**:官方原文 "Optimizations are subject to change between releases." —— 不要把手写的"最优管道"当成跨版本常量
- **`explain` 里的优化后管道不可手动执行**:官方注 "The optimized pipeline is not intended to be run manually"
- **`$out`/`$merge` 只能在末位、`$geoNear` 只能在首位**,且这三者不可重复出现
- **`$sort`+`$skip`+`$limit` 的合并结果里 `$skip` 仍留在原位**,只是 `$sort` 的 `limit` 被抬高 —— 两者缺一不可,漏掉 `$skip` 会多返回文档

## 参考资料

- [Aggregation Pipeline](https://www.mongodb.com/docs/manual/core/aggregation-pipeline/)（阶段语义与三阶段唯一性约束）
- [Aggregation Pipeline Optimization](https://www.mongodb.com/docs/manual/core/aggregation-pipeline-optimization/)（R1~R9 与 SBE 优化）
- [Aggregation Pipeline Limits](https://www.mongodb.com/docs/manual/core/aggregation-pipeline-limits/)（1000/100 MB/16 MiB/allowDiskUseByDefault）
- [The ESR (Equality, Sort, Range) Guideline](https://www.mongodb.com/docs/manual/tutorial/equality-sort-range-guideline/)（ESR 与 `$in` 201 分界）
- 项目内相关 demo:`02-NoSQL/MongoDB写关注/`（副本集 writeConcern/readConcern）、`07-数据存储/02-NoSQL/Cassandra一致性/`
