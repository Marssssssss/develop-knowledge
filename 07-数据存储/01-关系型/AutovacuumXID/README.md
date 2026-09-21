# autovacuum 触发阈值与事务 ID 回绕

## 简介

PostgreSQL 的 `UPDATE` / `DELETE` 不原地改数据,而是留下一堆"死元组"。清理靠
`VACUUM`,而 `VACUUM` 该在什么时候跑,由 autovacuum 守护进程按**一条公式**决定。

这条公式有个反直觉的后果:**表越大,越不容易被清理**。10 亿行的表要攒够 1 亿个死元组
才会被 vacuum —— 这正是"大表膨胀"的根因之一,也是为什么大表必须单独调
`autovacuum_vacuum_scale_factor`。

另一条线更硬:事务 ID 是 32 位的,**用完会回绕**,回绕后新旧事务无法区分,数据直接
不可见。PostgreSQL 用**四条防线**层层拦截,最后一道是**拒绝分配新 XID**(只读救命模式)。

本 demo 把这两套机制从官方源码逐行转写出来,让每个数字都可验算。

## 原理详解

### 1. 触发阈值(转写自 `relation_needs_vacanalyze`)

```text
vacthresh    = autovacuum_vacuum_threshold + autovacuum_vacuum_scale_factor × reltuples
             = 50 + 0.2 × N                (超过 1 亿时被 autovacuum_vacuum_max_threshold 封顶)
vacinsthresh = autovacuum_vacuum_insert_threshold
             + autovacuum_vacuum_insert_scale_factor × reltuples × pcnt_unfrozen
             = 1000 + 0.2 × N × (1 − relallfrozen/relpages)
anlthresh    = autovacuum_analyze_threshold + autovacuum_analyze_scale_factor × reltuples
             = 50 + 0.1 × N
```

判据是**严格大于**(`vactuples > vathresh`),等于不触发。

| reltuples | vacthresh | anlthresh |
| --- | --- | --- |
| 0 | 50 | 50 |
| 100 | 70 | 60 |
| 10 000 | 2 050 | 1 050 |
| 1 000 000 | 200 050 | 100 050 |
| 1 000 000 000 | **100 000 000**(封顶) | 100 000 050 |

三个容易被忽略的细节:

1. **insert 阈值的分母是"未冻结比例"**(`pcnt_unfrozen`),不是全表。全冻结的静态表
   插入阈值退化成纯 `1000`,几乎不会因插入被 vacuum。
2. **`vacthresh` 有 1 亿的上限**(`autovacuum_vacuum_max_threshold`),否则超大表永远等不到。
3. **打分是"实际 / 阈值"的比值**,不是绝对值:候选表按 `max(各分量)` 排序,**最接近阈值的先跑**。

### 2. 防回绕是一条独立通路

```c
xidForceLimit = recentXid - freeze_max_age;
force_vacuum  = TransactionIdPrecedes(relfrozenxid, xidForceLimit);
if (force_vacuum) *dovacuum = true;      // 不看死元组
```

而且文档明确写了:**即使 autovacuum 被关掉,防回绕的 vacuum 也会启动**
("This will happen even if autovacuum is disabled")。

### 3. 事务 ID 的四条防线(转写自 `SetTransactionIdLimit`)

```text
xidWrapLimit = oldest_datfrozenxid + (MaxTransactionId >> 1)   // +2147483647
xidStopLimit = xidWrapLimit - 3 000 000                        // 剩 300 万
xidWarnLimit = xidWrapLimit - 100 000 000                      // 剩 1 亿
xidVacLimit  = oldest_datfrozenxid + autovacuum_freeze_max_age // +2 亿
```

| 限位 | 距回绕 | 行为 |
| --- | --- | --- |
| `xidVacLimit` | ~90.7% | 强制启动 autovacuum(每 65536 个事务发一次信号,别灌爆 postmaster) |
| `xidWarnLimit` | ~4.66% | `WARNING: database "..." must be vacuumed within N transactions` |
| `xidStopLimit` | ~0.14% | `ERROR: database is not accepting commands that assign new transaction IDs…` |
| `xidWrapLimit` | 0 | 数据已不可区分(回绕) |

为什么是"一半":源码注释说真正出事的地方是从最老可能存在的 XID **绕到一半**的位置 ——
因为 XID 比较是环形的,`2^31` 之后旧的就"变成未来"了。

剩余百分比的分母是 `MaxTransactionId / 2`(= 2147483647),**不是** `2^32`。

### 4. 静态表的强制 vacuum 间隔

文档:如果一张表从不被(为回收空间而)vacuum,那么 autovacuum 大约每
`autovacuum_freeze_max_age − vacuum_freeze_min_age` 个事务强制光顾它一次,默认
`2e8 − 5e7 = 1.5e8`。想拉长间隔:调大 `freeze_max_age` 或调小 `freeze_min_age`。

### 5. 普通 vacuum 与 aggressive vacuum

`VACUUM` 靠**可见性映射**跳过没有死元组的页,因此普通 vacuum **不会冻结所有老 XID**。
当"全可见但未全冻结"的页堆积起来时,就会触发一次 **aggressive vacuum**(扫描全部页),
由 `vacuum_freeze_table_age` 控制(有效上限是 `0.95 × autovacuum_freeze_max_age`)。

## 对比:三条 vacuum 通路

| 通路 | 触发量 | autovacuum 关闭时 |
| --- | --- | --- |
| 死元组 | `n_dead_tup > 50 + 0.2N` | **不跑** |
| 插入 | `ins_since_vacuum > 1000 + 0.2N×未冻结比例` | **不跑** |
| 防回绕 | `age(relfrozenxid) > 2e8` | **照跑** |

## 环境与运行

- Python 3(仅标准库)、Go 1.22(仅标准库)

```bash
python python/main.py
python python/selfcheck_vacuum.py   # 48 项断言
cd go && go run .
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/autovac.py` | `thresholds` / `unfrozen_ratio` / `scores` / `needs_vacanalyze` |
| `python/xid.py` | `limits` / `classify` / `remaining_pct` / `freeze_interval` |
| `go/autovac.go` `go/xid.go` `go/main.go` | 同一模型的 Go 转写 |

## 性能边界与注意事项

- **本机不跑 PostgreSQL**:本 demo 复现的是**判定公式**,不模拟 I/O 与代价延迟
  (`autovacuum_vacuum_cost_delay=2ms` / `cost_limit` 的节流效应不在模型里)。
- **大表一定要单独调参**:默认 20% 对 10 亿行的表意味着 1 亿死元组,通常已经太晚。
- **别关 autovacuum**:关掉只是关掉前两条通路,防回绕那一条会在最坏的时候(业务高峰)
  强行启动,代价更大。
- **`autovacuum_max_workers=3` 是全局的**:worker 会被长事务/锁阻塞,大表排队时会互相拖累。
- **长事务会顶住 `oldest_datfrozenxid`**:未提交的旧事务、未清的复制槽、未决的 prepared
  transaction 都会让冻结点无法推进(源码的 hint 里明确列了这三项)。
- **冻结不是"免费"的**:aggressive vacuum 要扫全表;`vacuum_freeze_min_age` 调太小会让
  刚冻结的页又被改写,白做功。

## 参考资料

- PostgreSQL 18 官方文档《Routine Vacuuming》(24.1)
  —— <https://www.postgresql.org/docs/current/routine-vacuuming.html>
  (visible map、aggressive vacuum、防回绕、`vacuum_freeze_table_age` 的 0.95 上限)
- PostgreSQL 18 官方文档《Automatic Vacuuming》GUC(19.10.2)
  —— <https://www.postgresql.org/docs/current/runtime-config-autovacuum.html>
  (50 / 0.2 / 1000 / 0.2 / 50 / 0.1 / 1e8 / 2e8 / 1min / 3 / 2ms 各默认值)
- PostgreSQL 源码 `src/backend/postmaster/autovacuum.c`
  (`relation_needs_vacanalyze` 的阈值与打分、`pcnt_unfrozen`、force_vacuum 分支)
- PostgreSQL 源码 `src/backend/access/transam/varsup.c`
  (`SetTransactionIdLimit` 的四条限位、`GetNewTransactionId` 的 65536 节流与 ERROR 文案)
  —— 均经 `cdn.jsdelivr.net/gh/postgres/postgres@master/...` 实读
