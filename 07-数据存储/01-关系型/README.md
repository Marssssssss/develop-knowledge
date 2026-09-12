# 关系型数据库

> MySQL / PostgreSQL 等关系型数据库的核心机制: **MVCC / WAL / 事务锁 / 隔离级别**。
> 已覆盖的 demo 见索引。

## 已完成 demo

| 主题 | 路径 | 关键点 |
| --- | --- | --- |
| MVCC 多版本并发控制 | [MVCC/](./MVCC/) | DB_TRX_ID + DB_ROLL_PTR + undo log 链 + ReadView 4 条规则 (InnoDB 风格) + PostgreSQL xmin/xmax 对照 |
| WAL 预写日志 + ARIES 恢复 | [WAL/](./WAL/) | LSN + prevLSN + Steal+No-Force + Fuzzy checkpoint + Analysis/Redo/Undo 三阶段 |
| 两阶段锁 2PL + 死锁检测 | [2PL/](./2PL/) | S/X 锁兼容矩阵 + Growing/Shrinking + waits-for 图 + Strict 2PL 防 cascading abort |

> **B+ 树索引**（MVCC 配套存储结构）已在 [`02-Web开发/03-数据库/B+树索引/`](../../02-Web开发/03-数据库/B+树索引/) 完成。

## 待研究

- [ ] 隔离级别与幻读（next-key lock / predicate lock / SERIALIZABLE）
- [ ] 主从复制与 binlog（ROW vs STATEMENT 格式 + GTID + 异步/半同步）
- [ ] 逻辑复制与 CDC（logical decoding / debezium）
- [ ] 分库分表（Sharding 中间件：Citus / TiDB / Vitess）
- [ ] 连接池与 prepared statement 性能
- [ ] 优化器：cost-based + rule-based + histogram
- [ ] 在线 DDL：pt-online-schema-change / gh-ost
- [ ] 慢查询分析：EXPLAIN ANALYZE + plans
