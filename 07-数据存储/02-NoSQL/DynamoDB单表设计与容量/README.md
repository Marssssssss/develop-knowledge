# DynamoDB 单表设计与容量规划 — 单位换算 + 分区硬顶 + LSI/GSI

> 权威来源(全部实际读过):
> - [Best practices for designing and using partition keys effectively](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-design.html) — 3000 RCU/1000 WCU 每分区、4 KB/1 KB 单位、20 KB 条目示例
> - [Using Global Secondary Indexes in DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GSI.html) — GSI 键非唯一、缺排序键不落索引、吞吐独立、不能回表
> - [Using Local Secondary Indexes in DynamoDB](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/LSI.html) — 10 GB 项目集合、强一致读、回表按整条计费
> - [Using write sharding to distribute workloads evenly](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-sharding.html) — 随机后缀 1..200 官方示例
> - [Designing partition keys to distribute your workload](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-uniform-load.html) — 分区键均匀度对照表
> - [DynamoDB Cheat Sheet](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/CheatSheet.html) — 400 KB 条目、键长度、LSI 5 个 / GSI 20 个 / 投影属性 100 个
> - [Provisioned capacity pricing](https://aws.amazon.com/dynamodb/pricing/provisioned/) — 强一致/最终一致/事务读的 1 / 0.5 / 2 倍关系

## 简介

DynamoDB 的容量规划可以完全算出来的 —— 没有"大概"的余量:读单位按 4 KB 台阶、写单位按 1 KB 台阶,单个分区(单个分区键值)硬顶 3000 读单位/1000 写单位,单条条目 400 KB。本 demo 把这套算术、LSI/GSI 的结构约束、以及热分区与写分片模型化,并用官方文档给出的每个示例数字做断言。

关键概念(每条 1 句):
- **RCU/WCU**:1 读单位 = 每秒 1 次强一致读(≤4 KB)或 2 次最终一致读;1 写单位 = 每秒 1 次写(≤1 KB)
- **单分区硬顶**:每分区 3000 读单位/秒、1000 写单位/秒 —— 这是**物理上限**,自适应容量只负责隔离热点,不会抬高它
- **热分区**:分区键值分布不均 → 请求集中到少数分区 → 限流;官方均匀度对照表里 `Status code`(只有几个取值)与"按天取整的创建时间"都是 Bad
- **写分片**:给热分区键加后缀(官方示例:日期键拼 1..200 的随机数)把写摊开,代价是读要扇出到每个后缀再合并
- **LSI**:同一分区键、换排序键;必须建表时创建;与主表**共用**吞吐;支持强一致读;每个分区键值最多 10 GB
- **GSI**:可换分区键;吞吐**独立**;只支持最终一致读;查询**不能**回主表取非投影属性

## 原理详解

### 1. 单位换算:台阶式取整,而不是线性

```text
读:ceil(item_bytes / 4 KB) × 一致性倍数   (strong 1 / eventual 0.5 / transactional 2)
写:ceil(item_bytes / 1 KB) × 事务倍数     (标准 1 / 事务 2)
```

官方示例(全部在自检里逐条复现):10 KB 条目最终一致读 = 1.5 读单位、强一致 = 3、事务 = 6;3 KB 写 = 3 写单位、事务写 = 6;10 KB 事务写 = 20 写单位。

**取整方式因 API 而异**:
- `BatchGetItem` 把**每一条**各自向上取整到 4 KB 再相加 —— 官方示例:1.5 KB + 6.5 KB 算成 12 KB(4 KB + 8 KB),而不是 8 KB
- `Query`/`Scan` 把返回集合的**总大小**一次性向上取整 —— 官方示例:合计 40.8 KB 向上取整到 44 KB
- 单页上限 1 MB,超出部分靠 `LastEvaluatedKey` 翻页;不存在条目也照收读容量

### 2. 单分区硬顶:600 次读 vs 1000 次写

官方给出的推导:20 KB 条目一次强一致读消耗 5 个读单位,所以 **3000 / 5 = 600 次/秒**就是单分区上限。写侧同理,1 KB 条目单分区 1000 次/秒。

关键点在于**这个上限是按分区键值、不是按表**。表级容量充足但单个分区键值过热,照样限流 —— 这正是写分片要解决的问题。

### 3. 写分片与它的读代价

官方写法:对日期分区键拼接 `1..200` 的随机后缀 → `2014-07-09.1` … `2014-07-09.200`,写入摊到 200 个分区。本 demo 的推导是 `N = ceil(target_WCU / 1000)`;当目标吞吐是 200000 WCU 时正好得到 200,与官方示例的后缀范围一致。

代价(官方原文):"to read all the items for a given day, you would have to query the items for all the suffixes and then merge the results" —— **读扇出 N 倍**,所以分片是写侧的优化、读侧的成本。

### 4. LSI vs GSI

| 维度 | LSI | GSI |
| --- | --- | --- |
| 分区键 | 与主表相同 | 可不同 |
| 排序键 | 与主表不同 | 任意 |
| 创建时机 | **必须建表时**定义(最多 5 个) | 可后加(默认配额 20 个) |
| 吞吐 | 与主表**共用**(查询消耗主表 RCU) | **独立**配置;不足会让主表写被限流 |
| 一致性 | 最终一致 + **强一致** | **仅**最终一致 |
| 非投影属性 | 回主表取,**按整条条目**计费 | 不允许回表 |
| 容量上限 | 每个分区键值 **10 GB**(含主表条目 + 索引条目) | 无此约束 |

第三条与第六条最容易被忽略:GSI 省容量但少能力,LSI 多能力但把项目集合的容量上限绑在单个分区键值上。

### 5. GSI 的写放大与限流

主表的一次写要同时写主表与所有受影响的 GSI,官方原文:"The total provisioned throughput cost for a write consists of the sum of write capacity units consumed by writing to the base table and those consumed by updating the global secondary indexes." 且 "For a table write to succeed, the provisioned throughput settings for the table and all of its global secondary indexes must have enough write capacity ... Otherwise, the write to the table is throttled." —— **GSI 容量不足会反噬主表写入**,这是"给表加索引"最反直觉的后果。

## 环境

- Python 3.13(纯标准库,无第三方依赖,不连 AWS)
- Go 1.18+;本机无 Go 工具链,走人工审查 + `_docs/tools/syntax_sanity.py`

## 运行方式

```bash
cd python && python3 dynamodb_check.py
go run ../go/dynamodb_capacity.go ../go/dynamodb_capacity_check.go
```

## 关键代码

```python
def read_units(item_bytes, consistency="strong"):
    return ceil_div(item_bytes, READ_UNIT_BYTES) * CONSISTENCY_FACTOR[consistency]

def batch_get_units(sizes, consistency="strong"):
    """每个条目各自向上取整(官方:1.5 KB + 6.5 KB → 12 KB)"""
    total = sum(ceil_div(s, READ_UNIT_BYTES) * READ_UNIT_BYTES for s in sizes)
    return read_units(total, consistency)
```

```python
# GSI 写容量不足 → 主表写被限流(官方原文)
for ix in self.gsis:
    if not ix.indexed(item):
        continue                      # 缺 GSI 排序键 → 不写索引条目
    need = write_units(ix.entry_size(item))
    if need > ix.wcu:
        raise ProvisionedThroughputExceededException(...)
```

## 性能边界

- **单分区 3000 RCU / 1000 WCU 是硬顶**,自适应容量(官方注:"Adaptive capacity applies to on-demand mode and provisioned capacity")只改善热点隔离,不会突破这个数字 —— 超了就得分片
- **10 GB 是分区承载能力**:官方在 LSI 一节明确 "each item collection is stored in one partition. The total size of such an item collection is limited to the capability of that partition: 10 GB";据此可估算分区数(本 demo 的 `partitions_for_size`)
- **GSI 让写放大按索引数线性增长**:每个 GSI 都要一份写容量,官方建议 GSI 的写容量"should be equal or greater than the write capacity of the base table"
- **1 MB 页上限限制了单次 Query 的返回量**,深分页靠 `LastEvaluatedKey` 逐页推进

## 注意事项与常见坑

- **官方文档在 1000 与 1024 之间不自洽**:ServiceQuotas 页写 "DynamoDB denotes 1 KB = 1024 bytes",而读写操作页的示例又说 1500 条 × 64 字节 = "96 KB"(1000 进制)。本 demo 一律按 **1024** 计算并在代码注释标注;做容量估算时这个 2.4% 的差会随条目数放大
- **回表按整条计费,不按缺失的那几个属性**:官方原文 "This charge is for reading each entire item from the table, not just the requested attributes"。自检里构造了"20 KB 主表条目 + 缺失属性仅 100 B"的场景:费用是 5.5 个单位而不是 1.5
- **LSI 建表后无法补建**,上线前的索引设计没有回头路;GSI 可以后加,但新加的 GSI 回填会消耗写容量
- **项目集合上限只影响 LSI 表**:官方明确 "This does not apply to item collections in tables without local secondary indexes"
- **缩小集合的写仍被允许**:条目集合超限后,删除条目或缩减属性仍可写入("Read and write operations that shrink the size of the item collection are still allowed"),否则会陷入死锁
- **自适应容量不要当成容量规划工具**:官方对分区键均匀度的建议是"design your application for uniform activity across all partition keys",而不是"靠自适应容量兜底"
- **自检用小阈值跑 10 GB 逻辑**:单条上限 400 KB 意味着真造一个 10 GB 集合需 2.6 万条,`Table.lsi_collection_limit` 因此做成实例属性,常量本身(10 GB)另有独立断言

## 参考资料

- [Partition key design best practices](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-design.html)(3000/1000 硬顶与 600 次读的推导)
- [Using Global Secondary Indexes](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GSI.html)(独立吞吐、写放大、不可回表)
- [Using Local Secondary Indexes](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/LSI.html)(10 GB 项目集合、强一致读、整条计费)
- [Using write sharding](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-sharding.html)(1..200 随机后缀与读扇出)
- [Designing partition keys to distribute your workload](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-uniform-load.html)(均匀度对照表)
- [DynamoDB Cheat Sheet](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/CheatSheet.html)、[Service Quotas](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/ServiceQuotas.html)
- 项目内相关 demo:`02-NoSQL/Dynamo风格/`(一致性哈希 + vector clock + sloppy quorum)、`02-NoSQL/Cassandra一致性/`
