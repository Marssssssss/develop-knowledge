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

## 待研究(子类目待补)

- [ ] ScyllaDB(分片感知驱动 / shard-per-core;与 Cassandra 的 CQL 差异)
- [ ] DynamoDB 的 Adaptive Capacity / Auto Partitioning(容量模式自适应部分)
- [ ] MongoDB 分片集群(min mongos / chunk 分裂与迁移 / 均衡器窗口)
- [ ] Cassandra 二级索引与 SAI(本地索引 vs 全局索引的代价)
- [ ] HBase 架构(RegionServer / HFile / 预分区与热点)
- [ ] 时序数据库(InfluxDB / TDengine — 单独新建 `03-时序数据库/`;2026-09-12 起不在巡检范围)
