# NoSQL

## 已完成 demo

- [x] **Dynamo 风格存储** — 一致性哈希 + 偏好列表 + vector clock + hinted handoff;见 [Dynamo风格/](./Dynamo风格/)(C / Python / Go)
- [x] **Cassandra 一致性级别** — ONE/QUORUM/ALL + 强一致性公式 + read repair + LWT(Paxos)+ anti-entropy;见 [Cassandra一致性/](./Cassandra一致性/)(C / Python / Go)
- [x] **MongoDB 副本集 + writeConcern/readConcern** — oplog pull + 隐式 WC 公式(P-S-A trap)+ 5 级 readConcern + 因果一致性;见 [MongoDB写关注/](./MongoDB写关注/)(C / Python / Go)

## 待研究(子类目待补)

- [ ] DynamoDB / ScyllaDB(API 用法;DBA 视角)
- [ ] 时序数据库(InfluxDB / TDengine — 单独新建 `03-时序数据库/`)
- [ ] 文档模型 MongoDB CRUD 实践(与本节"复制/写关注"分开)
- [ ] 图数据库(Neo4j / Memgraph — 单独新建 `04-图数据库/`)
- [ ] KV 编码(Protobuf 在 NoSQL 中的角色)
- [ ] DynamoDB 的 Adaptive Capacity / Auto Partitioning
