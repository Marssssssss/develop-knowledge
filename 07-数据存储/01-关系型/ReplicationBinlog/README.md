# 主从复制与 binlog(ROW/STATEMENT + GTID + 半同步)

## 简介

MySQL 复制 = 主库把变更写进 binlog,从库拉取并重放,使数据一致。本 demo 模拟三个核心机制:

- **复制格式**:SBR(记 SQL 语句)与 RBR(记行变更事件,MySQL 8.0 默认)
- **GTID**:全局事务标识 `source_id:transaction_id`,重放幂等
- **半同步复制**:主库 commit 返回前等待至少一个从库把事件落盘 relay log

关键概念:binlog 事件 / relay log / IO 线程 + SQL 线程三线程模型 / GTID set / 等待点(AFTER_SYNC vs AFTER_COMMIT)/ 超时降级。

## 原理详解

**复制链路(MySQL 官方手册 19.2.1)**:

```
主库事务提交 ──写──> binlog
    │ (dump 线程推送)
    ▼
从库 IO 线程 ──写──> relay log ──ACK──> 主库(半同步时)
    │ (SQL 线程 / coordinator)
    ▼
从库重放 ──> 从库数据
```

**复制格式对照**(来自 dev.mysql.com/doc/refman/8.0/en/replication-formats.html):

| 维度 | STATEMENT (SBR) | ROW (RBR,默认) |
| --- | --- | --- |
| binlog 内容 | SQL 语句文本 | 行变更事件(WriteRows/UpdateRows/DeleteRows) |
| binlog 体积 | 小 | 大(可 `binlog_row_image=MINIMAL` 缩减) |
| 正确性 | 非确定性函数(UUID()/NOW())、触发器有风险 | 精确到行,天然安全 |
| 存储过程/触发器 | 需记录执行上下文 | 复制的是结果,不受影响 |

- 官方明确:`binlog_format` 自 8.0.34 起被弃用,未来 ROW 将是唯一格式。
- **MIXED** 格式默认走 SBR,遇到不安全语句自动切 ROW。

**GTID(19.1.3)**:

- 格式 `source_id:transaction_id`(如 `3E11FA47-…:23`),GTID 之间用逗号连接为 GTID set。
- **幂等**:一旦某 GTID 在某服务器 commit,后续同 GTID 事务被直接忽略 —— 从库重放天然防重复。
- Auto-position:从库不再手工指定 `MASTER_LOG_FILE/POS`,用 GTID set 协商差集。官方建议配合 ROW 格式。

**半同步(19.4.10)**:

- 语义:主库**等 ≥1 个从库**把事务事件写入 relay log 并**刷盘**后才 commit 返回;不等全部从库、也不等从库执行完成。
- 等待点 `rpl_semi_sync_source_wait_point`:
  - `AFTER_SYNC`(8.0 默认):binlog 落盘后、存储引擎 commit 前等待 —— ACK 的事务必然已持久化在从库,主库崩溃不丢已 commit 事务。
  - `AFTER_COMMIT`:引擎 commit 后才等待 —— 客户端在其他会话可见数据后 ACK 才到,主库崩溃时可能"幻读"(数据已暴露但未同步)。
- **超时降级**:`rpl_semi_sync_master_timeout`(默认 10s)到期无 ACK → 自动回退异步;从库追上后再恢复半同步。
- 官方警告:半同步崩溃后 failover,旧主库可能含未 ACK 事务,不应复用为 source。

## 环境

- Python ≥ 3.8(纯标准库);Go ≥ 1.21(静态审查,本机无工具链)

## 运行方式

```bash
python3 python/main.py   # 输出 ALL ... ASSERTIONS PASSED
go run go/main.go        # 在有 Go 工具链的环境
```

## 关键代码片段

```python
# 半同步等待(对应 AFTER_SYNC 等待点):commit 返回前等 replica ACK
def _wait_ack(self, upto):
    deadline = time.monotonic() + self.timeout
    while time.monotonic() < deadline:
        if self.relay_acked >= upto:   # relay log 已落盘
            return
        time.sleep(0.001)
    self.degraded = True               # 超时 → 降级异步(官方文档行为)

# GTID 幂等重放(SQL 线程)
if ev.gtid in self.executed_gtid:
    continue
```

## 性能与边界

- 半同步每个事务至少多付出一次到从库的 TCP RTT(官方手册原话),适合近距离快速网络。
- RBR 对宽表 + 大批量 UPDATE 的 binlog 膨胀显著。
- GTID 限制:不支持 `CREATE TABLE ... SELECT` 等事务内含不安全操作(19.1.3.7)。

## 注意事项与常见坑

- **STATEMENT + `UUID()`** → 主从数据不一致;MIXED 会自动切 ROW。
- 半同步降级是**静默**的:必须监控 `Rpl_semi_sync_master_status`,否则降级后实际是异步,数据丢失窗口悄然打开。
- `AFTER_COMMIT` 下从库收到的未 ACK 事务在其他会话已可见 → failover 后"回滚已见数据"的幻读。
- 从库重放默认串行(SQL 线程),大事务会产生复制延迟(并行复制 LOGICAL_CLOCK 可缓解,超出本 demo 范围)。

## 参考资料(实际阅读过的权威来源)

- [MySQL 8.0 Manual: 19.2.1 Replication Formats](https://dev.mysql.com/doc/refman/8.0/en/replication-formats.html) — SBR/RBR/MIXED 定义、默认值与弃用说明
- [MySQL 8.0 Manual: 19.4.10 Semisynchronous Replication](https://dev.mysql.com/doc/refman/8.0/en/replication-semisync.html) — 半同步语义、AFTER_SYNC/AFTER_COMMIT、超时降级、崩溃恢复警告
- [MySQL 8.0 Manual: 19.1.3 Replication with GTIDs](https://dev.mysql.com/doc/refman/8.0/en/replication-gtids.html) — GTID 格式、幂等、auto-position、与 RBR 配合建议
