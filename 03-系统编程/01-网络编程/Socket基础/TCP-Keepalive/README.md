# TCP Keepalive(连接活性探测机制)

## 简介

- TCP Keepalive 是 TCP 协议层内置的"对端失联探测"机制:空闲一段时间后,TCP 自动向对端发送探针报文;若对端 N 次未响应,内核主动关闭连接。
- 解决的核心问题:**半开连接**——客户端拔网线 / 进程被 kill -9 / 主机宕机,服务端收不到 FIN,会永远以为连接活着。
- 关键概念:空闲时间(`TCP_KEEPIDLE`)、探针间隔(`TCP_KEEPINTVL`)、探针次数(`TCP_KEEPCNT`)三参数 + `SO_KEEPALIVE` 总开关;Linux 默认 2h + 75s × 9 ≈ 2h11m 才能判定对端死亡,本 demo 改成 3 + 2 × 3 ≈ 9s。
- 历史背景:RFC 1122 §4.2.3.6(1989)首次规范 keepalive,但明确写"协议默认禁用,应用按需启用"——因为 keepalive 占用带宽且可能被中间设备阻塞。

## 原理详解

### 三个参数与一个开关

| 选项 / 参数 | 层级 | 含义 | Linux 默认 | sysctl 名 |
| --- | --- | --- | --- | --- |
| `SO_KEEPALIVE` | `SOL_SOCKET` | 总开关;0=禁用,1=启用 | 0(默认关) | (无,纯 socket) |
| `TCP_KEEPIDLE` | `IPPROTO_TCP` | 连接空闲多久后开始发探针(秒) | 7200(2 小时) | `net.ipv4.tcp_keepalive_time` |
| `TCP_KEEPINTVL` | `IPPROTO_TCP` | 两次探针之间间隔(秒) | 75 | `net.ipv4.tcp_keepalive_intvl` |
| `TCP_KEEPCNT` | `IPPROTO_TCP` | 探针累计失败多少次后判死 | 9 | `net.ipv4.tcp_keepalive_probes` |

**总判定时间** = `TCP_KEEPIDLE` + `TCP_KEEPINTVL × TCP_KEEPCNT`。

### 探测报文流程

1. 连接空闲达到 `TCP_KEEPIDLE` 秒 → 内核自动插入一个 1 字节的 keepalive ACK 报文(payload 为已接收数据的 ACK)。
2. 收到对方正常 ACK → 重置空闲计时,继续等。
3. 没收到 ACK → 等 `TCP_KEEPINTVL` 秒,再发一次。
4. 累计 `TCP_KEEPCNT` 次未收到 ACK → 内核向应用返回 `ETIMEDOUT`(110),socket 关闭。
5. **探针报文是 TCP 头,无应用层 payload**,对端进程不可见(若对端 `SO_KEEPALIVE` 也开着,内核自动回 ACK,不打扰应用)。

### 关键 API

| C | Python | Go | 作用 |
| --- | --- | --- | --- |
| `setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &1, ...)` | `sock.setsockopt(SOL_SOCKET, SO_KEEPALIVE, 1)` | `(*net.TCPConn).SetKeepAlive(true)` | 开总开关 |
| `setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE, &N, ...)` | `sock.setsockopt(IPPROTO_TCP, TCP_KEEPIDLE, N)` | (syscall 直调,见 demo) | 设空闲阈值 |
| `setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &N, ...)` | `sock.setsockopt(IPPROTO_TCP, TCP_KEEPINTVL, N)` | (syscall 直调) | 设间隔 |
| `setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT, &N, ...)` | `sock.setsockopt(IPPROTO_TCP, TCP_KEEPCNT, N)` | (syscall 直调) | 设探针次数 |

### 底层发生了什么

- `SO_KEEPALIVE=1` → 内核在 `tcp_set_keepalive` 中置 `sk_flag |= TCP_KEEPALIVE`。
- 每次收到对端数据 → 内核重置 `keepalive_timer`(到期时间 = now + idle)。
- timer 到期 → `tcp_keepalive_timer` 把当前序列号前 1 字节作为 payload 发出;若对端 TCP 回 ACK,`ack_process` 重置 timer。
- timer 到期次数累计 → 超过 `TCP_KEEPCNT` → `tcp_write_err(sk, ETIMEDOUT)` → 下次应用 `read` 返回 `-1 / errno = ETIMEDOUT`,或 Go 返回 `*net.OpError` 包裹的 `os.ErrDeadlineExceeded`。

## 对比 / 选型

| 探测机制 | 层级 | 是否需要协议栈支持 | 优缺点 |
| --- | --- | --- | --- |
| TCP Keepalive(本 demo) | TCP 内核 | 是 | 零应用代码;但默认 2h 太长,需调小 |
| 应用层心跳(如 WebSocket ping/pong、gRPC keepalive) | 应用 | 否 | 灵活可配,但需双方实现 |
| TCP_USER_TIMEOUT(可选附加) | TCP 内核 | 是 | 数据未确认超时,与 keepalive 正交 |
| 短连接 + 重连 | 应用 | 否 | 简单粗暴,但握手开销大 |

> 实战建议:TCP Keepalive **+** 应用层心跳双保险;keepalive 兜底网络异常,应用层探活业务层异常(如 NAT 表过期)。

## 环境准备

- 操作系统:Linux(内核 ≥ 2.4,`TCP_KEEPIDLE` 等 ≥ 2.4);macOS 同接口但默认值不同
- 编译器:`gcc` ≥ 4.8
- Python:≥ 3.10(`socket.TCP_KEEPIDLE` 等 3.10 引入;更早版本用 `struct` 直调 syscall)
- Go:≥ 1.17(`SetKeepAliveConfig` 1.23;本 demo 用 `syscall.SetsockoptInt` 兼容性更好)

## 运行方式

### C

```bash
cd c
gcc -O2 -Wall -Wextra -o ka_demo main.c
./ka_demo server 9090 &  # 后台运行 server
./ka_demo client 127.0.0.1 9090
```

测试 dead-peer 探测:
1. server 启动后,client 连上
2. 在 client 进程运行时,`kill -STOP <client-pid>` 暂停 client(模拟拔网线)
3. ~9s 后 server 应打印 `recv error: Connection timed out`

### Python

```bash
cd python
python3 main.py server 9090 &  # 后台
python3 main.py client 127.0.0.1 9090
```

### Go

```bash
cd go
go run . server 9090 &
go run . client 127.0.0.1 9090
```

## 关键代码片段

C 版核心(完整见 `c/main.c`):

```c
static void enable_keepalive(int fd, int idle, int intvl, int cnt) {
    int on = 1;
    setsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &on, sizeof(on));
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPIDLE,  &idle, sizeof(idle));
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPINTVL, &intvl, sizeof(intvl));
    setsockopt(fd, IPPROTO_TCP, TCP_KEEPCNT,   &cnt,  sizeof(cnt));
}
```

Go 版通过 syscall 直调 IPPROTO_TCP(标准库 `SetKeepAlivePeriod` 只设 idle + intvl,无 probes):

```go
const (tcpKeepIdle = 4; tcpKeepIntvl = 5; tcpKeepCnt = 6)
raw.Control(func(fd uintptr) {
    syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepIdle, idle)
    syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepIntvl, intvl)
    syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepCnt, cnt)
})
```

## 性能与边界

- **探针间隔越短,网络流量越大**;每次探针一个 TCP 段(40 B),设 1s × N 长时间可能触发 ISP QoS 限速。
- **`TCP_KEEPIDLE` 改动后已建立的连接立即生效**(每次收到数据都重置 timer);不必 close 再 connect。
- **`SO_KEEPALIVE` 与 `select/poll/epoll`**:对端死亡时 fd 会变成"可读",`read` 返回 0 或错误——容易与正常 FIN 混淆,需查 `errno`。
- **NAT 中超时问题**:多数家用路由器 NAT 表项 ~5 分钟未活动就清除,因此 keepalive 间隔必须 < 5 分钟才能维持"穿透";企业防火墙可能更短(30s ~ 120s)。

## 注意事项与常见坑

- ❌ **用默认值**:2 小时太长,服务器场景通常设 idle=60, intvl=10, cnt=3 → 90s 内探测。
- ❌ **只在 server 端开**:理论上 client 也应开,但 server 是最需要知道的角色。
- ❌ **以为 `ETIMEDOUT` 就是 keepalive 触发**:也可能是 TCP 重传超时(`tcp_retries2` ≈ 13-15 次后);`getsockopt(TCP_INFO)` 看 `tcpi_total_retrans` 可辅助判定。
- ❌ **Windows 上选项不同**:`SIO_KEEPALIVE_VALS`(ioctl)传 idle+intvl on/off,无 `TCP_KEEPCNT`。
- ❌ **NAT 穿透误判**:本端公网 NAT 表过期,探针无法到达对端 → keepalive 误杀活连接;需要更短间隔(30s)+ 应用层 ping 二次验证。

## 参考资料(实际阅读过的权威来源)

- [Linux man page: tcp(7) — TCP_KEEPIDLE / TCP_KEEPINTVL / TCP_KEEPCNT / SO_KEEPALIVE](https://man7.org/linux/man-pages/man7/tcp.7.html) — Linux 内核对四参数的完整描述
- [Linux Documentation Project — TCP Keepalive HOWTO](https://tldp.org/HOWTO/TCP-Keepalive-HOWTO/index.html) — Linux 官方 keepalive 完整指南
- [RFC 1122 §4.2.3.6 — TCP Keepalive](https://datatracker.ietf.org/doc/html/rfc1122#section-4.2.3.6) — keepalive 协议层定义;原文写"OPTIONAL"且"default off"
- [systemd.socket(5) — KeepAlive / KeepAliveTimeSec / KeepAliveIntervalSec / KeepAliveProbes](https://manpages.ubuntu.com/manpages/jammy/en/man5/systemd.socket.5.html) — systemd 配置如何映射到四个 socket 选项
- [Go net package — TCPConn.SetKeepAlive](https://pkg.go.dev/net#TCPConn.SetKeepAlive) — Go 标准库 API
- [Python socket — TCP_KEEPIDLE 常量(3.10+)](https://docs.python.org/3/library/socket.html#constants) — Python 标准库 keepalive 常量
- [W. Richard Stevens — UNIX Network Programming Vol 1 §7.5 "TCP Keepalive"](https://www.unpbook.com/) — UNP 教科书对 keepalive 的总结与争议
- [StackOverflow — how to use setsockopt and getsockopt with KEEP_ALIVE](https://stackoverflow.com/questions/17740492/how-i-will-use-setsockopt-and-getsockopt-with-keep-alive-in-linux-c-programming) — 实操代码示例