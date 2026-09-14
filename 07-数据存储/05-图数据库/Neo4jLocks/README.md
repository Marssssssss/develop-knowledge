# Neo4j 并发控制：读已提交隔离、丢失更新与锁管理器死锁检测

## 简介

Neo4j 默认隔离级别是 **READ_COMMITTED**：读不加锁、已提交即可见——性能好，但会放行**丢失更新、不可重复读、幻读**三类异常。本 demo 实现其并发控制内核：**实体级锁管理器（写锁互斥 + 锁持有到事务结束）+ 等待图（wait-for graph）死锁检测**，并复现官方文档的三个经典场景：100 并发 `+1` 的丢失更新、SET 直接依赖触发的自动写锁、交叉加锁死锁 vs 固定顺序防死锁。

## 原理详解

### 1. READ_COMMITTED 与丢失更新

官方 Operations Manual（Concurrent data access）："读取节点/关系的事务不会阻止另一个事务在第一个事务完成之前写入"。因此 `MATCH (n) SET n.prop = n.prop + 1` 由 100 个并发客户端执行时，**所有事务各自读到旧值再写回**，最坏情况下最终值低至 **1**（所有线程都在任何线程提交前完成读取）。

### 2. 自动写锁的条件：SET 右侧"直接依赖"被读属性

Cypher **只在 SET 右侧表达式直接依赖被读属性时**才在读取前自动获取写锁：

- `SET n.prop = n.prop + 1` → 自动写锁（读前锁定，防丢失更新）✅
- `SET n += {prop: n.prop + 1}`（映射字面量）→ 自动写锁 ✅
- 先 `WITH n.prop AS p` 再用 `p` 计算 → **无直接依赖，不加锁** ❌
- 循环依赖 `SET n += {propA: n.propB+1, propB: n.propA+1}` → **不加锁** ❌

复杂场景的官方 workaround：**哑属性技巧**——先 `SET n.dummy = true`（强制拿写锁）`REMOVE n.dummy`，再读 `n.prop` 计算。

### 3. 锁的粒度与获取表（官方表 2 摘录）

| 修改操作 | 获取的锁 |
| --- | --- |
| 创建/删除节点 | 该节点写锁 |
| 创建/删除关系 | 该关系 + **两端节点**写锁 |
| 更新属性 / 标签 | 该节点/关系写锁 |
| 密集节点（≥50 关系）上的关系增删 | **共享度锁**（shared degree lock）代替独占锁，提交期才取精确排他锁 |

密集节点优化：度数存在并发结构里，关系修改在事务期只取**粗粒度共享锁**、提交期取**精确排他锁**，让多个事务能同时给同一密集节点挂关系——这是 Neo4j 写吞吐的关键设计。

### 4. 死锁：等待图检测

两个事务各持一锁互等对方（T1 持 A 等 B、T2 持 B 等 A）→ 等待图出现**环**。Neo4j 内置死锁检测：检测到即以 `Neo.TransientError.Transaction.DeadlockDetected`（GQLSTATUS 50N05）**终止一个事务**，其余事务在其锁释放后继续。官方建议：**并发更新按固定顺序加锁**（先 A 后 B），或让单一线程做同类更新。

### 5. 实现要点：等待边必须保留

死锁检测的正确性依赖：**阻塞的事务在等待图中保留 `tx -> entity` 边**，直到拿到锁或被终止。若等待边随单次尝试失败即弹出，环永远不闭合，检测形同虚设（本 demo 实现时踩过的坑）。

## 环境

- Python 3.8+ / Go 1.18+，无第三方依赖。

## 运行方式

```bash
python locks.py
go run locks.go
```

按用户约定不实际运行，逻辑经人工代码审查。

## 关键代码

```python
# 阻塞时登记并保留等待边, 再查环
self._waiting[txid] = entity
if self._find_cycle(txid):
    raise DeadlockError(...)   # 受害者放弃等待, 事务被终止
# 等待图投影: 等资源的 tx -> 持有资源的 tx 集合
for holder in self._holders.get(entity, set()):
    if holder == start_tx: return True   # 回到起点: 成环
```

## 性能边界

| 项 | 边界 |
| --- | --- |
| 锁粒度 | 实体级（节点/关系），比页级/表级并发度高 |
| 死锁检测 | 每次阻塞时做 DFS 查环，O(V+E)，V/E = 等待事务/等待边 |
| 密集节点 | 共享度锁牺牲一点一致性时机换并发吞吐 |
| 锁超时 | `db.lock.acquisition.timeout`（默认 0 = 不超时） |

## 注意事项与常见坑

1. **不可重复读**：同一事务两次读同一属性可能不同值（官方示例 6）；官方建议"每个属性只读一次并保留该值"。
2. **索引扫描异常**：并发更新索引属性时，实体可能被扫描**多次或跳过**（丢失读取/重复读取），即使有唯一性约束也不例外。
3. **锁泄漏**：事务未 finish 则锁不释放（Java 3.0 手册强调 try-finally）；泄漏事务还会污染线程池里的嵌套事务。
4. **死锁≠回滚一切**：被终止的只是检测到的那个事务，其持有锁在结束时释放，**其余事务正常继续**，应用侧可重试被终止事务。

## 参考资料（实际读过）

1. Neo4j Operations Manual — Concurrent data access（隔离级别/丢失更新/锁表/死锁，本 README 主要依据）: https://neo4j.com/docs/operations-manual/current/database-internals/concurrent-data-access
2. Neo4j 中文文档镜像 — 并发数据访问（锁获取表中文版与死锁示例）: https://neo4j.ac.cn/docs/operations-manual/current/database-internals/concurrent-data-access/
3. Neo4j Java Reference 3.0 — Transaction management（ACID/flat nested transactions/锁释放语义）: https://neo4j.com/docs/java-reference/3.0/transactions/
4. Neo4j 知识库 — 共享锁与排他锁事务: https://neo4j.ac.cn/developer/kb/shared-vs-exclusive-transaction-locks/
