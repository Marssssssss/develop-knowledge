# TCP 连接建立的两条队列：backlog 与 SYN 队列

## 简介

`listen(fd, backlog)` 的 `backlog` 参数是服务端最容易被误解的参数之一：**它限制的不是
"半连接"，而是"已完成三次握手、等着被 `accept()` 拿走"的连接数**。搞错这一点，线上就会
出现"客户端超时、服务端日志里却什么都没有"的诡异故障。

关键概念：

- **半连接队列（SYN queue）**：存收到 SYN、回了 SYN+ACK、还在等最终 ACK 的连接（状态
  `SYN_RECV`），上限是 `net.ipv4.tcp_max_syn_backlog`。
- **全连接队列（accept queue）**：存已完成三次握手、等应用 `accept()` 的连接，上限是
  `min(listen backlog, net.core.somaxconn)`。
- **`somaxconn`**：内核级天花板。Linux 5.4 起默认 **4096**，更早是 **128**——这个默认值
  是很多"连接莫名丢弃"事故的根因。
- **SYN Cookie**：半连接队列溢出时的回退机制，把连接状态编码进 SYN+ACK 的序号里，从而
  不占队列。
- **`tcp_abort_on_overflow`**：全连接队列满时"丢弃等重传"还是"直接回 RST"的开关。

历史背景：Linux **2.2** 改变了 `backlog` 的语义——从"未完成连接数"改为"已完成连接数"
（man7 listen(2) 原文明确记录了这次变更）。更早的内核里，`somaxconn` 甚至是个硬编码
的 128。

## 原理详解

### 1. 两条队列的分工

```
客户端                     服务端内核                        应用
  │  ── SYN ────────────►  ┌──────────────┐
  │                        │ 半连接队列    │ 上限 tcp_max_syn_backlog
  │  ◄── SYN+ACK ──────────│ (SYN_RECV)   │
  │  ── ACK ─────────────► └──────┬───────┘
  │                               │ 握手完成，搬队列
  │                        ┌──────▼───────┐
  │                        │ 全连接队列    │ 上限 min(backlog, somaxconn)
  │                        │ (ESTABLISHED)│
  │                        └──────┬───────┘
  │                               │ accept()
  │                        ┌──────▼───────┐
  │                        │  应用套接字   │
```

两条队列**互相牵连但容量独立**：半连接队列满了会丢 SYN；全连接队列满了会让"本该搬进来"
的连接失败，而连接失败又会引发客户端重传 SYN，反过来给半连接队列加压。

### 2. 队列上限怎么算

| 队列 | 上限 | 备注 |
| --- | --- | --- |
| 全连接 | `min(backlog, net.core.somaxconn)` | **静默取小**。backlog 超了不报错，直接被截断 |
| 半连接 | `net.ipv4.tcp_max_syn_backlog` | 每监听器（per-listener）限制；低内存机器最小 128 |

man7 listen(2) 原文：「If the backlog argument is greater than the value in
`/proc/sys/net/core/somaxconn`, then it is silently capped to that value.」
所以**只改应用层 `listen(fd, 1024)` 是没用的**，必须同时调 `somaxconn`。

### 3. 溢出时的行为

**半连接队列溢出**：

- `tcp_syncookies = 1`（默认）→ 不再入队，改用 SYN Cookie（状态编码在序号里）；
  内核文档特别注明：syncookies 下 `tcp_max_syn_backlog` 的上限检查**不再严格适用**。
- `tcp_syncookies = 0` → 直接丢 SYN，客户端只能重传（`tcp_syn_retries`）。
- 典型信号：`dmesg` 出现 `Possible SYN flooding on port ...`。**注意这不一定是攻击**，
  合法突发流量也会触发。

**全连接队列溢出**（由 `tcp_abort_on_overflow` 决定）：

| 取值 | 动作 | 客户端观感 |
| --- | --- | --- |
| `0`（默认） | 静默忽略客户端的最终 ACK；服务端按 `tcp_synack_retries` 重传 SYN+ACK，客户端重发 ACK | 稍慢，但只要应用及时 `accept()` 就能**自愈** |
| `1` | 立刻回 RST | 立刻 `connection refused`，**失去自愈机会** |

内核文档明确建议：只有在"确知监听进程无法调优加速"时才开 `tcp_abort_on_overflow=1`，
否则会伤害客户端。

### 4. SYN Cookie 的代价

内核文档对 `tcp_syncookies` 的措辞非常严厉：

> "syncookies seriously violate TCP protocol, do not allow to use TCP extensions,
> can result in serious degradation of some services (f.e. SMTP relaying)"

并且明确「It MUST NOT be used to help highly loaded servers to stand against legal
connection rate」——**日志里出现 SYN flood 告警但实际是合法流量，说明服务器配置有严重
问题**（该调 `tcp_max_syn_backlog` / `tcp_synack_retries` / `tcp_abort_on_overflow`）。

### 5. 内存代价

内核文档：**一个 `SYN_RECV` 请求套接字约 304 字节**。所以 `tcp_max_syn_backlog = 65536`
大约占用 **19 MiB**；`= 128`（低内存最小值）只要 38 KiB。

## 环境准备

- 操作系统：Python / Go 版跨平台（离散事件模拟 + 平台相关的真实检查会自动跳过）；
  C 版的真实套接字检查段为 Linux 专属。
- 语言版本：Python 3.8+；gcc/clang；Go 1.21+。

## 运行方式

### Python（先跑这个，10 项断言）

```bash
python3 main.py
```

### C

```bash
gcc -O2 -Wall -Wextra -pedantic main.c -o backlog_demo
./backlog_demo
```

### Go

```bash
go run main.go
```

### 在真机上观察（Linux）

```bash
ss -lnt                                          # Recv-Q=当前全连接队列长度, Send-Q=队列上限
netstat -s | grep -i listen                       # listen queue overflow / SYNs dropped
nstat -az TcpExtListenOverflows TcpExtListenDrops
cat /proc/sys/net/core/somaxconn                  # 隐形天花板
cat /proc/sys/net/ipv4/tcp_max_syn_backlog
dmesg | grep -i 'SYN flooding'
```

复现溢出的最小实验：`listen(fd, 4)` + 把自己 `SIGSTOP` 十几秒，然后并发连 50 次。

## 关键代码片段

```c
/* 内核实际生效的全连接队列上限：backlog 与 somaxconn 取小（静默截断） */
static int accept_limit(const Listener *l) {
    return l->backlog < l->somaxconn ? l->backlog : l->somaxconn;
}

/* 收到 SYN：先看半连接队列，满了才轮到 syncookies */
static enum action on_syn(Listener *l, int client) {
    if (l->syn_q < l->tcp_max_syn_backlog) return ACT_SYNACK_SENT;
    if (l->syncookies)                    return ACT_SYNCOOKIE_SENT;   /* 不占队列 */
    return ACT_SYN_DROPPED;                                           /* 丢 SYN */
}

/* 收到最终 ACK：全连接队列满时由 abort_on_overflow 决定命运 */
static enum action on_final_ack(Listener *l, int client) {
    if (l->accept_q < accept_limit(l)) { l->accept_q++; return ACT_MOVED_TO_ACCEPT; }
    l->overflow++;
    if (l->abort_on_overflow) { l->rst_sent++; return ACT_RST; }       /* 快失败 */
    l->ignored[client] = 1;
    return ACT_ACK_IGNORED;                                           /* 等重传，可自愈 */
}
```

```python
def retry_delay(base, attempt):
    """真实 TCP 的重传是**指数退避**的（tcp_syn_retries / tcp_synack_retries：
    约 1s、2s、4s、8s、16s、32s）。用固定间隔重试是常见建模错误——它会让
    "自愈"几乎不可能发生，从而把"应用太慢"误判成"协议不自愈"。"""
    return base * (2 ** attempt)
```

## 性能与边界

本 demo 实测输出（Python 版）：

**应用消费速度 vs 全连接队列溢出**（`backlog = 4`，60 个连接）：

| accept 间隔 | 建连成功 | 溢出（ACK 被忽略） | 自愈成功 | RST | 最终 accept | 放弃 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 60 | 0 | 0 | 0 | 60 | 0 |
| 5 | 28 | 277 | 20 | 0 | 28 | 32 |
| 10 | 16 | 307 | 10 | 0 | 16 | 44 |
| 25 | 8 | 329 | 4 | 0 | 8 | 52 |
| 50 | 6 | 333 | 2 | 0 | 6 | 54 |

**SYN 洪泛**（`tcp_max_syn_backlog = 4`，每 tick 到达 20 个 SYN）：

| tcp_syncookies | 处理 SYN | 丢弃 | 发 SYN Cookie | 半连接队列峰值 |
| --- | --- | --- | --- | --- |
| 0 | 2280 | **2216** | 0 | 4 |
| 1 | 400 | **0** | 384 | 4 |

**somaxconn 是隐形天花板**（应用两处都写 `backlog = 1024`，400 个连接）：

| `net.core.somaxconn` | 有效上限 | 队列峰值 | 溢出 |
| --- | --- | --- | --- |
| 128 | 128 | 128 | 1632 |
| 4096 | 1024 | 400 | 0 |

**`tcp_abort_on_overflow` 取舍**（`backlog = 4`，accept 间隔 4，30 个连接）：
`=0` 时溢出 92 次、自愈 18 个、最终 accept 27 个；`=1` 时只溢出 18 次（连接早失败，
不再重传加压）、RST 18 次、自愈 0 个、最终 accept 只有 12 个。

## 注意事项与常见坑

1. **`backlog` 不是"能同时服务的连接数"**：它只是"握完手还没被 accept 的缓冲区"。
   应用 accept 得快，队列就不会满；队列满说明**应用消费速度跟不上握手速率**。
2. **`somaxconn` 才是天花板**：`listen(fd, 1000000)` 不会报错，只会被静默截到
   `somaxconn`。上线前务必确认这个值（老发行版默认 128 太容易撞）。
3. **全连接队列溢出的表现极具误导性**：客户端 `SYN` 重传但握手已经完成，服务端业务日志
   里看不到任何请求 → 容易误去查带宽/负载。**先看 `ss -lnt` 的 `Recv-Q` 和
   `netstat -s | grep -i listen`**。
4. **别把 `tcp_abort_on_overflow=1` 当"优化"**：它只是把"慢"换成"快失败"，反而减少成功
   连接数（本 demo 实测 27 → 12）。
5. **别用 syncookies 扛合法流量**：内核文档明确反对。它禁用 TCP 扩展（窗口缩放、SACK、
   时间戳），会让高 RTT/大带宽场景严重降级。正确的做法是加大
   `tcp_max_syn_backlog` 并让应用 `accept()` 更快。
6. **建模时别用固定间隔重试**：真实重传是指数退避的。第一版模拟用固定 3 个 tick 重试，
   结果"自愈"完全没发生（5 次重试只覆盖 15 个 tick，而队列排空需要几百个 tick），
   差点得出"默认策略不自愈"的错误结论。
7. **退役的 `tcp_tw_recycle`**：它曾用来加速 TIME_WAIT 回收，但在 NAT 环境下会丢连接，
   Linux 4.12 起已移除；现在是 `tcp_tw_reuse`（默认 2，仅 loopback）。

## 参考资料（实际阅读过的权威来源）

- [listen(2) — Linux manual page](https://man7.org/linux/man-pages/man2/listen.2.html)
  — `backlog` 在 Linux 2.2 后的语义（完整的已建立连接队列）、`tcp_max_syn_backlog`、
  被 `somaxconn` 静默截断、syncookies 下上限失效、5.4 起默认 4096（此前 128）。
- [Linux 内核文档 ip-sysctl](https://docs.kernel.org/networking/ip-sysctl.html)
  — `tcp_abort_on_overflow`（默认 FALSE 与"仅在确知无法调优时启用"的建议）、
  `tcp_max_syn_backlog`（per-listener、每个 SYN_RECV 约 304 字节）、`tcp_syncookies`
  （"seriously violate TCP protocol"、"MUST NOT be used to help highly loaded
  servers"、置 2 可强制生成）、`somaxconn`（默认 4096，5.4 前 128）、`tcp_tw_reuse`。
- [tcp(7) — Linux manual page](https://man7.org/linux/man-pages/man7/tcp.7.html)
  — `tcp_syn_retries` / `tcp_synack_retries` / `tcp_max_orphans` 等连接建立相关参数。
