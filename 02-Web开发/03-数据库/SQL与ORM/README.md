# SQL 与 ORM

## 已完成 demo

- [x] [../B+树索引/](../B+树索引/) — B+ 树索引原理（叶子兄弟链 + copy-up/push-up 分裂 + borrow/merge 重平衡 + bulk-load O(N)）；位于父级 03-数据库/ 而非本目录，因 B+ 树同时支撑 SQL 与 NoSQL 索引。
- [x] [N+1与预取策略/](./N+1与预取策略/) — 懒加载触发条件 + 5 种预取策略的语句数与结果等价性 + joined 去重前提 (Python + Go)
- [x] [EXPLAIN执行计划解读/](./EXPLAIN执行计划解读/) — 代价模型 arb. units + 节点树解析 + actual time/loops 换算 + rows 移除计数 (Python + Go)
- [x] [连接池调优/](./连接池调优/) — `core*2+spindles` 经验公式 + pool-locking 下界 `Tn*(Cm-1)+1` + 小池饱和曲线 (Python + Go)
- [x] [键集分页/](./键集分页/) — OFFSET 扫过行数 O(N) vs keyset 索引定位 O(log N) + 行值比较 + 翻页一致性 (Python + Go)

## 待研究

- [ ] 事务隔离级别
- [ ] SQLAlchemy 关系映射（见 N+1与预取策略/ 的 `_lazy` 演示：`relationship()` 默认 `lazy="select"`）
- [ ] GORM 链式 API
- [ ] 窗口函数与 CTE
- [ ] 查询推导式优化（子查询 → 半连接）
