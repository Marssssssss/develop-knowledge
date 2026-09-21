# Web 数据库

## 子领域

- [SQL与ORM/](./SQL与ORM/) — 索引、事务、SQLAlchemy / GORM
- [NoSQL/](./NoSQL/) — MongoDB / Elasticsearch / Redis

## 已完成 demo

- [x] [B+树索引/](./B+树索引/) — B+ 树索引原理(M-way + leaf sibling chain + copy-up/push-up 分裂 + borrow/merge 重平衡 + bulk-load O(N))
- [x] [SQL与ORM/ORM会话与工作单元/](./SQL与ORM/ORM会话与工作单元/) — 身份映射弱引用 + 已提交快照脏检查 + flush 三层顺序(saves 先于 deletes、表内 UPDATE 先于 INSERT、DELETE 逆拓扑序) (Python + Go)
- [x] [SQL与ORM/批量写入与Upsert/](./SQL与ORM/批量写入与Upsert/) — PG 唯一索引推断/arbiter + excluded 与 WHERE 最后求值 + cardinality violation + SQLite UPSERT 与 REPLACE 差异 (Python + Go)
- [x] [SQL与ORM/行级安全与多租户/](./SQL与ORM/行级安全与多租户/) — USING 与 WITH CHECK 两个方向 + 无策略即 default-deny + 属主绕过与 FORCE + permissive OR/restrictive AND (Python + Go)
- [x] [NoSQL/JSONB半结构化与索引/](./NoSQL/JSONB半结构化与索引/) — json 与 jsonb 的规范化差异 + 包含@>「越级不算」 + 存在? 只匹配顶层 + GIN 两操作符类与 fastupdate (Python + Go)
- [x] [列式存储与Parquet/](./列式存储与Parquet/) — Dremel 记录切分与装配(def/rep levels) + RLE/Bit-Packing 混合编码 + 数据页三段布局 (Python + Go)
- [x] [SQL与ORM/N+1与预取策略/](./SQL与ORM/N+1与预取策略/) — 懒加载 vs 4 种预取策略的语句数与结果等价性 (Python + Go)
- [x] [SQL与ORM/EXPLAIN执行计划解读/](./SQL与ORM/EXPLAIN执行计划解读/) — 代价模型 + 节点树解析 + loops 换算 + rows removed (Python + Go)
- [x] [SQL与ORM/连接池调优/](./SQL与ORM/连接池调优/) — 池大小经验公式 + pool-locking 下界 + 饱和曲线 (Python + Go)
- [x] [SQL与ORM/键集分页/](./SQL与ORM/键集分页/) — OFFSET O(N) vs keyset O(log N) + 翻页一致性 (Python + Go)
- [x] [NoSQL/Redis数据结构底层/](./NoSQL/Redis数据结构底层/) — dict 渐进式 rehash + zskiplist span 排名 (Python + Go)

## 待研究

- [ ] MVCC 实现（注：07-数据存储/01-关系型/MVCC 已有系统侧 demo，本目录若要写应取 Web 应用视角）
- [x] SQL 优化（EXPLAIN）→ 见 [SQL与ORM/EXPLAIN执行计划解读/](./SQL与ORM/EXPLAIN执行计划解读/)
- [x] ORM 性能陷阱（N+1）→ 见 [SQL与ORM/N+1与预取策略/](./SQL与ORM/N+1与预取策略/)
- [ ] 事务隔离级别（MVCC 可见性 + 幻读/写偏斜）
- [ ] 关系型分库分表与在线 DDL
- [x] 列式存储与 OLAP 引擎 → 见 [列式存储与Parquet/](./列式存储与Parquet/)
- [ ] 读写分离与复制延迟下的会话一致性（read-your-writes / 单调读）
- [ ] 数据库迁移（expand-contract / 零停机 DDL / gh-ost 式影子表）
- [ ] 慢查询治理（statement_timeout / lock_timeout / 死锁与重试）
- [ ] 分布式事务与 outbox 模式（至少一次投递 + 幂等消费）

## 参考资料

### 2026-09-22 新增（本批 5 个 demo 实际读过的来源）

- SQLAlchemy 2.0 官方文档 — Session Basics / State Management：<https://docs.sqlalchemy.org/en/20/orm/session_basics.html>
- SQLAlchemy 源码 `orm/identity.py` / `orm/unitofwork.py` / `orm/persistence.py`：<https://github.com/sqlalchemy/sqlalchemy>
- GORM 官方文档 — Session：<https://gorm.io/docs/session.html>
- PostgreSQL 18 官方文档 — INSERT（ON CONFLICT）：<https://www.postgresql.org/docs/18/sql-insert.html>
- SQLite 官方文档 — UPSERT：<https://www.sqlite.org/lang_upsert.html>
- PostgreSQL 18 官方文档 — 8.14. JSON Types：<https://www.postgresql.org/docs/18/datatype-json.html>
- PostgreSQL 18 官方文档 — 65.4. GIN Indexes：<https://www.postgresql.org/docs/18/gin.html>
- PostgreSQL 18 官方文档 — 5.9. Row Security Policies：<https://www.postgresql.org/docs/18/ddl-rowsecurity.html>
- PostgreSQL 源码 `src/backend/rewrite/rowsecurity.c`：<https://github.com/postgres/postgres>
- apache/parquet-format — README.md / Encodings.md / LogicalTypes.md：<https://github.com/apache/parquet-format>

### 早期来源

- PostgreSQL 14.1 官方文档 — Using EXPLAIN：<https://www.postgresql.org/docs/14/using-explain.html>
- PostgreSQL 14.1 官方文档 — LIMIT and OFFSET：<https://www.postgresql.org/docs/14/queries-limit.html>
- SQLAlchemy 2.0 官方文档 — Relationships / Loading Techniques：<https://docs.sqlalchemy.org/en/20/orm/relationships.html>
- HikariCP 官方 wiki — About Pool Sizing：<https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing>
- Redis 源码 — `src/dict.c` / `src/t_zset.c` / `src/server.h`：<https://github.com/redis/redis>
