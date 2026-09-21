# 关系型数据库

> MySQL / PostgreSQL 等关系型数据库的核心机制: **MVCC / WAL / 事务锁 / 隔离级别 / 缓冲池 / 优化器 / 2PC / Prepared Statement**。
> 已覆盖的 demo 见索引。

## 已完成 demo

| 主题 | 路径 | 关键点 |
| --- | --- | --- |
| MVCC 多版本并发控制 | [MVCC/](./MVCC/) | DB_TRX_ID + DB_ROLL_PTR + undo log 链 + ReadView 4 条规则 (InnoDB 风格) + PostgreSQL xmin/xmax 对照 |
| WAL 预写日志 + ARIES 恢复 | [WAL/](./WAL/) | LSN + prevLSN + Steal+No-Force + Fuzzy checkpoint + Analysis/Redo/Undo 三阶段 |
| 两阶段锁 2PL + 死锁检测 | [2PL/](./2PL/) | S/X 锁兼容矩阵 + Growing/Shrinking + waits-for 图 + Strict 2PL 防 cascading abort |
| **隔离级别与幻读** | [IsolationLevels/](./IsolationLevels/) | SQL 4 级别 × 4 异常矩阵 + PG SI vs InnoDB next-key lock 对照 + SSI pivot 检测 |
| **缓冲池 Buffer Pool** | [BufferPool/](./BufferPool/) | InnoDB midpoint LRU(3/8 old sublist)+ clock sweep + WAL-before-data + fuzzy checkpoint |
| **CBO 成本优化器** | [CostBasedOptimizer/](./CostBasedOptimizer/) | PG 成本模型(1.0/4.0/0.01)+ 直方图/MCV/NDV + DP join 枚举(Selinger 1979)+ GEQO |
| **两段式提交 2PC / XA** | [TwoPhaseCommit/](./TwoPhaseCommit/) | 协调者/参与者状态机 + 决定日志 + recovery + blocking 问题 + XA-style API |
| **Prepared Statement + Pool** | [PreparedStatementPool/](./PreparedStatementPool/) | PG 扩展协议(Parse→Bind→Execute)+ custom/generic plan + PgBouncer max_prepared_statements |
| **主从复制与 binlog** | [ReplicationBinlog/](./ReplicationBinlog/) | SBR/RBR 格式 + 三线程链路 + GTID 幂等 + 半同步 AFTER_SYNC/AFTER_COMMIT + 超时降级 |
| **逻辑复制与 CDC** | [LogicalDecodingCDC/](./LogicalDecodingCDC/) | logical decoding 流水线 + slot 持留 WAL + REPLICA IDENTITY + Debezium 事件/offset/snapshot |
| **Join 算法** | [JoinAlgorithms/](./JoinAlgorithms/) | 朴素/块嵌套/hash(GRACE 分区 3(M+N))/sort-merge 成本模型 + 重复键回溯(CMU 15-445) |
| **物化视图 + 增量刷新** | [MaterializedView/](./MaterializedView/) | 持久化 + 全量 REFRESH 阻塞语义 vs CONCURRENTLY diff 增量 + 唯一索引前置 |
| **在线 DDL** | [OnlineDDL/](./OnlineDDL/) | INSTANT/INPLACE/COPY 矩阵 + MDL 两端独占 + row log + 行版本 64 上限 + gh-ost binlog 流 |
| **分片与路由键空间** | [ShardingVitess/](./ShardingVitess/) | 键范围含起点不含终点 + 左对齐使右侧 0 可省 + `0x80` 中点 + numeric/reverse_bits vindex 分布对照(10000:0 vs 5000:5000) + fan-out 三形态 + resharding 键不动 |
| **EXPLAIN ANALYZE 读数** | [ExplainAnalyze/](./ExplainAnalyze/) | actual rows/time 是**每次执行均值**要乘 loops + `Rows Removed by Filter` 出现条件 + BitmapAnd 恒 0 + LIMIT/merge join 的假性落差 + cost 445/470 原式 |
| **并行查询 worker 数** | [ParallelQuery/](./ParallelQuery/) | `compute_parallel_worker` 的 log3 三倍阈值循环 + `min_parallel_table_scan_size` 1024 页下界 + `max_parallel_workers_per_gather=2` 上限 + reloption 覆盖 + 四条不可并行条件 |
| **autovacuum 与 XID 回绕** | [AutovacuumXID/](./AutovacuumXID/) | `50+0.2N` 阈值与 1 亿封顶 + insert 阈值按未冻结比例缩放 + 打分是比值 + XID 四线(wrap/stop(300 万)/warn(1 亿)/vac(2 亿)) |
| **声明式分区与裁剪** | [PartitionPruning/](./PartitionPruning/) | RANGE 含下界不含上界 + HASH 无法按区间裁剪 + DEFAULT 分区排除不掉 + 三阶段裁剪(计划期/Subplans Removed/never executed) |

> **B+ 树索引**（MVCC 配套存储结构）已在 [`02-Web开发/03-数据库/B+树索引/`](../../02-Web开发/03-数据库/B+树索引/) 完成。

## 待研究

- [x] 主从复制与 binlog（ROW vs STATEMENT 格式 + GTID + 异步/半同步）→ [ReplicationBinlog/](./ReplicationBinlog/)
- [x] 逻辑复制与 CDC（logical decoding / debezium）→ [LogicalDecodingCDC/](./LogicalDecodingCDC/)
- [x] 分库分表（Sharding 中间件：Citus / TiDB / Vitess）→ [ShardingVitess/](./ShardingVitess/)
- [x] 在线 DDL：pt-online-schema-change / gh-ost → [OnlineDDL/](./OnlineDDL/)
- [x] 慢查询分析：EXPLAIN ANALYZE + plans → [ExplainAnalyze/](./ExplainAnalyze/)
- [x] Materialized Views + 增量刷新 → [MaterializedView/](./MaterializedView/)
- [ ] 物化视图与查询重写(query rewrite)
- [ ] 列式存储与向量化执行（PG 的 fdw/cstore、ClickHouse 的 MergeTree 对照）
- [ ] 并行应用的代价模型：`parallel_setup_cost` / `parallel_tuple_cost` 的实测标定
- [ ] 分区表上的 partitionwise join / aggregate 收益
- [ ] 备份与 PITR：pg_basebackup + WAL 归档 + 时间点恢复
