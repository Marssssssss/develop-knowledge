# Redis Stream 邮件/奖励队列:消费组语义

> 系统邮件、离线奖励、补偿发放,本质都是**异步队列 + 至少一次交付**。
> Redis Stream 的消费组把这副骨架原生提供:'>' 只发新条目、服务端记账
> 谁拿了什么(pending)、XACK 收口。

## 1. 生产端:XADD 自动 ID

`XADD mails * title welcome` —— 传 `*` 由服务器生成
`<毫秒时间戳>-<序号>` 的 ID:**单调递增**,天然可作消费位点与去重键。

## 2. 消费端:XREADGROUP 的两个读法(官方页口径)

| 读法 | 语义 |
| --- | --- |
| `STREAMS key >` | 只读**从未投递给任何消费者**的新条目 |
| `STREAMS key <具体 ID>` | 读**本消费者**的 pending 历史(崩溃恢复入口) |

- 同组消费者分摊条目(官方例:A、B、C 三条,两个消费者分到 A、C 与 B),
  **互不重复**;
- "the server will remember that a given message was delivered to you"
  ——投递即入 **pending entries list(PEL)**;
- 消费者名首次出现**自动创建**,无需预注册。

## 3. 收口:XACK 与至少一次

- 处理完成 XACK → 条目离开该消费者的 pending;
- **不 ack 的永远留在 pending**——这就是"至少一次"交付的账本;
- 崩溃恢复:重启后用 ID `0` 重读自己 pending 重处理;长时间未 ack 的可
  XCLAIM 转给别的消费者接管;
- 代价:重发意味着**业务侧必须自持幂等**(重复投递可能发生)——
  正好接上本目录《幂等发放与 SET-NX》的判重设计。

## 自检

`python python/mailqueue.py` —— 4 项断言:自动 ID 格式与单调 /
'>' 分摊不重复 + 自动注册消费者 / XACK 收口 pending /
崩溃后 pending 不丢可接管。Go 侧 `go/mailqueue.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [Redis — XADD(* 自动 ID)](https://redis.io/commands/xadd/)
- [Redis — XREADGROUP(> 与 pending 语义,含 A/B/C 分摊例)](https://redis.io/commands/xreadgroup/)
