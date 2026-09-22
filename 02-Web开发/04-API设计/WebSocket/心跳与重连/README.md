# 610 · WebSocket 心跳与重连

> 目标：把 WebSocket 长连接最容易出事的一段——**连接还活着吗**和**断了怎么连回来**——拆成可验证的四层：控制帧硬约束、Ping/Pong 语义、关闭握手状态机、重连退避。
> 多语言：Python（可运行 + 断言自检）／Go（人工审查 + 结构校验，本机无 Go 工具链）。

## 目录

```
python/  frames.py（控制帧 / Close body / 状态码治理）
         close.py（关闭握手状态机）
         heartbeat.py（Ping/Pong 账本）
         reconnect.py（重连策略 + 退避）
         main.py  selfcheck_ws.py  selfcheck_frames.py  selfcheck_lifecycle.py
go/      ws.go（控制帧 / Close body / 状态码）  hb.go（握手 + 心跳）
         backoff.go（退避 + 演示入口）
```

运行：

```bash
python python/main.py
python python/selfcheck_ws.py     # 245 断言全绿
go run go/ws.go go/hb.go go/backoff.go
```

## 一、为什么要在应用层做心跳

RFC 6455 §5.5.2 的 NOTE 一句话点明了 Ping 的两个用途：

> A Ping frame may serve either as a **keepalive** or as a means to **verify that the remote endpoint is still responsive**.

工程背景（非规范内容）：长连接中间往往隔着 NAT、负载均衡、防火墙，它们会对长时间没有流量的连接做老化回收；而一个「只收不发」的推送连接，在没有业务消息时就是完全静默的。所以必须主动制造流量（keepalive），并且要有办法确认对端真的还活着（responsiveness 探测）——**这两件事的目标不同**：keepalive 只需要有字节流动，探测需要能算出往返时延并判定超时。

## 二、控制帧的三条硬约束（§5.5 / §5.4）

| 约束 | 原文 | 违反后果 |
|---|---|---|
| 操作码最高位为 1 | "identified by opcodes where the most significant bit of the opcode is 1" | 那是数据帧 |
| 载荷 ≤ **125 字节** | "All control frames MUST have a payload length of 125 bytes or less" | 非法帧 |
| **不得分片** | "MUST NOT be fragmented" | 非法帧 |

已定义 0x8 Close / 0x9 Ping / 0xA Pong，0xB-0xF 保留给未来的控制帧。

配套的一条（§5.4）很有意思，RFC 还专门给了理由：

> Control frames MAY be injected in the middle of a fragmented message.
> NOTE: If control frames could not be interjected, the latency of a ping, for example, would be very long if behind a large message.

也就是说，**正在传一个 100 MB 的消息时，Ping 必须能插队**。这要求接收端的帧循环不能「先读完整个消息再处理下一帧」。

## 三、Ping / Pong 的四条语义（§5.5.2 / §5.5.3）

1. **收到 Ping 必须回 Pong**，唯一例外是此前已收到 Close：
   > "MUST send a Pong frame in response, unless it already received a Close frame."
2. **回显载荷必须逐字节一致**：
   > "must have identical 'Application data' as found in the message body of the Ping frame being replied to."
   这条是 Ping 能用来测 RTT 的前提——凭载荷才能把应答对回请求。所以 Ping 载荷**每次都要不同**（序号/时间戳），否则无法配对。
3. **对端可以只答最近的一个**：
   > "the endpoint MAY elect to send a Pong frame for only the most recently processed Ping frame."
   这是最容易写错的一条：不能假设「发了 N 个 Ping 就必然收到 N 个 Pong」。demo 里 `recv_pong` 命中一项时会把**更早的待应答项一并作废**并计入 `missed_pongs`，而不是让它们永久挂账。
4. **Pong 可以是主动的单向心跳**：
   > "A Pong frame MAY be sent unsolicited. This serves as a unidirectional heartbeat. A response to an unsolicited Pong frame is not expected."

第 4 条带来一个重要推论：**活性计时不能只认 Pong 的配对成功**。单向 Pong 同样证明对端活着，所以本 demo 用「最后一次收到**任意**帧」计时（工程口径，README 明确标注）。

## 四、Close 帧与状态码（§5.5.1 / §7.4）

Close 帧若有 body，**前 2 字节必须是网络字节序的无符号状态码**，其后可跟 UTF-8 原因串：

```
encode(1000, "bye") → 03 e8 62 79 65
                      ^^^^^ 状态码 ^^^^ "bye"
```

body 只有 1 字节是**非法**的（状态码必须占满 2 字节）；body 为空表示不携带状态码。

### 四个「不能用」要分清

| 类别 | 码 | 说明 |
|---|---|---|
| **MUST NOT 写入** | 1005 / 1006 / 1015 | §7.4.1 明说"MUST NOT be set as a status code in a Close control frame"。它们只用于**应用层表示**状态（没收到码 / 异常断开 / TLS 握手失败），不是可以发出去的值 |
| RFC 保留 | 1004 | 原文就写 "Reserved. The specific meaning might be defined in the future." |
| 区间保留未分配 | 1016–2999 | 区间归本协议与扩展，但 IANA 表里是 Unassigned |
| 私有区间 | 4000–4999 | 可用，但 "can't be registered" |

区间归属（§7.4.2）：

| 区间 | 归属 | 可注册 |
|---|---|---|
| 0–999 | 不使用 | — |
| 1000–2999 | 本协议、修订、扩展 | 否 |
| 3000–3999 | 库/框架/应用，**直接向 IANA 注册** | 是 |
| 4000–4999 | 私有用途 | 否（明说不可注册） |

IANA 注册表里几个 RFC 6455 之后新增的码，含义本身就指示了重试行为：**1012 Service Restart**、**1013 Try Again Later**、**1014 Bad Gateway**；以及 3000 Unauthorized / 3003 Forbidden / 3008 Timeout。

## 五、关闭握手状态机（§5.5.1）

```
open ──发出 Close──> closing ──收到 Close──> closed
  │                     ▲
  └──────收到 Close──────┘   （未发过 → MUST 回一个，typically echo 状态码）
```

三条常被写错的规则：

- 发出 Close 之后 **MUST NOT 再发任何数据帧**（`may_send_data()`）。
- 收到 Close 且自己**没发过** → **必须**回一个 Close，且原文说 typically 回显收到的状态码。
- 收发齐备后两端都要关 TCP，但两侧要求不同：**server MUST 立即关；client SHOULD 等 server 先关，但 MAY 随时关**。

实现上有个坑：状态不能只用单个枚举字段。「已发未收」和「已收未发」都叫 closing，但下一步动作完全不同——用单字段会把「收到 Close 后回一个」误判成「重复发送」。本 demo 用 `sent` / `received` 两个布尔量合成状态（`close.py`）。

双方同时发 Close 时，两边都收发齐备，直接进 closed。

## 六、重连：策略与算法是两件事

要分清楚，**这两层来自不同的地方**：

1. **收到哪个码要不要重连** —— RFC 6455 **没有规定重连策略**，它只定义关闭码的语义。所以这是工程策略，本 demo 依据 §7.4.1 各码的文字描述给出：

| 码 | 语义 | 重连 | 理由 |
|---|---|---|---|
| 1000 | 目的已达 | 否 | 正常结束 |
| 1001 | 端点要离开 | 是 | 服务器重启、页面跳转 |
| 1002 / 1003 / 1007 | 协议错误 / 类型不支持 / 载荷非法 | 否 | 再连一次只会再错一次 |
| 1008 / 1009 | 违反策略 / 消息过大 | 否 | 消息本身的问题 |
| 1010 | 要求的扩展没谈成 | 否 | 谈不成的扩展再连也谈不成 |
| 1011 | 服务器内部意外 | **是** | 原文 "unexpected condition"，是瞬时的 |
| 1012 / 1013 / 1014 | Service Restart / Try Again Later / Bad Gateway | **是** | 名字本身就要求重试 |
| 1015 | TLS 握手失败 | 否 | 证书问题不会自己好 |
| 未知码 | — | 是 | 保守，避免永久停摆 |

2. **重连间隔怎么算** —— 取自 gRPC 官方 `doc/connection-backoff.md`，一份有明确数值的公开规范：

```
INITIAL_BACKOFF = 1s      MULTIPLIER = 1.6      MAX_BACKOFF = 120s
JITTER = 0.2              MIN_CONNECT_TIMEOUT = 20s
```

算法是先算等待、后推进游标：

```
current_backoff = Min(current_backoff * MULTIPLIER, MAX_BACKOFF)
current_deadline = now() + current_backoff
                 + UniformRandom(-JITTER * current_backoff, +JITTER * current_backoff)
```

两个要点：

- **Jitter 是硬性要求**。原文说替代实现「必须让同时开始的退避发散，且不得比该算法更频繁地尝试连接」。没有 jitter 会让所有客户端在同一时刻重试，制造雷同的惊群。
- **退避必须能重置**，否则「新连接」和「断线重连」行为不一致。gRPC 选在收到 SETTINGS 帧时重置；WebSocket 的对应点是握手完成且双向已有帧流通（本 demo 取「收到第一个数据帧或 Pong」作为确认点，属工程映射，规范未规定）。

## 七、与 HTTP 轮询的对照

| 维度 | WebSocket 长连接 | HTTP 轮询 |
|---|---|---|
| 活性探测 | 应用帧 Ping/Pong，可测 RTT | 每次请求天然是一次探测 |
| 空闲成本 | 需要主动 keepalive | 无连接，无成本 |
| 断线感知 | 依赖超时判定，有延迟 | 下一次请求即失败 |
| 关闭语义 | 双向 Close 握手 + 状态码 | 无 |
| 重连 | 需要退避策略（无规范） | 天然是下一次请求 |

## 参考（本轮实际读取）

- RFC 6455 §5.4 / §5.5 / §5.5.1 / §5.5.2 / §5.5.3 / §7.4.1 / §7.4.2 — https://www.rfc-editor.org/rfc/rfc6455.txt （控制帧硬约束、Ping/Pong 语义、关闭握手、状态码区间）
- IANA WebSocket Close Code Number Registry — https://www.iana.org/assignments/websocket/close-code-number.csv （1012/1013/1014、3000/3003/3008、1016-2999 Unassigned）
- gRPC Connection Backoff Protocol — https://github.com/grpc/grpc/blob/master/doc/connection-backoff.md （五个参数与算法、jitter 与重置要求）
