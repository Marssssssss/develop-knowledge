# Web 数据库

## 子领域

- [SQL与ORM/](./SQL与ORM/) — 索引、事务、SQLAlchemy / GORM
- [NoSQL/](./NoSQL/) — MongoDB / Elasticsearch / Redis

## 已完成 demo

- [x] [B+树索引/](./B+树索引/) — B+ 树索引原理(M-way + leaf sibling chain + copy-up/push-up 分裂 + borrow/merge 重平衡 + bulk-load O(N))
- [x] [SQL与ORM/N+1与预取策略/](./SQL与ORM/N+1与预取策略/) — 懒加载 vs 4 种预取策略的语句数与结果等价性 (Python + Go)
- [x] [SQL与ORM/EXPLAIN执行计划解读/](./SQL与ORM/EXPLAIN执行计划解读/) — 代价模型 + 节点树解析 + loops 换算 + rows removed (Python + Go)
- [x] [SQL与ORM/连接池调优/](./SQL与ORM/连接池调优/) — 池大小经验公式 + pool-locking 下界 + 饱和曲线 (Python + Go)
- [x] [SQL与ORM/键集分页/](./SQL与ORM/键集分页/) — OFFSET O(N) vs keyset O(log N) + 翻页一致性 (Python + Go)
- [x] [NoSQL/Redis数据结构底层/](./NoSQL/Redis数据结构底层/) — dict 渐进式 rehash + zskiplist span 排名 (Python + Go)

## 待研究

- [ ] MVCC 实现
- [x] SQL 优化（EXPLAIN）→ 见 [SQL与ORM/EXPLAIN执行计划解读/](./SQL与ORM/EXPLAIN执行计划解读/)
- [x] ORM 性能陷阱（N+1）→ 见 [SQL与ORM/N+1与预取策略/](./SQL与ORM/N+1与预取策略/)
- [ ] 事务隔离级别（MVCC 可见性 + 幻读/写偏斜）
- [ ] 关系型分库分表与在线 DDL
- [ ] 列式存储与 OLAP 引擎

## 参考资料

- PostgreSQL 14.1 官方文档 — Using EXPLAIN：<https://www.postgresql.org/docs/14/using-explain.html>
- PostgreSQL 14.1 官方文档 — LIMIT and OFFSET：<https://www.postgresql.org/docs/14/queries-limit.html>
- SQLAlchemy 2.0 官方文档 — Relationships / Loading Techniques：<https://docs.sqlalchemy.org/en/20/orm/relationships.html>
- HikariCP 官方 wiki — About Pool Sizing：<https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing>
- Redis 源码 — `src/dict.c` / `src/t_zset.c` / `src/server.h`：<https://github.com/redis/redis>
