# 逻辑复制与 CDC(PostgreSQL logical decoding + Debezium)

## 简介

**逻辑解码(logical decoding)** 把 PostgreSQL 的物理 WAL 解码成行级变更流(INSERT/UPDATE/DELETE),供外部消费者订阅;**CDC(Change Data Capture)** 是以此为基座的工程化管道(Debezium + Kafka Connect),把业务库变更实时投递到消息队列。本 demo 模拟:复制槽 WAL 持留、事件解码、replica identity、offset 断点续传、snapshot+增量衔接。

关键概念:WAL / replication slot(`confirmed_flush_lsn`)/ output plugin(pgoutput)/ REPLICA IDENTITY / offset / snapshot。

## 原理详解

**逻辑解码流水线**(postgresql.org/docs/current/logicaldecoding.html):

```
SQL INSERT/UPDATE/DELETE
   │ 写入
   ▼
WAL(物理记录,LSN 递增)
   │ walsender + output plugin 逻辑解码
   ▼
replication slot ── 事件流 ──> 消费者(Debezium connector)
   ▲                                │
   └── confirmed_flush_lsn ←── ACK ──┘   (ACK 前的 WAL 不得回收)
```

1. 每个逻辑复制槽持留 `confirmed_flush_lsn` 之后的 WAL —— 消费者停止 ACK 会导致磁盘被 WAL 撑爆(PG 文档 47.2.2)。
2. output plugin(如内置 `pgoutput`)把事务输出为 `begin` → 行变更 → `commit` 序列;行变更可拿到 `INSERT/UPDATE 的新行`;旧行是否可用取决于表级 `REPLICA IDENTITY`(无主键的表 `UPDATE/DELETE` 拿不到完整旧元组)。
3. 消费方式:流复制协议、SQL 接口(`pg_logical_slot_get_changes`)或自定义 output writer。

**Debezium / Kafka Connect**(debezium.io architecture):

- MySQL connector 用客户端库读 **binlog**;PostgreSQL connector 从**逻辑复制流**读取 —— 两条路线在 demo1/demo2 中分别模拟。
- 事件结构:`op`(c=create / u=update / d=delete / r=snapshot read)、`before` / `after`、`source`(lsn、slot、snapshot 标志)。
- **offset 持久化在 connector 侧**,重启后从断点续传,不丢不重。
- 部署形态:Kafka Connect 集群 / Debezium Server(Kinesis、Pub/Sub、Pulsar)/ 嵌入式 Engine(库形态)。
- 启动顺序:先**初始 snapshot**(op=r,全量导出当前状态)再无缝切增量流。

## 对比

| 维度 | 物理流复制 | 逻辑解码 |
| --- | --- | --- |
| 输出内容 | WAL 块级流(页镜像) | 行级变更(逻辑) |
| 消费者 | PG 从库 | 任意外部程序(Debezium 等) |
| 跨版本/异构 | 必须同版本同架构 | 可以跨大版本、异构下游 |
| 持留机制 | 从库 WAL 发送位点 | slot `confirmed_flush_lsn` |

## 环境

- Python ≥ 3.8;Go ≥ 1.21(静态审查)

## 运行方式

```bash
python3 python/main.py   # 输出 ALL 7 ... ASSERTIONS PASSED
go run go/main.go
```

## 关键代码片段

```python
# 槽持留:未 ACK 前 confirmed_lsn 落后 streamed_lsn → WAL 不得回收
retain = slot.retention_lsn()
assert retain < slot.streamed_lsn

# replica identity = nothing → UPDATE 事件的 old 元组为 null
old_tuple = old if ident == "default" else None
```

## 性能与边界

- 长事务会延迟逻辑解码的事件可见(commit 才发出);PG 14+ 支持大事务流式解码(streaming)。
- 槽是**孤儿 WAL 的头号来源**:消费者下线忘删槽 → `pg_wal` 膨胀(官方文档明确警告)。

## 注意事项与常见坑

- 无 PK 表做 CDC:UPDATE/DELETE 的 `before` 为 null → 下游无法精确对账;建议 `REPLICA IDENTITY FULL`(体积换正确性)。
- Debezium 重启后 offset 依赖 **Kafka Connect offsets topic**,清了 topic 会重复消费(需幂等 sink)。
- snapshot 与增量衔接点若选错(非一致性快照)会出现"漏了 snapshot 开始后、流开始前的变更"。

## 参考资料(实际阅读过的权威来源)

- [PostgreSQL Docs: Ch.47 Logical Decoding](https://www.postgresql.org/docs/current/logicaldecoding.html) — 槽/output plugin/replica identity/消费接口/流式解码
- [Debezium Docs: Architecture](https://debezium.io/documentation/reference/stable/architecture.html) — connector 读日志路线、Kafka Connect/Server/Engine 三形态、事件路由
- [MySQL 8.0 Manual: 19.2.1 Replication Formats](https://dev.mysql.com/doc/refman/8.0/en/replication-formats.html) — MySQL 侧 CDC 来源(binlog)的格式细节
