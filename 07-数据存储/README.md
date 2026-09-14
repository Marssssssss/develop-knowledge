# 07 数据存储

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-关系型/](./01-关系型/) | MySQL / PostgreSQL / 索引 / 事务 |
| [02-NoSQL/](./02-NoSQL/) | MongoDB / Cassandra / DynamoDB |
| [03-缓存/](./03-缓存/) | Redis / Memcached |
| [04-搜索引擎/](./04-搜索引擎/) | Elasticsearch / Meilisearch |
| [05-图数据库/](./05-图数据库/) | Neo4j / Cypher / 属性图模型 |

## 已完成 demo

- [x] Elasticsearch 倒排索引与近实时搜索 — 见 [04-搜索引擎/elasticsearch/](./04-搜索引擎/elasticsearch/)（Python / Go）
- [x] BM25 评分算法（Lucene/ES 默认相关性，k1=1.2 / b=0.75 / tf 饱和 / 长度归一化） — 见 [04-搜索引擎/BM25评分/](./04-搜索引擎/BM25评分/)（Python / Go）
- [x] HNSW 近似最近邻检索（Malkov 2016，多层可导航小世界图，O(log N) 查询复杂度） — 见 [04-搜索引擎/HNSW向量检索/](./04-搜索引擎/HNSW向量检索/)（Python / Go）
- [x] Lucene 段合并与删除回收（TieredMergePolicy + 20 MB/s 节流 + forceMerge） — 见 [04-搜索引擎/段合并策略/](./04-搜索引擎/段合并策略/)（Python / Go）
- [x] MVCC 多版本并发控制 — 见 [01-关系型/MVCC/](./01-关系型/MVCC/)（C / Python / Go）
- [x] WAL 预写日志 + ARIES 恢复 — 见 [01-关系型/WAL/](./01-关系型/WAL/)（C / Python / Go）
- [x] 两阶段锁 2PL + 死锁检测 — 见 [01-关系型/2PL/](./01-关系型/2PL/)（C / Python / Go）
- [x] Dynamo 风格存储(一致哈希 + vector clock + sloppy quorum + hinted handoff)— 见 [02-NoSQL/Dynamo风格/](./02-NoSQL/Dynamo风格/)(C / Python / Go)
- [x] Cassandra 一致性级别(CL 决策 + read repair + LWT/Paxos + anti-entropy)— 见 [02-NoSQL/Cassandra一致性/](./02-NoSQL/Cassandra一致性/)(C / Python / Go)
- [x] MongoDB 副本集 writeConcern / readConcern(oplog pull + 隐式 WC + 因果一致性)— 见 [02-NoSQL/MongoDB写关注/](./02-NoSQL/MongoDB写关注/)(C / Python / Go)
- [x] Redis 持久化（RDB 快照 + AOF 日志 + Fork 写时复制）— 见 [03-缓存/RedisPersistence/](./03-缓存/RedisPersistence/)(C / Python / Go)
- [x] Redis Cluster（16384 哈希槽 + CRC16/XMODEM + Gossip + 故障转移）— 见 [03-缓存/RedisCluster/](./03-缓存/RedisCluster/)(C / Python / Go)
- [x] 缓存淘汰算法（Memcached 精确 LRU vs Redis 近似 LRU + 候选池）— 见 [03-缓存/LRUEviction/](./03-缓存/LRUEviction/)(C / Python / Go)

## 待研究

- [ ] MySQL InnoDB B+ 树索引（已迁到 `02-Web开发/03-数据库/B+树索引/`）
- [ ] MVCC 多版本并发控制（已在 `01-关系型/MVCC/` 完成）
- [ ] Redis 持久化（RDB / AOF）（已在 `03-缓存/RedisPersistence/` 完成）
- [x] ES BM25 评分算法细节（已在 `04-搜索引擎/BM25评分/`）
- [ ] 隔离级别与幻读防（next-key lock / SSI）
- [ ] Redis Sentinel 高可用
- [ ] Memcached vs Redis 业务选型对比（03-缓存 已部分覆盖；缓存目录仍待深挖）
- [ ] 中文 IK 分词器与 BM25 协同（影响 avgdl / length normalization）
- [x] 图数据库 Neo4j / Cypher / 属性图模型（`05-图数据库/` 两批 10 demo：PropertyGraphModel / CypherParser / GraphTraversal / PageRank / Neo4jStorage + VarLengthPath / BoltProtocol / Neo4jLocks / CypherPipeline / MergeSemantics）
- [ ] 时序数据库 InfluxDB / TDengine（03-时序数据库，2026-09-12 起不在巡检范围）