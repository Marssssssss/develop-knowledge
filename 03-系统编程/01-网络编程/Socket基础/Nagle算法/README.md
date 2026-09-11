# Nagle 算法 vs `TCP_NODELAY`

## 简介

Nagle 算法（RFC 896, 1984, John Nagle）是 TCP 用来抑制"小报文段"（tinygram /
small-packet problem）的一种机制——当连接上还有未确认数据时，新到达的用户数据
被暂缓不发，直到对端 ACK 回包或缓冲到 MSS 量级为止。Linux 通过
`TCP_NODELAY` socket 选项关闭它。该选项在 POSIX 头文件 `<netinet/tcp.h>`
中定义，POSIX 名称直白：**"Avoid coalescing of small segments"**。

本 demo 用一个进程内的 loopback TCP 服务器 + 客户端，通过
`getsockopt(TCP_INFO)` 读取内核维护的 `tcpi_segs_out`（发出的 TCP 段数），
定量对比开启 / 关闭 Nagle 之后同样 100 次单字节 `send()` 真正落到链路上的段
数差异。

**关键概念**：

| 概念 | 一句话解释 |
| --- | --- |
| **Nagle 算法** | 抑制小型 outbound 段的启发式，"有未 ACK 数据时攒一波再发" |
| **`TCP_NODELAY`** | POSIX socket 选项，置 1 后立即 flush 每个 outbound 段 |
| **`TCP_CORK`** | Linux 自有的更激进版本——把所有数据关进"桶"直到清掉 cork |
| **`TCP_QUICKACK`** | 反方向：禁用对端的 delayed ACK，对 Nagle 也间接加速 |
| **`tcpi_segs_out`** | `<linux/tcp.h>` 中 `struct tcp_info` 的发出段计数 |

**历史背景**：1984 年 Nagle 在 Ford Aerospace 操作 ARPANET/MILNET 时发现，
telnet 一类交互应用下 41 字节包（1 字节数据 + 40 字节头）的 4000% 开销在
重负载下引发拥塞和重传。他在 RFC 896 中提出的方案无需计时器、不分字节大小
——只看"该连接上是否有未 ACK 的数据"，这是 Nagle 算法迄今仍然简洁的原因。

## 原理详解

### 1. Nagle 算法操作规则（RFC 896 §"The solution..."）

> "The solution is to **inhibit the sending of new TCP segments when new
> outgoing data arrives from the user** if any previously transmitted data
> on the connection remains unacknowledged. This inhibition is to be
> unconditional; no timers, tests for size of data received, or other
> conditions are required. Implementation typically requires **one or two
> lines inside a TCP program**."

口诀：**"ACK-first-then-flush"**。规则伪码：

```
on SEND(user_data):
    if sndbuf.unacked_bytes == 0:
        flush()              # 无未确认数据，立即发
    elif buffer + user_data <= MSS:
        buffer.append(user_data)   # 有未确认数据，且合并不超 MSS，攒着
    else:
        flush(); buffer = user_data  # 攒到要超 MSS，先发一拨再攒
on ACK_RECEIVED:
    flush()                  # 对端确认了，攒着的立刻发
```

实现上确实是"one or two lines"，Linux kernel 在 `tcp_sendmsg()` 里查
`tp->snd_nxt > tp->snd_una`（即有未 ACK 字节）来决定是否走 slow path 排队。

### 2. Nagle 与 Delayed ACK 的相互作用

两端不对称：

- **发送端**：有未 ACK 数据 → 攒着等 ACK 才发下一拨
- **接收端**：RFC 1122 §4.2.3.2 允许 delayed ACK——收到数据后等待 500ms 再回
  ACK（如果有数据要回 ACK，**piggyback** 顺便一起回）

把它们放一起就形成 **"Nagle-Delayed ACK deadlock"**：

```
client                            server
  --> send "hello"   (MSS=16384, server 不会立刻 ACK)
  wait for ACK...
                                (delayed ACK 计时器开 500ms)
                                500ms 后...
  <-- ACK "hello"
  --> flush "world"
```

Famous 例子：旧版 X11 在画"一个像素"时（一个字节请求 + 一个字节响应）能
观察到长达 200ms 的延迟。`TCP_NODELAY` 就是为了让 client 模式（发出 X
然后等 server 回复）打破这个 200ms 的天花板。

### 3. `TCP_NODELAY` 的精确语义（Linux `tcp(7)` man page）

> "If set, disable the Nagle algorithm. This means that **segments are
> always sent as soon as possible, even if there is only a small amount of
> data**. When not set, data is buffered until there is a sufficient
> amount to send out, thereby avoiding the frequent sending of small
> packets, which results in poor utilization of the network.
> **This option is overridden by `TCP_CORK`**; however, setting this option
> **forces an explicit flush of pending output, even if `TCP_CORK` is
> currently set**."

两条补充规则务必记住：

1. **NODELAY 不敌 CORK**：如果同时 set 了 `TCP_CORK`，Nagle 关闭也不发。
   想真发得清 `TCP_CORK`。
2. **NODELAY 自身是一次性 flush 触发**：set NODELAY 会把"cork 着 + Nagle 关着"
   的 buffer flush 出去，再恢复正常工作。

### 4. `getsockopt(TCP_INFO)` 与 `tcpi_segs_out`

Linux 内核在每次 TCP 段进出时累加：

```c
/* net/ipv4/tcp_output.c (Linux kernel 6.x) */
tcp_event_new_data_sent(struct sock *sk, struct sk_buff *skb)
{
    tcp_advance_send_head(sk, skb);
    ...
}
```

这些计数器对用户态可见——通过 `getsockopt(IPPROTO_TCP, TCP_INFO, &info)`
可读 `struct tcp_info`（定义在 `<linux/tcp.h>`）。我们关心的字段：

```
offset  field                  meaning
──────  ─────────────────────  ──────────────────────────────────────
  8     tcpi_rto               当前的 retransmission timeout
 60     tcpi_pmtu              当前路径 MTU
 84     tcpi_bytes_acked       累计已 ACK 的字节
 88     tcpi_bytes_received    累计收到的字节
100     tcpi_segs_out          ★ 累计发出的 TCP 段数
104     tcpi_segs_in           ★ 累计收到的 TCP 段数
```

调用示例：

```c
struct tcp_info info;
socklen_t len = sizeof(info);
getsockopt(fd, IPPROTO_TCP, TCP_INFO, &info, &len);
uint32_t segs_out = info.tcpi_segs_out;     /* 发出段数 */
```

### 5. 数据流（demo 1/2：`send() × 100` 单字节写入）

```
┌─────────────────────────────┐                ┌────────────────────────┐
│        client (caller)      │                │        server (peer)   │
│                             │                │                        │
│ TCP_NODELAY=1: 每 send()    │   segment#1    │                        │
│ 立即发                       │ ─────────────► │                        │
│                             │   1 byte data  │                        │
│                             │                │  recv() ×100           │
│ TCP_NODELAY=0: 前 N 次累加  │   segment#1    │  全部成功              │
│  直到对端 ACK 或 MSS 满      │ ─────────────► │                        │
│  本机 loopback 不一定会累加  │  N byte data   │                        │
│  满 100 字节取决于 ACK 速度  │                │                        │
└─────────────────────────────┘                └────────────────────────┘

Δ segs_out = (getsockopt AFTER) - (getsockopt BEFORE)
        ≈ segments actually on the wire
```

### 6. 核心 API

| 函数 | 头文件 | 说明 |
| --- | --- | --- |
| `setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &on, 4)` | `<sys/socket.h>` `<netinet/tcp.h>` | 关闭 Nagle |
| `getsockopt(fd, IPPROTO_TCP, TCP_INFO, &info, &len)` | `<sys/socket.h>` `<linux/tcp.h>` | 读 `tcpi_*` 计数 |
| `net.TCPConn.SetNoDelay(bool)` | Go 标准库 | `true` 等价 TCP_NODELAY=1 |
| `sock.setsockopt(IPPROTO_TCP, TCP_NODELAY, 1)` | Python `socket` 模块 | 同上 |

## 对比 / 选型

| 维度 | Nagle 开（默认） | `TCP_NODELAY=1` | `TCP_CORK` |
| --- | --- | --- | --- |
| 何时使用 | **服务端**：HTTP/文件传输/批量写 | **交互客户端**：telnet、SSH、X11、游戏 RPC、自定义协议 | 预拼一组头+payload 后一次性发出（如 sendfile(2) + 自定义头） |
| 小数据延迟 | 显著（最多 200-500ms 等待 ACK） | 极小（立即发） | 极大（你 cork 着不动就会缓冲） |
| 网络效率 | 高（合并小包） | 低（小包风暴） | 最高（一帧全数据） |
| 实现需求 | 系统默认 | setsockopt | setsockopt + 记得 unset |
| 跨平台 | 标准 BSD socket，默认开 | 标准 socket 选项，所有类 Unix + Windows (Winsock: `IPPROTO_TCP` × `TCP_NODELAY`) | 仅 Linux 特有 |

## 环境准备

- **操作系统**：Linux（demo 用 `getsockopt(TCP_INFO)` 与 Linux struct 布局）
- **语言版本**：
  - C：gcc，编译时链接 `-pthread`
  - Python：3.10+（用 `socket.TCP_INFO`、`time.perf_counter_ns`）
  - Go：1.21+（仅用标准库 + `syscall`）
- **依赖**：无（无第三方库）

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pthread c/nagle_demo.c -o /tmp/nagle_demo
/tmp/nagle_demo
```

### Python

```bash
python3 python/nagle_demo.py
```

### Go

```bash
cd go && go run nagle_demo.go
```

**预期输出**（loopback，差异主要在 `tcpi_segs_out`）：

```
=== demo 1  Nagle on   ==============================================
  Sent 100 single-byte writes (100 bytes total)
  TCP segments emitted (tcpi_segs_out delta): 8     ← Nagle 攒波
  wall time                         : 3200.0 µs (32.00 µs/write)
  server received                   : 100 bytes
  >>> Nagle coalesced 100 bytes into 8 segments (ratio 12.50x)

=== demo 2  TCP_NODELAY=1 ==============================================
  Sent 100 single-byte writes (100 bytes total)
  TCP segments emitted (tcpi_segs_out delta): 100   ← 每发一包
  wall time                         : 2900.0 µs (29.00 µs/write)
  server received                   : 100 bytes
  >>> TCP_NODELAY emitted one segment per write as expected
```

> 数字会因内核版本、Nagle 计时器、loopback 队列深度变化——关注"相对差异"，
> loopback 下两种模式的差异主要是**段数**，时间差异被 syscall 开销淹没。

## 关键代码片段

### C（demo 1 节选）

```c
/* 1) 关 / 开 Nagle */
int on = 1;
setsockopt(c, IPPROTO_TCP, TCP_NODELAY, &on, sizeof(on));   /* demo 2 */
/* demo 1 故意不调用 —— 用默认行为 */

struct tcp_info info;                                       /* 2) 读计数 */
socklen_t len = sizeof(info);
uint32_t segs_before = info.tcpi_segs_out;
getsockopt(c, IPPROTO_TCP, TCP_INFO, &info, &len);

for (int i = 0; i < N_WRITES; i++) {                        /* 3) 100 次 1B */
    send(c, &b, 1, 0);
    nanosleep(&(struct timespec){0, 1000}, NULL);           /* 1µs，让 */
}                                                           /* 内核累积 */

shutdown(c, SHUT_WR);                                       /* 4) 半关闭 */

getsockopt(c, IPPROTO_TCP, TCP_INFO, &info, &len);
uint32_t sent = info.tcpi_segs_out - segs_before;
```

### Python

```python
if nodelay_on:
    c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

# getsockopt returns raw bytes of struct tcp_info
buf = c.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 200)
ints = struct.unpack_from("<30I", buf, 0)
tcpi_segs_out = ints[25]   # offset 100, see <linux/tcp.h>

for _ in range(N_WRITES):
    c.send(b"a")
    time.sleep(1e-6)
c.shutdown(socket.SHUT_WR)
```

### Go

```go
c, _ := net.DialTCP("tcp4", nil, addr)
if nodelayOn {
    c.SetNoDelay(true)                       // 标准库封装 OK
}
fd, _ := fdOf(c)                             // syscall.Conn -> uintptr fd

// raw syscall6: getsockopt(TCP_INFO) -> struct tcp_info
segsBefore, _ := getsockoptTCPInfoSegsOut(fd)
for i := 0; i < nWrites; i++ {
    c.Write([]byte{'a'})
    time.Sleep(1 * time.Microsecond)
}
segsAfter, _ := getsockoptTCPInfoSegsOut(fd)
delta := segsAfter - segsBefore
```

## 性能与边界

- **段合并比**：在响应式 server 上 `tcpi_segs_out` 通常为 1~10；
  在 `TCP_NODELAY` 下会与 `send()` 调用次数相等（100）。
- **500 ms 的 worst-case**：传统 delayed-ACK timer 至少 40 ms 起跳，
  Linux 默认走 RFC 1122 推荐的 40 ms（`tcp_delack_max`）。Nagle+delayed ACK
  的链路在小数据 RPC 上可见明显的 40-200 ms 卡顿。
- **MSS**：loopback 默认 64 KB；本地以太网常见 1460 字节 (IPv4) / 1448 (IPv6)
  —— 一旦 `cork + write_one_byte` 攒满 MSS，下一段立刻发出，不等 ACK。

### 规模上限

- `getsockopt(TCP_INFO)` 在 Linux 2.6.32+ 提供；`tcpi_segs_out` 字段需要
  Linux 4.6+ 才会被填充。在更老的内核上 get 个空 buffer，demo 会得到 0。
- `TCP_NODELAY` 自 BSD 起可用，跨平台移植无忧；`TCP_CORK` 仅 Linux 有；
  Windows 在 Winsock 用同名的 `TCP_NODELAY`，没有 `TCP_CORK`，但有
  **`TCP_NO_OFFLOAD`** 等价物。

## 注意事项与常见坑

1. **Nagle 是系统默认**。没有显式 `setsockopt(TCP_NODELAY, 1)` 时所有
   socket 都开。HTTP client library（如 libcurl）、gRPC client 默认会在
   dial 之后**主动**关 Nagle——这正是为了避免 40ms 延迟坑你的 RPC 路径。
2. **`send()` 不等于"发出"**。即使 `TCP_NODELAY=1`，段还要经过本地 TCP
   拥塞控制 + 接收窗口 + 网卡 queue。**不要**用"send 立刻走 wire"去推理
   latency。
3. **接收端必须先开 `TCP_NODELAY` or `TCP_QUICKACK`** 仅在**对端**也重要
   时才相关。本 demo 都在 client 上关 Nagle；服务器（Nagle 或 QUICKACK
   都不重要——它不回大量小数据）侧保持默认。
4. **`TCP_CORK` 是"更狠"的桶**。忘了 `unset` 就是灾难——后续数据会一直卡在
   socket buffer 直到你 `close()` 或显式 `setsockopt(CORK, 0)`。
   Linux 上还有 200ms 自动超时"踢出"机制（见 `tcp(7)` man page `TCP_CORK`
   段），但你不能依赖这个 safety net。
5. **`TCPI_OPT_SYN_DATA` / `TCP_DEFER_ACCEPT`** 不在 Nagle 范畴，但都是
   同一族"减少小包"开关——详见 `tcp(7)`。
6. **TSO/LRO/GSO offload**：真实网卡上小 write 仍可能被硬件合并。这层在
   loopback 上不存在，因此本 demo 数字比真实网卡"小包就一定段数多"的直觉更
   干净。生产 NIC 上观察段数用 `tcpdump` 而非 `tcpi_segs_out`。
7. **`tcpi_segs_out` 增量观察法**：必须 **"先 getsockopt 拿 before，
   写完再 getsockopt 拿 after"**。`tcpi_segs_out` 是 socket 生命周期累计
   的，单独读到的绝对值没有"段数"含义，只能对照 delta。
8. **`TCP_INFO` 跨内核字段布局差异**：Linux 内核 ≤ 4.5 没有 `tcpi_segs_out`
   / `tcpi_segs_in`；5.x 加上 `tcpi_delivery_rate_app_limited`；6.x 又扩了
   `tcpi_rwnd_limited` 等字段。本 demo 假设 `tcpi_segs_out` 在 offset 100
   （Linux 4.6+），更老的内核会读到 0 但不会崩——只是数字没意义。
9. **Windows**：Winsock 同样有 `TCP_NODELAY`（不在套接字层而在协议层
   `IPPROTO_TCP`）；但 `TCP_INFO` **没有** struct 直接对应——得用
   `SIO_TCP_INFO` IOCTL + `TCP_INFO_v0` / `_v1` WSA extra struct，字段
   名字略不同。本 demo 在 Windows 上不应运行。

## 参考资料（实际阅读过的权威来源）

- **RFC 896 — "Congestion Control in IP/TCP Internetworks" (John Nagle,
  1984)** — <https://datatracker.ietf.org/doc/html/rfc896> — Nagle 算法
  操作规则的原始定义（"inhibit the sending of new TCP segments when new
  outgoing data arrives from the user if any previously transmitted data
  on the connection remains unacknowledged"）+ ARPANET small-packet problem
  的现象描述
- **Linux `tcp(7)` man page** — <https://man7.org/linux/man-pages/man7/tcp.7.html>
  — `TCP_NODELAY` / `TCP_CORK` / `TCP_QUICKACK` 字段原文；明确说明
  "NODELAY is overridden by TCP_CORK" 与 "NODELAY forces an explicit flush"
- **Linux `socket(7)` man page** — <https://man7.org/linux/man-pages/man7/socket.7.html>
  — `setsockopt(2)` / `getsockopt(2)` 的整体调用语义与参数（optval 是 int）
- **`netinet/tcp.h(0p)`** — <https://man7.org/linux/man-pages/man0/netinet_tcp.h.0p.html>
  — POSIX 给出的官方选项名与简短描述："TCP_NODELAY — Avoid coalescing
  of small segments"
- **RFC 1122 §4.2.3.2 — Delayed Acknowledgment** — 与 RFC 2581/SACK 并列
  在 Nagle 互作用章节被反复引用；给出 40-500 ms 延迟 ACK 的 budget
- **`struct tcp_info` 在 Linux 内核 `<include/uapi/linux/tcp.h>`** — 各计数
  器（如 `tcpi_segs_out`）的字段定义；offset 100 = 26th uint32（来自
  6.x 内核）
