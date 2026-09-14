# 两段式提交 2PC / XA 事务

> 分布式事务的经典协议:**prepare 投票 + commit 决定**,最致命的弱点是 **blocking**。

## 一、简介

当一个业务事务跨越多个资源管理器(例如 PG 一份数据 + MySQL 一份数据 + 一个消息队列),需要保证要么全部 commit,要么全部 abort。**2PC(Two-Phase Commit)** 是 1978 年由 Jim Gray 提出的解法:加一个**协调者(Coordinator / Transaction Manager)**,分两阶段:

- **Phase 1 (Prepare / Vote)**:协调者向所有参与者发 PREPARE。参与者**写 redo log → 给 row 加锁 → 投 YES/NO**。投 YES 后不能单方面决定,必须等协调者最终命令。
- **Phase 2 (Commit / Abort)**:协调者根据所有投票写**决定日志(decision record)→ fsync**,然后向所有参与者广播 COMMIT 或 ABORT。

XA 是 X/Open 1980s 标准化这个协议的 C API。

## 二、原理详解

### 2.1 状态机

协调者:`INIT → WAITING → DECIDED → done`

参与者:`INIT → PREPARED → COMMITTED/ABORTED`,或停留在 `IN_DOUBT`(coordinator 丢了)。

### 2.2 Coordinator 日志(关键)

```
BEGIN TXN T1
PREPARE T1 → part_a YES
PREPARE T1 → part_b YES
fsync decision=COMMIT       <-- 关键 fsync 点;之后崩溃也能恢复
END TXN T1
```

协调者恢复时:replay 日志;
- 若 `decision=COMMIT` 已写 → 给所有 PREPARED 的参与者发 COMMIT
- 若 `decision=ABORT` 已写 → 给所有 PREPARED 发 ABORT
- 若没有任何决定 → **presumed-abort**(假设 ABORT)

### 2.3 The Blocking Problem

**如果协调者在 fsync decision 后、broadcast 前崩溃**,所有 YES 投票的参与者都**永远卡在 PREPARED**:
- 不能 commit:可能协调者实际决定是 ABORT
- 不能 abort:可能协调者实际决定是 COMMIT
- 持有的行锁一直占着,阻塞其他事务

Gray 称之为 2PC 的"根本弱点":**它是一个 CP(consistency / partition-tolerance)协议**,牺牲可用性换原子性。

### 2.4 三段式提交 3PC

为缓解 blocking,3PC 加 PRE-COMMIT 阶段:协调者先广播 PRE-COMMIT,参与者收到后知道"决定一定是 COMMIT",即使协调者丢了也能完成。**但 3PC 在网络分区下不能保证原子性**(Skeen 1982 证明),实际很少用。

### 2.5 替代方案:不是 XA 怎么办?

- **Saga**:每个服务本地事务 + 补偿事务。**长流程可用,不持有跨服务锁**。
- **Outbox pattern**:业务写库 + 同表写消息,外部 CDC 转发。**数据库 + MQ 原子化**。
- **Transactional Kafka / Pulsar**:把原子性下沉到消息层(读未提交消息 → 提交 offset)。
- **Spanner / CockroachDB**:同集群内仍用 2PC,但用 Paxos 复制的 coordinator + bounded latency。

## 三、对比矩阵

| 协议 | 协调者恢复 | 跨网络分区 | 性能 | 适用 |
| --- | --- | --- | --- | --- |
| **2PC / XA** | log replay | blocking | 4 RTT | 同集群多 RM |
| **3PC** | log replay | 非阻塞但需完美失败检测 | 6 RTT | 几乎不用 |
| **Paxos Commit** | 多数派 | 非阻塞 | 4 RTT + Quorum | 跨 DC |
| **Saga** | N/A | 终态一致 | 1 RTT/step | 跨服务长事务 |

## 四、运行方式

```bash
cd 07-数据存储/01-关系型/TwoPhaseCommit/
python twopc.py
# 4 scenarios:
#   1. happy path (all YES → COMMIT)
#   2. one NO vote → ABORT
#   3. coordinator crash after fsync → IN_DOUBT → recovery
#   4. XA-style API

go run twopc.go
```

## 五、关键代码

`twopc.py` 的 coordinator 状态机:

```python
def coordinator_decide(c, parts, txn_id, vote_ok):
    decision = "COMMIT" if vote_ok else "ABORT"
    c.decision = decision
    c.log.append(f"[{c.name}] fsync decision={decision}")
    if c.crash_after_decision:
        # BLOCKING: parts stuck in PREPARED
        c.state = CoordState.CRASHED
        return
    for p in parts:
        if p.state == PartState.PREPARED:
            if decision == "COMMIT": participant_commit(p, txn_id)
            else: participant_abort(p, txn_id)
```

## 六、性能边界

- **2 RTT**:prepare + commit;每段含 1 次 fsync
- **Lock duration**:整个协议期间(= 2 RTT + 2 fsync ~ 4–20 ms 本地,~100–400 ms 跨 DC)
- **Coordinator 故障恢复**:从最近 checkpoint replay 日志;replay 时间 = O(已决定事务数 × log 行大小)

## 七、注意事项与常见坑

1. **不要跨服务 XA**:HTTP 微服务不能用 2PC 锁(跨服务 RTT 太长,任意 partition 都会拖垮系统)。用 Saga。
2. **PostgreSQL `PREPARE TRANSACTION`**:必须允许(默认 `max_prepared_transactions = 0`)。两阶段之间外部可见 PREPARED 数据 + 锁。
3. **MySQL `XA START/END/PREPARE/COMMIT`**:InnoDB 支持,XA recovery 启动时自动跑。
4. **协调者单点**:必须用 Raft/Paxos 复制;否则协调者机器掉电,所有 in-doubt 卡死。
5. **Heuristic decisions**(XA 规范):允许参与者单方面 commit/abort 来"逃脱卡死";**会破坏原子性,仅作灾难逃生口**。

## 八、参考资料

实际读过的权威链接:

1. "Two-Phase Commit (2PC)" — Software System Design:https://softwaresystemdesign.com/distributed-transactions-and-reliability/two-phase-commit/  *(状态机 + 阻塞 + 何时用 2PC vs Saga)*
2. "Designing Data-Intensive Applications" Ch. 9 — Martin Kleppmann:https://dataintensive.net/  *(Consensus vs Atomic Commit + 2PC 阻塞分析)*
3. Wikipedia "Two-phase commit protocol":https://en.wikipedia.org/wiki/Two-phase_commit_protocol  *(协议消息流 + presumed-abort/presumed-commit 优化)*
4. "MySQL Distributed Transactions: 2PC and SAGA Patterns":https://shayne007.github.io/2025/06/08/MySQL-Distributed-Transactions-2PC-and-SAGA-Patterns/  *(XA 语法 + 协调者 Python 实现)*
5. "Two-Phase Commit (2PC)" — Sharief 综述:http://sharief007.github.io/dev-docs/design-concepts/consensus/two-phase-commit/  *(in-doubt 阻塞 + XA 限制 + Saga 替代)*
6. "Designing Data-Intensive-Applications Note 17":https://umiao.github.io/2024/04/27/Designing-Data-Intensive-Applications-Note-17  *(3PC 不可行证明 + X/Open XA C API)*
7. Gray 1978 "Notes on Database Operating Systems":https://research.microsoft.com/en-us/um/people/gray/papers/DBOS.pdf  *(2PC 原始论文)*