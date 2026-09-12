# WAL 预写日志 + ARIES 恢复

## 简介

**Write-Ahead Logging (WAL)** 是关系型数据库保证 **ACID 中 A（Atomicity）和 D（Durability）** 的核心协议；**ARIES (Algorithms for Recovery and Isolation Exploiting Semantics)** 是 IBM 1992 年由 C. Mohan 等人在 ACM TODS 发表的具体实现算法，被 PostgreSQL、IBM Db2、Apache Derby、Microsoft SQL Server（部分）、InnoDB redo log 等广泛采用或借鉴。

适用场景：所有需要崩溃恢复的 OLTP 数据库；任何被 `COMMIT` 写过的数据必须能在系统崩溃（包括 kill -9、OS panic、磁盘故障）后被恢复。WAL 是数据库"持久性"的物理基础。

**关键概念清单**：
- **LSN (Log Sequence Number)**：每条日志记录单调递增的序号
- **WAL**：日志必须在对应数据页落盘**之前**刷到稳定存储
- **pageLSN**：每个数据页记录"我被哪条日志最后修改过"
- **prevLSN**：同一事务的日志记录用 prevLSN 形成链，便于反向撤销
- **CLR (Compensation Log Record)**：回滚时产生，是 redo-only 的补偿日志；用 `undoNextLSN` 跳过被补偿的原始日志
- **fuzzy checkpoint**：不需停机，写 begin→end 两段记录，end 中保存 tx_table + dirty_page_table 快照
- **三阶段恢复**：Analysis（决定 redo 起点）→ Redo（repeat history）→ Undo（回滚 loser）
- **Steal + No-Force**：Steal 允许未 commit dirty page 被刷盘；No-Force 允许 commit 时不强制刷 data page
- **master record**：磁盘上一个独立区域，记录"最新已 flush 的 checkpoint begin 位置"

**历史背景**：ARIES 论文发表于 1992 年 ACM TODS Vol.17, No.1, pp 94-162（IBM Research Report RJ6649 公开版本），作者 C. Mohan, D. Haderle, B. Lindsay, H. Pirahesh, P. Schwarz。论文解决了早期恢复算法的多个痛点：(1) 支持细粒度锁与部分回滚；(2) 引入 pageLSN 避免不必要 redo；(3) CLR + undoNextLSN 避免重复 undo；(4) fuzzy checkpoint 不停机。

## 原理详解

### 工作机制分步说明

1. **事务开始**：写一条 BEGIN 日志（含 txid）；初始化 transaction table 条目（state='U'，lastLSN=当前 LSN）。
2. **写数据**：UPDATE 前先 append 一条日志（含 pageID, offset, before_image, after_image），记录 prevLSN 指向同 tx 的前一条；然后才修改内存中的 page，并在 page 上记录 pageLSN。
3. **WAL 刷盘**：日志刷盘必须先于对应 data page 刷盘——即 `pageLSN ≤ flushedLSN` 才能刷 data page（这是 WAL 的核心数学约束）。
4. **事务提交**：写 COMMIT 日志并 `fsync`（保证持久），再写 END 日志。
5. **Fuzzy checkpoint**：周期性写 BEGIN_CHECKPOINT（即写 begin 记录本身作为时间锚）+ END_CHECKPOINT（含 tx_table / dirty_page_table 当前快照），最后更新 master record。
6. **崩溃恢复**：系统启动后读 master record 取到最近 checkpoint，从那开始三阶段恢复。

### ARIES 三阶段恢复

```
                  Recovery starts at master_lsn = "begin checkpoint LSN"
                                       │
                          ┌────────────▼────────────┐
                          │   Pass 1: ANALYSIS      │
                          │  重建 tx_table 与        │
                          │  dirty_page_table        │
                          │  (从 begin ckpt 到 log end) │
                          └────────────┬─────────────┘
                                       ▼
                          ┌─────────────────────────┐
                          │   Pass 2: REDO          │
                          │  从 min(recLSN) 开始     │
                          │  顺扫所有 REDO 记录      │
                          │  (含 CLR 与未 commit tx) │
                          │  → "Repeating History"  │
                          └────────────┬────────────┘
                                       ▼
                          ┌─────────────────────────┐
                          │   Pass 3: UNDO          │
                          │  反向扫 loser tx 的      │
                          │  lastLSN 写 CLR 抵消     │
                          └─────────────────────────┘
```

#### Analysis Pass
从 `master_lsn`（最近完整 checkpoint 的 begin LSN）开始扫描到 log end：
- 见到 BEGIN：在 tx_table 添加条目（state='U', lastLSN=新）
- 见到 UPDATE：tx_table 更新 lastLSN；dirty_page_table 若无该 page 则记录 recLSN（首次脏化）
- 见到 CLR：tx_table 更新 lastLSN（跳过被补偿的）
- 见到 COMMIT 或 END：把 state 改为 'C'，从损失集移除
- 见到 ABORT：state='A'，从损失集移除
- 见到 CKPT_END（再次）：通常是其他历史信息；以最后一个为准

最终：loss set = tx_table 中 state='U' 的事务；redo 起点 = dirty_page_table 中最小 recLSN。

#### Redo Pass
"Repeat History" 是 ARIES 的关键哲学：重新执行**所有**日志（含未提交的 loser）的 update 操作。判断某条 UPDATE 是否需重做的条件（任一满足则跳过）：
1. 受影响 page 不在 dirty_page_table 中（已经被写盘，且更早的 log 记录都已更新过它）
2. dirty_page_table 中的 recLSN > 当前 LSN
3. pageLSN ≥ LSN（page 已经是这个状态）

通过 pageLSN 检查使 redo 极少执行未真正需要的应用。

#### Undo Pass
loss set 中的事务即为要回滚的对象。ARIES 用 lastLSN 反向链，避免每次都从头扫；并写 CLR 防止重做时重复应用 undo。

### 核心数据结构

```
LogRecord layout:
┌──────────────────────────────────────────────────────────────┐
│ LSN │ PrevLSN │ TX │ Type │ PID │ Off │ Before │ After │... │
├──────────────────────────────────────────────────────────────┤
│  10 │    0    │ 41 │ UPD  │  0  │  1  │ "old"  │ "new" │   │
│  11 │   10    │ 41 │ UPD  │  1  │  5  │  ...   │  ...  │   │
│  12 │   11    │ 41 │ COMMIT│ -  │  -  │    -   │   -   │   │
│  13 │   12    │ 41 │ END  │  -  │  -  │    -   │   -   │   │
└──────────────────────────────────────────────────────────────┘

CLR (Compensation Log Record):
┌──────────────────────────────────────────────────────────────┐
│ LSN │ PrevLSN │ TX │ CLR   │ PID │ Off │ Before │ After │
│     │         │    │undoNext= 前一跳│                │
│     │         │    │ 不需要 before/redo 抵消值        │
└──────────────────────────────────────────────────────────────┘
```

### 与 Steal / No-Force / Force / No-Steal 的关系

| 策略组合 | 含义 | ARIES |
| --- | --- | --- |
| Steal | 未 commit dirty page 可被刷盘 | ✅ |
| No-Steal | dirty page 不能在 commit 前刷盘 | ❌ |
| Force | commit 时强制刷数据页 | ❌ |
| No-Force | commit 时 data page 不必刷盘 | ✅ |

ARIES 用 **Steal + No-Force** 提供最高灵活性（与最佳 page cache 复用率），但代价是恢复时必须 redo 所有 update（包括 loser）。

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
# python/main.py: ARIES undo pass (反向扫 + CLR 思想)
def recover(log, master_lsn):
    # Analysis / Redo (省略 — 已通过 demo 4 内联展示)
    losers = {tid for tid, info in tx_table.items() if info['state'] == 'U'}
    # Undo: 反向扫描
    for rec in reversed(log):
        if rec.type == LRT_UPDATE and rec.trans_id in losers:
            # 写 CLR 等价物: 把 after 还原为 before
            pages.setdefault(rec.page_id, {})[rec.offset] = rec.before
    return ...
```

```c
/* c/main.c: WAL 强制刷盘发生在 COMMIT 时 */
void db_commit(DB *d, int tx) {
    int prev = d->tx_last_lsn[tx];
    int idx = alloc_log(d, LR_COMMIT, tx, prev);
    d->tx_last_lsn[tx] = d->log[idx].lsn;
    d->disk_log_lsn = d->next_lsn - 1;  /* force fsync */
    ...
}
```

## 性能与边界

- **redo 起点**：由 dirty_page_table 中**最小** recLSN 决定，越小则 redo 越长。生产中通过 checkpoint + purge 收窄该窗口。
- **Log 量**：与 update 密度线性关系；Group Commit 可合并多个 tx 的 COMMIT 刷盘。
- **LSN 类型**：生产用 64-bit；本 demo 用 int 简化。
- **checkpoint 频率**：频繁 checkpoint 减小 redo 起点但增 IO；通常每几分钟至数十秒一次。
- **Clr 链长度**：loss set 大小决定 undo 工作量；通常是少数事务。

## 注意事项与常见坑

1. **WAL 的"刷盘先后"约束**：先刷 COMMIT log 再让 data page 刷盘；如果反序，崩溃后 COMMIT 的事务可能因 data page 没持久而丢失修改。
2. **避免不必要 redo**：ARIES 用 pageLSN 比较实现"无副作用 redo"。如果跳过 pageLSN，则 redo 全部日志代价极大。
3. **clR 必须 redo-only**：因为它本身代表一次撤销，撤销不能再次被撤销（否则会"负负得正"）。
4. **partial rollback**：ARIES 支持嵌套 top-level actions（用 dummy CLR），允许即使父事务 abort，子动作"如 create index"也可保留；这是 1992 论文 §6 论述的能力。
5. **二阶段提交 (2PC) 与 WAL**：跨节点 tx 时 prepare 阶段把所有页 force 到 disk；ARIES 提供的 redo 信息让 prepare 节点能从 prepare log 反推到 ready 状态。

## 对比：PostgreSQL vs MySQL vs SQL Server

| 项 | PostgreSQL | MySQL InnoDB | SQL Server |
| --- | --- | --- | --- |
| 实现 | PostgreSQL 自实现（简化版 ARIES） | redo log + undo log | ARIES 变体 |
| 默认策略 | Full Page Writes (FPW) | Group Commit | Steal + No-Force |
| Page size | 8 KB 默认（可调） | 16 KB 默认 | 8 KB |
| 物理粒度 | page-level redo | redo log 是 physical | page-level |

## 参考资料（实际阅读过的权威来源）

- [Apache Derby Logging & Recovery README](https://db.apache.org/derby/papers/recovery.html) — ARIES Overview、Repeating History、Undo/Redo Pass、Checkpoint LWM 概念；指向 Mohan 1992 原文。
- [ARIES Recovery Algorithm — Colorado State Ch16](https://www.cs.colostate.edu/~cs430dl/pages/more_examples/Ch16/Recovery.pdf) — 原始 ARIES 解析，含 Loss Set、ToUndo、Analysis/Redo/Undo 三阶段流程图。
- [Wisconsin Minibase project — ARIES recovery node2](https://research.cs.wisc.edu/coral/minibase/logMgr/report/node2.html) — 简化版 ARIES 教学实现，强调物理 redo 与 WAL 约束。
- [ARIES: A Transaction Recovery Method (UBC slides)](https://www.cs.ubc.ca/~rap/teaching/504/2014/slides/aries.pdf) — UBC 数据库课程 slide，含 log record 字段表与 dirty page table 图。
- [星环科技 — 数据库恢复子系统的常见技术方案对比](https://www.transwarp.cn/news/1712) — 中文资料概述 ARIES 三阶段流程、Steal+No-Force、Group Commit、Early Lock Release、Fuzzy Checkpoint 等关联优化。
