# 两阶段锁 (2PL) 与隔离级别

## 简介

**Two-Phase Locking (2PL)** 是 1976 年由 Eswaran 等人提出的**悲观**并发控制协议，是关系型数据库保证 **可串行化（serializability）** 最经典的协议；与 MVCC 路线形成鲜明对比。2PL 通过把所有锁的获取和释放严格按时间划成两阶段——**Growing (成长)** 阶段只能获取锁、**Shrinking (收缩)** 阶段只能释放锁——自动保证事务的 conflict-serializability。

适用场景：商用 RDBMS 仍部分用锁（如 SQL Server 默认 2PL，PostgreSQL 用 SI/MVCC + 2PL 复合）。本 demo 重点是 **S/X 锁兼容性矩阵 + Growing/Shrinking 阶段 + 死锁检测（waits-for 图）**。

**关键概念清单**：
- **S-lock (Shared lock)**：多个事务可同时持同一资源的 S 锁；与 S 兼容、与 X 互斥
- **X-lock (Exclusive lock)**：独占锁；与任何锁都互斥
- **Growing phase**：事务开始到第一次 unlock 之间的阶段，只允许 acquire
- **Shrinking phase**：第一次 unlock 之后到事务结束，只允许 release
- **Strict 2PL (S2PL)**：X-lock 必须持有到 commit/abort 才能释放，防止 cascading abort
- **Strong Strict 2PL (SS2PL / Rigorous 2PL)**：所有锁（含 S）持有到 commit；等价 SERIALIZABLE 的 S2PL 变体
- **waits-for graph**：节点为事务，有向边 `Ti → Tj` 表示 Ti 在等 Tj 释放锁；DFS 找环即可识别死锁
- **4 种 SQL 隔离级别**：READ UNCOMMITTED / READ COMMITTED / REPEATABLE READ / SERIALIZABLE
- **3 类异常**：dirty read、non-repeatable read、phantom read

**历史背景**：2PL 协议由 K. P. Eswaran 等人在 1976 年的 CACM 论文 *"The notions of consistency and predicate locks in a database system"* 中提出。1979 年 Beeri & Bernstein 提出 SS2PL。1992 年 ARIES 论文进一步与 MVCC 路线集成（MVCC + 2PL 都可恢复）。至今仍是数据库教科书主流章节。

## 原理详解

### 工作机制分步说明

1. **事务开始**：进入 Growing phase；初始化事务状态为 active。
2. **读操作**：先对目标对象 `lock(R, S)`；按兼容性矩阵判定能否立即获取；不能则阻塞。
3. **写操作**：先 `lock(R, X)`；X 锁是排他，写前必获取。
4. **Growing 阶段**：单调累加锁；不允许释放任何锁。
5. **第一次解锁**：自动进入 Shrinking 阶段；不再允许获取新锁。
6. **commit / abort**：
   - 2PL 普通版：commit 时一次性释放所有锁（这之间算 Shrinking phase）；
   - **Strict 2PL**：X 锁必须持有到 commit/abort；普通 S 锁可在 Shrinking 内释放；
   - **Strong Strict 2PL**：所有 S+X 锁都持有到 commit/abort；保证 recoverability 且无 cascading abort。
7. **死锁检测**：DBMS 周期性扫描 waits-for 图；发现环则选 youngest victim（最新事务）abort 释放其锁。

### 锁兼容性矩阵

```text
       │ Holder 请求什么
  持有 │    S       X
 ─────┼──────────────────
 none  │  OK       OK
  S    │  OK       block
  X    │  block    block
```

FIFO 反饥饿变种：若队列前面有未 grant 的 X，新 S 必须排在它之后——避免 X 永远等不到。

### Schedule 分类层次（CMU 15-445）

```
Universal set of all schedules
   ⊆ View Serializable
       ⊆ Conflict Serializable       (2PL 保证; SS2PL ⊂ 这里)
           ⊆ Strong Strict 2PL (Rigorous 2PL)
               ⊆ Serial Schedules
```

实际生产数据库追求"直接生成 Strict 2PL schedule"以同时获得可串行化 + recoverability + 无 cascading abort。

### Growing / Shrinking 时间线示意

```
        Growing phase                Shrinking phase
 read ─┐
       ├── acquire locks              release locks ──┐
       │   (no release)              (no acquire)  │
       │                                            │
       ▼                                            ▼
 begin                                            commit / abort
───────────────────────────────────────────────────────────────────►
        ↑ 第一次 unlock = 阶段切换边界  ↑
```

### 死锁检测（Waits-for 图 + DFS）

```text
T1 → T2   T1 在等 T2 释放某资源
T2 → T3   T2 在等 T3
T3 → T1   形成环 → 死锁!
```

DFS 检测环：从每个 waiter 出发，若 DFS 路径中再次访问自身，则环。检出后从环里选 victim；通常选获取锁最少的事务回滚（成本最小）。本 demo 用"最大 id = 最年轻 = youngest"作为简化策略。

### 4 隔离级别 × 3 异常矩阵

| 隔离级别 \ 异常 | Dirty Read | Non-Repeatable Read | Phantom Read |
| --- | :---: | :---: | :---: |
| **READ UNCOMMITTED** | ✓ 可能 | ✓ 可能 | ✓ 可能 |
| **READ COMMITTED** | ✗ 不可能 | ✓ 可能 | ✓ 可能 |
| **REPEATABLE READ**（SQL 标准） | ✗ | ✗ | ✓ 可能 |
| **REPEATABLE READ**（MySQL InnoDB / PostgreSQL 实现） | ✗ | ✗ | ✗ 防幻读（超出标准） |
| **SERIALIZABLE** | ✗ | ✗ | ✗ |

### 与乐观/悲观对比

| 项 | 2PL（悲观） | OCC / MVCC（乐观） |
| --- | --- | --- |
| 并发度 | 中（锁竞争热点时差） | 高（读不阻写） |
| 死锁 | 有，需检测/预防 | 无 |
| 适用负载 | 写多读少，写竞争高 | 读多写少 |
| 调度方式 | DBMS 调度锁 | 应用控制 commit/abort 时机 |
| rollback 代价 | 中（需时撤销） | 高（validation 失败时整事务） |
| 代表实现 | SQL Server（默认） | PostgreSQL（默认）/ MySQL InnoDB（默认） |

## 环境准备

- 操作系统：任意（pure stdlib）
- 语言版本：Python 3.8+ / C11 (gcc 9+) / Go 1.21+
- 依赖：均使用标准库，无第三方依赖

## 运行方式

### Python
```bash
python3 python/main.py
```

### C
```bash
gcc -O2 -Wall -Wextra -std=c11 c/main.c -o main && ./main
```

### Go
```bash
cd go && go run main.go
```

## 关键代码片段

```python
# python/main.py: 兼容性矩阵 + FIFO 反饥饿
def _can_grant(self, lock, tx, mode):
    if mode == 'S':
        if lock.granted_x is not None: return False
        # FIFO: 若有 X 在前等待, S 也必须等
        if any(not r.granted and r.mode == 'X' for r in lock.waiting):
            return False
        return True
    # 'X' 模式下任何持有锁 + 任何等待者都拒
    if lock.granted_x is not None: return False
    if lock.granted_s: return False
    if lock.waiting: return False
    return True

# 死锁检测: DFS waits_for 图找环
def _has_cycle(self):
    visited = set()
    def dfs(node, path):
        for p in path:
            if p == node: return path[path.index(node):] + [node]
        if node in visited: return None
        visited.add(node)
        for nxt in self.waits_for.get(node, ()):
            r = dfs(nxt, path + [node])
            if r: return r
        return None
    ...
```

```c
/* c/main.c: 同一 tx 不能在 shrinking 阶段取新锁 */
bool db_lock(DB *db, int tx, ...) {
    if (!db->txs[tx].growing) return false;  /* 二阶段严格性 */
    ...
}
```

```go
// go/main.go: victim = 环中最大 id (最年轻事务)
victim := 0
for _, t := range cyc {
    if t > victim { victim = t }
}
```

## 性能与边界

- **死锁检测开销**：waits-for 图扫描 O(V²)，频率过高会成性能瓶颈；通常 1-10 秒一次。
- **锁表大小**：极端并发可达 10⁶ 个 row lock；DBMS 用**锁升级**(多个 row lock → 1 table lock)缓解。
- **优先级反转**：低优先级事务持锁阻碍高优先级事务；可加优先级继承（Priority Inheritance）。
- **2PL 隔离级别映射**：SERIALIZABLE 实际通常实现为 SS2PL；RR 在 SQL Server 用 S2PL，MySQL 用 next-key lock 模拟。

## 注意事项与常见坑

1. **不可在 Shrinking 阶段再加锁**：严格 2PL 规则。否则破坏可串行化保证。
2. **next-key lock 防幻读**：MySQL InnoDB 在 RR 下用 record lock + gap lock = next-key lock 才能防 phantom；纯 2PL S+X 不行。
3. **PostgreSQL RR 比 SQL 标准严**：SQL 标准允许 RR 出现 phantom，但 PG 的 RR 也防；这是好事但易与标准误解混淆。
4. **死锁检测的 victim 选择**：本 demo 用 max id = 最年轻；工业实现往往考虑"已做工作少 + undo 代价小"，结合 undo 段大小、回滚有副作用等。
5. **Phantom 仍可能发生在 RC/READ UNCOMMITTED**：即便用 RR+2PL，如果没有 range lock / next-key lock，也不能防。
6. **优先级反转**：2PL 路线下并未自动解决；OS 用 mutex 的 priority inheritance 仅作缓解。
7. **锁升级的成本**：读锁 → 写锁转换（upgrade）需要先把所有 S lock 重新升级；被 block 时升级顺序易产生饥饿。

## 参考资料（实际阅读过的权威来源）

- [CMU 15-445 Spring 2023 L16 Two-Phase Locking PDF](https://15445.courses.cs.cmu.edu/spring2023/notes/16-twophaselocking.pdf) — Schedule 完整层次、S/X-lock 兼容矩阵、Growing/Shrinking 定义、SS2PL vs S2PL、wait-for-graph 死锁检测。
- [Wikipedia / Wikiwand — Two-phase locking](http://wikiwand.dev/en/Two-phase_locking) — 2PL/S2PL/SS2PL/C2PL 变体对比表与各层级保证（conflict-serializability / view-serializability / deadlocks 等）。
- [Princeton COS418 Fall'25 — Concurrency Control (Precept 09)](https://www.cs.princeton.edu/courses/archive/fall25/cos418/docs/precept_09_concurrency_control.pptx) — 2PL + OCC + 2PC 三件套授课幻灯片，"死锁/cascading aborts/Strict 2PL 例子 vs Basic 2PL schedule"对比表。
- [Aerospike Blog — Serializable transactions and the price of getting concurrency right](https://aerospike.com/blog/serializable-transactions-distributed-systems/) — 2PL/MVCC/分布式序列化与 2PC 三种策略对比，含 next-key lock / predicate lock 防 phantom 段落。
- [Vlad Mihalcea — Write Skew anomaly: 2PL vs MVCC](https://vladmihalcea.com/write-skew-2pl-mvcc/) — 真实 SQL Server vs PostgreSQL/MySQL InnoDB 在 Serializable 下的 write skew 差异。

## 同源 demo

- **MVCC 多版本并发控制** — 与本 demo 形成路线对比：MVCC 让读不阻塞写、不需要 next-key lock 防 phantom；2PL 让读阻塞写、需要 lock manager。
- **WAL 预写日志** — 数据库"持久性"层；与"一致性"层的 2PL/MVCC 配合。
