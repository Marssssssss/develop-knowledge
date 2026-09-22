# NoSQL

## 已完成 demo

- [x] **Dynamo 风格存储** — 一致性哈希 + 偏好列表 + vector clock + hinted handoff;见 [Dynamo风格/](./Dynamo风格/)(C / Python / Go)
- [x] **Cassandra 一致性级别** — ONE/QUORUM/ALL + 强一致性公式 + read repair + LWT(Paxos)+ anti-entropy;见 [Cassandra一致性/](./Cassandra一致性/)(C / Python / Go)
- [x] **MongoDB 副本集 + writeConcern/readConcern** — oplog pull + 隐式 WC 公式(P-S-A trap)+ 5 级 readConcern + 因果一致性;见 [MongoDB写关注/](./MongoDB写关注/)(C / Python / Go)
- [x] **MongoDB 聚合管道与查询优化** — R1~R9 优化规则(投影下推 / `$sort`+`$match` / `$limit` 合并…)+ ESR 索引准则 + `$in` 201 元素阈值 + 1000 阶段 / 100MB 阻塞阶段限制;见 [MongoDB聚合与查询优化/](./MongoDB聚合与查询优化/)(Python / Go)
- [x] **DynamoDB 单表设计与容量** — 4KB/1KB 读写单位步进 + 3000 RCU / 1000 WCU 分区硬顶 + LSI/GSI 四项差异 + 写分片与读扇出;见 [DynamoDB单表设计与容量/](./DynamoDB单表设计与容量/)(Python / Go)
- [x] **Cassandra 数据建模与墓碑** — 查询驱动建模 + 主键分量语义 + 分区 10 万值/100MB 两条线 + 墓碑三条清除条件 + STCS/LCS/TWCS;见 [Cassandra数据建模与墓碑/](./Cassandra数据建模与墓碑/)(Python / Go)
- [x] **KV 存储引擎 LSM** — WAL 32KB 分块 + 7 字节头部 + `10^L` MB 分层 + 动态层级 90% 末层 + 三类写停顿;LevelDB/RocksDB 官方口径;见 [KV存储引擎LSM/](./KV存储引擎LSM/)(Python / Go)
- [x] **Protobuf 编码** — base-128 varint + ZigZag + tag 公式 + packed 双向兼容 + 未知字段跳过 + Last One Wins;见 [Protobuf编码/](./Protobuf编码/)(Python / Go)
- [x] **ScyllaDB 分片感知与 shard-per-core** — `zero_based_shard_of` = floor(0.token × shards)、msb 左移弃高位、`init_zero_based_shard_start` 逐格 +1 修正、minimum/maximum 三态硬编码、tablet 迁移期双 shard 写、fixed_shard 的 `[shard:16][hash:48]`;见 [ScyllaDB分片感知与shard-per-core/](./ScyllaDB分片感知与shard-per-core/)(Python / Go)
- [x] **DynamoDB 自适应容量与热分区** — 3000 RCU / 1000 WCU 分区设计上限、20KB item 消耗 5 RCU 使单分区并发降到 600、adaptive capacity 借用冷分区余量但冲不过硬顶、写分片的随机后缀与「码点乘积 mod 200 + 1」计算后缀及其读扇出代价;见 [DynamoDB自适应容量与热分区/](./DynamoDB自适应容量与热分区/)(Python / Go)
- [x] **MongoDB 分片集群与 chunk 均衡** — 均衡阈值 = 3 × range size(默认 384MB)、并发上限 floor(n/2)、range 迁移 7 步与异步删除、jumbo chunk 的 divisible/indivisible 两种处置;见 [MongoDB分片集群与chunk均衡/](./MongoDB分片集群与chunk均衡/)(Python / Go)
- [x] **Cassandra 二级索引与 SAI** — per-SSTable / per-column 两层组件与两级完成标记、RowMapping 只在 FLUSH 时建立、**rowID 是 SSTable 局部的**、字符串走 byte-ordered trie 而数值走 bbtree、结果只保证 token 序;见 [Cassandra二级索引与SAI/](./Cassandra二级索引与SAI/)(Python / Go)
- [x] **HBase Region 与 HFile** — `IncreasingToUpperBound` 的 `initialSize × count³` 阈值(256MB→2048MB→6912MB→10GB 天花板)、count>100 防溢出、`max.filesize.jitter` 0.25 带来 ±12.5% 抖动错峰、HFile v3 只能写不能写旧版;见 [HBase Region与HFile/](./HBase%20Region与HFile/)(Python / Go)

## 待研究(子类目待补)

- [x] ScyllaDB(分片感知驱动 / shard-per-core)— 已在 `ScyllaDB分片感知与shard-per-core/` 完成
- [x] DynamoDB 的 Adaptive Capacity / Auto Partitioning — 已在 `DynamoDB自适应容量与热分区/` 完成
- [x] MongoDB 分片集群(chunk 分裂与迁移 / 均衡器窗口)— 已在 `MongoDB分片集群与chunk均衡/` 完成
- [x] Cassandra 二级索引与 SAI(本地索引 vs 全局索引的代价)— 已在 `Cassandra二级索引与SAI/` 完成
- [x] HBase 架构(RegionServer / HFile / 预分区与热点)— 已在 `HBase Region与HFile/` 完成
- [ ] 时序数据库(InfluxDB / TDengine — 单独新建 `03-时序数据库/`;2026-09-12 起不在巡检范围)
- [ ] Couchbase(N1QL 与内存优先架构)
- [ ] ScyllaDB tablets(与 vnode 的迁移代价对比)
- [ ] MongoDB 8.0 reshard 到相同 shard key 的内部流程
- [ ] HBase MOB 与 DateTieredCompaction
