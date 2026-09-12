# MVCC 多版本并发控制

## 简介

MVCC（Multi-Version Concurrency Control，多版本并发控制）是 InnoDB / PostgreSQL / Oracle 等主流关系型数据库在 **不阻塞读者**的前提下提供事务隔离的核心机制。与 2PL（两阶段锁）不同，MVCC 通过为每一行保留多个历史版本，让读事务沿 undo log 链回溯到对其可见的版本，从而实现"读不阻塞写、写不阻塞读"。

适用场景：高并发 OLTP，特别是读写比为 10:1 以上的工作负载；需要一致快照的报表查询（`SELECT ... FOR UPDATE` 之外）。本 demo 重点是 **InnoDB 风格的 ReadView + undo log 链** 机制，并附 PostgreSQL 的 **xmin/xmax** 对照参考。

**关键概念清单**：
- **DB_TRX_ID**：每行记录上 6 字节隐藏字段，记录"最后修改此行的事务 id"
- **DB_ROLL_PTR**：7 字节回滚指针，指向 undo log 中存储的"上一个版本"
- **undo log**：单链表，存储被覆盖的旧版本（INSERT undo / UPDATE undo 两种）
- **ReadView（一致性快照）**：事务在做快照读时拍下的"视图"，由 low_water / high_water / active_ids 构成
- **purge**：后台线程清理 undo log 中"不再有任何快照需要"的老版本
- **可见性规则**：`trx_id == creator` / `trx_id < low_water` / `trx_id >= high_water` / 是否在 active_ids

**历史背景**：MVCC 思想可追溯到 1978 年 Bayer 与 2010s 的 InnoDB/PostgreSQL 实现。MySQL 5.6（2013）开始 MVCC 才成为 InnoDB 默认；PostgreSQL 自 8.x 起就采用纯 append-only + xmin/xmax 路径。CockroachDB、TiDB、YugabyteDB 等分布式 SQL 也基于 MVCC 变体。

## 原理详解

### 工作机制分步说明

1. **行存储**：每个 row 在用户字段之外隐藏 3 个字段：`DB_TRX_ID (6B)`、`DB_ROLL_PTR (7B)`、`DB_ROW_ID (6B, 仅无主键时生效)`。
2. **UPDATE 流程**：
   - 将当前行（old）的拷贝作为新 undo log entry 推入回滚段；
   - undo entry 的 `roll_ptr_next` 指向已有 undo log 链头（如果有）；
   - 更新行的 `roll_ptr` 指向新 entry；
   - 修改 `trx_id` 为当前事务 id，更新业务字段。
3. **SELECT（快照读）流程**：
   - 第一次 SELECT 在 RC 下创建新 ReadView，在 RR 下创建并后续复用；
   - 沿行 `roll_ptr → undo entry → entry.roll_ptr_next → ...` 的链向上走；
   - 对每个版本用可见性规则判断是否可见，找到第一个可见版本即可返回。
4. **purge 流程**：后台线程扫描 undo log，清理"没有任何事务快照需要"的老版本；否则 MVCC 看似无开销实际上 undo 表空间会无限增长。

### 核心数据结构（InnoDB 风格）

```
行 (clustered index record):
┌─────────────┬─────────────┬──────────────┐
│ DB_TRX_ID   │ DB_ROLL_PTR │ user fields  │
│ (6 bytes)   │ (7 bytes)   │              │
└─────────────┴─────────────┴──────────────┘
                │
                ▼  (指向 undo log 中最近一次 "old 版本")
undo log (rollback segment):
┌────────┬──────────┬───────┬────────┐  ┌────────┬──────────┬───────┬────────┐
│rp_next │ trx_id   │ value │deleted │  │rp_next │ trx_id   │ value │deleted │
│   ↓    │  ...     │  ...  │  ...   │  │  -1    │  ...     │  ...  │  ...   │
└────────┴──────────┴───────┴────────┘  └────────┴──────────┴───────┴────────┘
   (较新)                                    (链底, 基线)
```

### ReadView 可见性规则

```text
判断某版本 (trx_id) 是否对当前 ReadView 可见:
1. if trx_id == creator_id                    → 可见 (自己的)
2. if trx_id < low_water                      → 可见 (早于所有活跃事务, 已提交)
3. if trx_id >= high_water                    → 不可见 (快照后才开启)
4. if trx_id in active_ids                    → 不可见 (快照时还在运行)
5. else                                       → 可见 (不在 active = 已提交, < high_water)
```

引用 MySQL 9.7 Reference Manual §17.3："Internally, InnoDB adds three fields to each row stored in the database: A 6-byte DB_TRX_ID field ... A 7-byte DB_ROLL_PTR field ... A 6-byte DB_ROW_ID field ..."（实际阅读，见参考资料链接 1）。

### PostgreSQL 对照

| 字段 | InnoDB | PostgreSQL |
| --- | --- | --- |
| 创建者版本 | DB_TRX_ID | xmin |
| 删除者版本 | DB_ROLL_PTR → undo | xmax |
| 物理位置 | clustered index 主键行 | ctid（page+offset） |
| 历史存储 | rollback segment | heap 表中多版本共存 |
| 清理 | purge 线程 | VACUUM / autovacuum |
| RR 是否防 phantom | 防（next-key lock 配合） | 防（也比 SQL 标准严） |
| snapshot 取时机 | RR: 首 SELECT；RC: 每 SELECT | 句首基于 xmin |
| 可见性算法 | ReadView 4 条规则 | xmin/xmax + xid-snapshot |

### 4 隔离级别下 ReadView 行为对比

| 隔离级别 | ReadView 创建时机 | 可见性副作用 |
| --- | --- | --- |
| **READ UNCOMMITTED** | 不走 MVCC，直接拿最新版（dirty read） | 能看未提交 |
| **READ COMMITTED** | 每条 SELECT 都创建新 ReadView | 同一事务内多次 SELECT 可能看到不同结果 |
| **REPEATABLE READ**（InnoDB 默认） | 首 SELECT 创建，后续全部复用 | 同一事务内多次 SELECT 看到一致的快照；额外靠 next-key lock 防幻读 |
| **SERIALIZABLE** | 同 RR，但所有 SELECT 隐式变 SELECT ... LOCK IN SHARE MODE | 通过锁退化为 S2PL，等价 SS2PL |

## 环境准备

- 操作系统：任意（纯 stdlib）
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
# python/main.py: 可见性规则 (核心 4 条)
def _visible_rule(self, trx_id: int) -> bool:
    if trx_id == self.snapshot.creator_id: return True  # 自己写的
    if trx_id < self.snapshot.low_water:  return True  # 早于活跃事务集, 已提交
    if trx_id >= self.snapshot.high_water: return False # 快照后才开启
    return trx_id not in self.snapshot.active_ids      # 中间地带: 不在 active = 已提交

# UPDATE 流程: 老版本压入 undo log, roll_ptr 链接
def update(self, name, value):
    row = self.db.rows.get(name) or Row()
    self.db.undo_log.append(UndoEntry(roll_ptr_next=row.roll_ptr,
                                       trx_id=row.trx_id, value=row.value,
                                       deleted=row.deleted))
    row.roll_ptr = len(self.db.undo_log) - 1   # 新 row 指向最新 undo entry
    row.trx_id = self.id                       # 写入当前 tx
    row.value = value
```

## 性能与边界

- **空间成本**：每行 UPDATE 写入一个 undo entry；长事务会让 rollback segment 持续增长；可通过 `innodb_max_purge_lag` 节流 purge。
- **可见性判断成本**：`trx_id` 比较 O(1)，但沿 undo log 链回溯需 O(版本数)，长链场景最差可达数十次。
- **active_ids 列表**：MySQL 5.5 时代一锁遍历所有 active 事务；MySQL 5.6 引入 read-only optimization，只锁 rw 列表，scale 显著提升。
- **行大小**：InnoDB clustered index 每行额外 13 字节（6+7，DB_ROW_ID 仅在无主键时算）；secondary index 行不含隐藏字段，靠主键引用。

## 注意事项与常见坑

1. **不要无脑利用 MVCC 解决一切并发问题**：MVCC 处理"读不阻塞写"，但写-写仍需锁。InnoDB 的 RR 还有 next-key lock 才能防幻读（demo 简化版不演示）。
2. **长事务会让 undo 表空间爆**：MySQL 文档明文建议 "commit transactions regularly, including transactions that issue only consistent reads"（链接 1）。否则 rollback segment 涨到几十 GB 也是常见事故。
3. **trx_id wraparound**：postgreSQL `XID` 是 32-bit，InnoDB `DB_TRX_ID` 6 字节约 280 万亿，但都需要 purge 跟踪。InnoDB 还需 `innodb_purge_threads`。
4. **二级索引的特殊性**：MySQL 9.7 文档指出，secondary index 不内嵌 DB_TRX_ID，更新走 delete-mark + insert，最终 purge 时统一清理；二级索引命中可能涉及回 clustered index 取最新快照，ICP 优化部分条件下能避免该回表。
5. **READ COMMITTED 在生产中的取舍**：Oracle 默认就是 RC，原因是 RC 减少 undo chain 遍历；但 PG/MySQL 默认 RR/Snapshot，原因是 RC 在并发报表下无一致性视图。

## 参考资料（实际阅读过的权威来源）

- [MySQL 9.7 Reference Manual §17.3 InnoDB Multi-Versioning](https://dev.mysql.com/doc/refman/9.7/en/innodb-multi-versioning.html) — DB_TRX_ID/DB_ROLL_PTR/DB_ROW_ID 大小、insert/update undo log 区别、purge 机制、二级索引特殊性。
- [MySQL Server Dev — ReadView Class Reference](https://dev.mysql.com/doc/dev/mysql-server/latest/classReadView.html) — m_low_limit_id / m_up_limit_id / m_ids / m_low_limit_no 字段定义及函数语义。
- [Planet MySQL — Repeatable Read Isolation Level](https://planet.mysql.com/entry/?id=35825) — ReadView 两条规则的演进、MySQL 5.6 read-only 优化、`trx_sys_t::rw_trx_list` vs `ro_trx_list` 拆分。
- [Planet MySQL — InnoDB undo logging & history system](https://planet.mysql.com/entry/?id=673965) — READ UNCOMMITTED/COMMITTED/REPEATABLE READ 在 undo log 上的行为差异；长事务危害。
- [PostgreSQL Documentation — MVCC](https://www.postgresql.org/docs/current/mvcc.html) — xmin / xmax / cmin / cmax 与可见性规则
