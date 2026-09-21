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
- [x] 分析器链与中文分词（char filter→tokenizer→token filter 三段式 + ik_max_word/ik_smart + discount_overlaps 影响 norm） — 见 [04-搜索引擎/分析器链与中文分词/](./04-搜索引擎/分析器链与中文分词/)（C / Python / Go）
- [x] bool 查询与评分合并（must/should 计分相加、filter/must_not 不计分、minimum_should_match 规格与默认值） — 见 [04-搜索引擎/Bool查询与评分合并/](./04-搜索引擎/Bool查询与评分合并/)（C / Python / Go）
- [x] 分布式检索两阶段与深分页（scatter-gather + from/size 10000 上限 + search_after/PIT + query_then_fetch vs dfs） — 见 [04-搜索引擎/分布式检索与深分页/](./04-搜索引擎/分布式检索与深分页/)（C / Python / Go）
- [x] DocValues 列式存储与聚合（doc→term 反向访问、doc-value-only、global ordinals + packed ints） — 见 [04-搜索引擎/DocValues列式存储与聚合/](./04-搜索引擎/DocValues列式存储与聚合/)（C / Python / Go）
- [x] IVF-PQ 向量量化（残差乘积量化 + M×ksub 距离表 ADC + SDC + nprobe 召回/代价权衡） — 见 [04-搜索引擎/IVFPQ向量量化/](./04-搜索引擎/IVFPQ向量量化/)（C / Python / Go）
- [x] MVCC 多版本并发控制 — 见 [01-关系型/MVCC/](./01-关系型/MVCC/)（C / Python / Go）
- [x] WAL 预写日志 + ARIES 恢复 — 见 [01-关系型/WAL/](./01-关系型/WAL/)（C / Python / Go）
- [x] 两阶段锁 2PL + 死锁检测 — 见 [01-关系型/2PL/](./01-关系型/2PL/)（C / Python / Go）
- [x] Dynamo 风格存储(一致哈希 + vector clock + sloppy quorum + hinted handoff)— 见 [02-NoSQL/Dynamo风格/](./02-NoSQL/Dynamo风格/)(C / Python / Go)
- [x] Cassandra 一致性级别(CL 决策 + read repair + LWT/Paxos + anti-entropy)— 见 [02-NoSQL/Cassandra一致性/](./02-NoSQL/Cassandra一致性/)(C / Python / Go)
- [x] MongoDB 副本集 writeConcern / readConcern(oplog pull + 隐式 WC + 因果一致性)— 见 [02-NoSQL/MongoDB写关注/](./02-NoSQL/MongoDB写关注/)(C / Python / Go)
- [x] MongoDB 聚合管道与查询优化(R1~R9 规则 + ESR 索引 + `$in` 201 阈值 + 1000 阶段限制)— 见 [02-NoSQL/MongoDB聚合与查询优化/](./02-NoSQL/MongoDB聚合与查询优化/)(Python / Go)
- [x] DynamoDB 单表设计与容量(4KB/1KB 步进 + 3000 RCU / 1000 WCU 分区顶 + LSI/GSI 差异)— 见 [02-NoSQL/DynamoDB单表设计与容量/](./02-NoSQL/DynamoDB单表设计与容量/)(Python / Go)
- [x] Cassandra 数据建模与墓碑(查询驱动建模 + 三条清除条件 + STCS/LCS/TWCS)— 见 [02-NoSQL/Cassandra数据建模与墓碑/](./02-NoSQL/Cassandra数据建模与墓碑/)(Python / Go)
- [x] KV 存储引擎 LSM(WAL 32KB 分块 + `10^L` MB 分层 + 动态层级 + 三类写停顿)— 见 [02-NoSQL/KV存储引擎LSM/](./02-NoSQL/KV存储引擎LSM/)(Python / Go)
- [x] Protobuf 编码(varint / ZigZag / tag / packed / 未知字段跳过 / Last One Wins)— 见 [02-NoSQL/Protobuf编码/](./02-NoSQL/Protobuf编码/)(Python / Go)
- [x] Redis 持久化（RDB 快照 + AOF 日志 + Fork 写时复制）— 见 [03-缓存/RedisPersistence/](./03-缓存/RedisPersistence/)(C / Python / Go)
- [x] Redis Cluster（16384 哈希槽 + CRC16/XMODEM + Gossip + 故障转移）— 见 [03-缓存/RedisCluster/](./03-缓存/RedisCluster/)(C / Python / Go)
- [x] 缓存淘汰算法（Memcached 精确 LRU vs Redis 近似 LRU + 候选池）— 见 [03-缓存/LRUEviction/](./03-缓存/LRUEviction/)(C / Python / Go)
- [x] 主从复制与 binlog（SBR/RBR + GTID + 半同步 AFTER_SYNC/AFTER_COMMIT + 超时降级）— 见 [01-关系型/ReplicationBinlog/](./01-关系型/ReplicationBinlog/)(Python / Go)
- [x] 逻辑复制与 CDC（logical decoding + slot 持留 WAL + Debezium offset/snapshot）— 见 [01-关系型/LogicalDecodingCDC/](./01-关系型/LogicalDecodingCDC/)(Python / Go)
- [x] Join 算法（hash/GRACE/sort-merge 成本模型，CMU 15-445）— 见 [01-关系型/JoinAlgorithms/](./01-关系型/JoinAlgorithms/)(Python / Go)
- [x] 物化视图增量刷新（全量 REFRESH 阻塞 vs CONCURRENTLY diff + 唯一索引）— 见 [01-关系型/MaterializedView/](./01-关系型/MaterializedView/)(Python / Go)
- [x] 在线 DDL（INSTANT/INPLACE/COPY + MDL 两端独占 + gh-ost binlog 流）— 见 [01-关系型/OnlineDDL/](./01-关系型/OnlineDDL/)（Python / Go）
- [x] HTTP 缓存语义（RFC 9111 新鲜度/Age/Vary + RFC 5861 stale 扩展）— 见 [03-缓存/HTTP缓存语义/](./03-缓存/HTTP缓存语义/)（Python / Go）
- [x] 缓存一致性模式（写顺序竞态枚举 + Facebook lease 防 stale set/惊群）— 见 [03-缓存/缓存一致性模式/](./03-缓存/缓存一致性模式/)（C / Python / Go）
- [x] W-TinyLFU 准入策略（Caffeine 4-bit CMS + 窗口 LRU + 主区 SLRU）— 见 [03-缓存/WTinyLFU准入/](./03-缓存/WTinyLFU准入/)（Python / Go）
- [x] Redis 过期与淘汰（EXPIRE GT/LT + activeExpireCycle + LFU Morris 计数）— 见 [03-缓存/Redis过期与淘汰/](./03-缓存/Redis过期与淘汰/)（C / Python / Go）
- [x] Memcached Slab 分配器（39 个 class 建表 + 内部碎片 + 页卡死）— 见 [03-缓存/MemcachedSlab分配/](./03-缓存/MemcachedSlab分配/)（C / Python / Go）
- [x] 隔离级别与幻读（SQL 四级别 × 四异常 + PG SI vs InnoDB next-key + SSI pivot）— 见 [01-关系型/IsolationLevels/](./01-关系型/IsolationLevels/)（Python / Go）
- [x] 缓冲池 Buffer Pool（midpoint LRU 3/8 + clock sweep + WAL-before-data）— 见 [01-关系型/BufferPool/](./01-关系型/BufferPool/)（Python / Go）
- [x] CBO 成本优化器（PG 1.0/4.0/0.01 成本模型 + 直方图/MCV/NDV + Selinger DP + GEQO）— 见 [01-关系型/CostBasedOptimizer/](./01-关系型/CostBasedOptimizer/)（Python / Go）
- [x] 两段式提交 2PC / XA（协调者状态机 + 决定日志 + blocking 问题）— 见 [01-关系型/TwoPhaseCommit/](./01-关系型/TwoPhaseCommit/)（Python / Go）
- [x] Prepared Statement 与连接池（PG 扩展协议 + custom/generic plan + PgBouncer）— 见 [01-关系型/PreparedStatementPool/](./01-关系型/PreparedStatementPool/)（Python / Go）
- [x] 分片与路由键空间（Vitess 键范围 [start,end) + 左对齐 + vindex 分布对照 + resharding）— 见 [01-关系型/ShardingVitess/](./01-关系型/ShardingVitess/)（Python / Go）
- [x] EXPLAIN ANALYZE 读数口径（loops 归一化 + BitmapAnd 恒 0 + LIMIT/merge join 假性落差）— 见 [01-关系型/ExplainAnalyze/](./01-关系型/ExplainAnalyze/)（Python / Go）
- [x] 并行查询 worker 数（`compute_parallel_worker` log3 公式 + 4 个旋钮 + 不可并行四条件）— 见 [01-关系型/ParallelQuery/](./01-关系型/ParallelQuery/)（Python / Go）
- [x] autovacuum 与事务 ID 回绕（`50+0.2N` 阈值与 1 亿封顶 + XID 四道防线）— 见 [01-关系型/AutovacuumXID/](./01-关系型/AutovacuumXID/)（Python / Go）
- [x] 声明式分区与分区裁剪（RANGE/LIST/HASH 差异 + 三阶段裁剪 + DEFAULT 分区天花板）— 见 [01-关系型/PartitionPruning/](./01-关系型/PartitionPruning/)（Python / Go）

## 待研究

- [ ] MySQL InnoDB B+ 树索引（已迁到 `02-Web开发/03-数据库/B+树索引/`）
- [ ] MVCC 多版本并发控制（已在 `01-关系型/MVCC/` 完成）
- [ ] Redis 持久化（RDB / AOF）（已在 `03-缓存/RedisPersistence/` 完成）
- [x] ES BM25 评分算法细节（已在 `04-搜索引擎/BM25评分/`）
- [ ] 隔离级别与幻读防（next-key lock / SSI）
- [ ] Redis Sentinel 高可用
- [x] 中文 IK 分词器与 BM25 协同（影响 avgdl / length normalization）（已在 `04-搜索引擎/分析器链与中文分词/`）
- [x] 图数据库 Neo4j / Cypher / 属性图模型（`05-图数据库/` 三批 15 demo：PropertyGraphModel / CypherParser / GraphTraversal / PageRank / Neo4jStorage + VarLengthPath / BoltProtocol / Neo4jLocks / CypherPipeline / MergeSemantics + PathMatchModes / CypherNullLogic / SearchIndexes / SchemaConstraints / LouvainModularity）
- [x] Cypher 路径匹配模式（默认关系唯一性 TRAIL / REPEATABLE ELEMENTS=WALK / ACYCLIC，七桥图 2·48·0 与路由器网 80 条 + 十项占比）— 见 [05-图数据库/PathMatchModes/](./05-图数据库/PathMatchModes/)（Python / Go）
- [x] Cypher null 与三值逻辑（官方 9 行真值表 + AND/OR 吸收律 vs XOR 无吸收 + IN 八例 + 类型谓词对 null 恒 true + 排序 null 升序最后/降序最前）— 见 [05-图数据库/CypherNullLogic/](./05-图数据库/CypherNullLogic/)（Python / Go）
- [x] Neo4j 搜索性能索引（Range/Text/Point/Token 四类谓词可解性 + trigram 索引 "developer" + planner 选择与 USING 提示 + 复合索引收录条件）— 见 [05-图数据库/SearchIndexes/](./05-图数据库/SearchIndexes/)（Python / Go）
- [x] Neo4j 约束（唯一性 / 存在性 / 类型 / Key，Key = 存在性+唯一性，`LIST<STRING NOT NULL>` 与联合类型，约束名与索引共享命名空间）— 见 [05-图数据库/SchemaConstraints/](./05-图数据库/SchemaConstraints/)（Python / Go）
- [x] Louvain 与模块度（式(1) Q 与式(2) ΔQ 增量、两阶段聚合、自环 A_ii=2s 的聚合不变性、ring of 30 cliques 30→15）— 见 [05-图数据库/LouvainModularity/](./05-图数据库/LouvainModularity/)（Python / Go）
- [ ] 时序数据库 InfluxDB / TDengine（03-时序数据库，2026-09-12 起不在巡检范围）