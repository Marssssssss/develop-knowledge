# SO_REUSEADDR / SO_REUSEPORT 与 TIME_WAIT 状态

## 简介

- `TIME_WAIT` 是 TCP 主动关闭方在收到对端 FIN-ACK 后进入的状态,持续 `2 × MSL`(Linux 内核写死 60s);它的存在是 TCP 可靠性的基石,不是 bug。
- `SO_REUSEADDR`(POSIX 1986):允许 bind 一个处于 `TIME_WAIT` 的地址,加速 server 重启。
- `SO_REUSEPORT`(Linux 3.9,2013):允许多个 socket 同时 bind 同一 `(addr, port)`,内核按四元组 hash 分发新连接,用于多进程负载分担。
- 关键概念:`TIME_WAIT` 与 `SO_REUSEADDR` 是"先有蛋后有鸡"的关系——前者是 TCP 协议层的稳定性设计,后者是 socket API 给应用的"绕过"开关。
- 历史背景:1981 年 RFC 793 提出 `TIME_WAIT`(当时称 `2MSL-WAIT`);RFC 793 §3.9 明确"主动关闭方必须等 2×MSL 才能释放端口";BSD/POSIX socket 在 1986 年加入 `SO_REUSEADDR`;2013 年 Linux 3.9 加 `SO_REUSEPORT`(照搬 BSD 内核)。

## 原理详解

### TIME_WAIT 状态机片段

```
ESTABLISHED ──主动 close()──► FIN_WAIT_1 ──收到 ACK──► FIN_WAIT_2 ──收到 FIN──► TIME_WAIT
                                                                            │
                                                              2*MSL 后(60s)│
                                                                            ▼
                                                                          CLOSED
```

为什么需要 `TIME_WAIT`:
1. **抗报文延迟/重传**:对端可能重传 FIN,本地需要能 ACK 回应;若已 CLOSED 会回 RST 让对方误判连接异常。
2. **抗旧报文混淆**:同一对 `(srcip, srcport, dstip, dstport, proto)` 五元组在 `TIME_WAIT` 内禁止被新连接复用,防止旧连接的迟到数据被新连接误收。

### SO_REUSEADDR

- Linux 语义:**新旧 socket 都设 `SO_REUSEADDR` 才允许覆盖**;BSD 只要新 socket 设即可。
- 不解决多 socket 同端口问题,仅解决"端口在 `TIME_WAIT` 时如何重启 server"。
- ⚠️ 不查清端口属于谁就重用,可能让攻击者在你重启的瞬间劫持端口(若服务用特权端口 < 1024 且攻击者有同 uid)——所以生产环境应该 bind 特定 IP 而非 `0.0.0.0`。

### SO_REUSEPORT(必须 Linux 3.9+)

- 允许多个 socket bind 完全相同的 `(addr, port)`,内核按 (srcip, srcport, dstip, dstport) hash 分发。
- 典型用途:Nginx worker 进程(每 worker 一个 listen fd,内核分发避免 `accept()` 锁);Linux 4.6+ 加 `SO_ATTACH_REUSEPORT_CBPF` 可自定义 hash 策略。
- 与 `SO_REUSEADDR` 的关键区别:`REUSEADDR` 只解决"复用 TIME_WAIT 端口",`REUSEPORT` 解决"同时存在多个监听 socket"。

### 关键 API

| C | Python | Go | 作用 |
| --- | --- | --- | --- |
| `setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &1, 4)` | `sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)` | `syscall.SetsockoptInt(fd, SOL_SOCKET, SO_REUSEADDR, 1)` | 允许 bind TIME_WAIT |
| `setsockopt(fd, SOL_SOCKET, SO_REUSEPORT, &1, 4)` | `sock.setsockopt(SOL_SOCKET, SO_REUSEPORT, 1)`(3.11+) | `syscall.SetsockoptInt(fd, SOL_SOCKET, 15, 1)` | 允许多 socket 同端口 |

### 底层发生了什么(Linux 内核)

- `bind` 路径 `inet_csk_get_port`:
  1. 先查 `tcp_hashinfo` 哈希表有没有相同 `(addr, port)` 的 socket 在 `TIME_WAIT`;
  2. 若有,检查新 socket 是否设 `SO_REUSEADDR` 且旧 socket 也设 → 通过;否则返回 `-EADDRINUSE`。
  3. 若用 `SO_REUSEPORT` 且新 socket 设了 → 直接通过(挂入 `inet_listen_hashbucket` 的 reuseport_group 链表)。
- `accept` 路径 `inet_csk_accept`:若有 `reuseport_group`,从组内按四元组 hash 选一个 socket 把新连接挂上去。

## 对比 / 选型

| 选项 | 解决场景 | 风险 |
| --- | --- | --- |
| `SO_REUSEADDR` | server 重启跳过 TIME_WAIT | 攻击者同 uid 可能劫持(<1024) |
| `SO_REUSEPORT` | 多进程负载分担(Nginx worker) | 连接分发不均(hash 偏斜);旧连接残留 |
| `tcp_tw_reuse=1`(sysctl) | **客户端**主动连接时复用 TIME_WAIT | Linux 4.12+ 已移除 `tcp_tw_recycle`(NAT 灾难) |
| `tcp_tw_reuse=2`(4.12+) | 同上 + 无时间戳限制 | — |
| bind 特定 IP 而非 `0.0.0.0` | 减少劫持面 | 失去通配监听 |

## 环境准备

- 操作系统:Linux 内核 ≥ 3.9(为 `SO_REUSEPORT`);macOS / BSD 行为差异较大,见 BSD `SO_REUSEPORT`(1995 起)
- 编译器:`gcc` ≥ 4.8
- Python:≥ 3.11(`socket.SO_REUSEPORT` 常量 3.11 引入;更早需走 `socket.setsockopt` 数字常量 15)
- Go:≥ 1.18
- 工具:`ss -tan`(iproute2,查看 socket 状态;`netstat -tan` 亦可)

## 运行方式

### C

```bash
cd c
gcc -O2 -Wall -Wextra -o reuse_demo main.c

# 实验 A:不开 REUSEADDR,验证重启失败
./reuse_demo server 9090 &      # 启动
./reuse_demo client 127.0.0.1 9090 3
kill %1                         # ctrl-C 关掉 → 进 TIME_WAIT
./reuse_demo server 9090        # 应 bind 失败 EADDRINUSE

# 实验 B:开 REUSEADDR,重启成功
./reuse_demo server_reuse 9090 & ...
kill %1
./reuse_demo server_reuse 9090  # 应成功

# 实验 C:REUSEPORT 双进程负载分担
./reuse_demo twin A 9091 &
./reuse_demo twin B 9091
./reuse_demo client 127.0.0.1 9091 5   # 观察连接分别落到 A/B

./reuse_demo explain            # 看概念说明
```

### Python

```bash
cd python
python3 main.py explain        # 看概念说明
python3 main.py demo           # 自动跑 A/B/C 三组实验(可能因权限问题 EACCES)
```

### Go

```bash
cd go
go run . explain               # 看概念说明
go run . demo                  # 自动跑 A/B/C 三组实验
```

## 关键代码片段

C 版核心(`c/main.c`):

```c
static int make_listen(int port, int reuse_addr, int reuse_port) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    int yes = 1;
    if (reuse_addr) setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    if (reuse_port) setsockopt(fd, SOL_SOCKET, SO_REUSEPORT,  &yes, sizeof(yes));
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_ANY), .sin_port = htons(port) };
    bind(fd, (struct sockaddr *)&a, sizeof(a));
    listen(fd, 16);
    return fd;
}
```

Python 关键差异(`python/main.py`):

```python
# 3.11+ 有 SO_REUSEPORT 常量;更早用数字 15(不一定跨平台)
fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1) if hasattr(socket, "SO_REUSEPORT") else None
```

Go 通过 `net.ListenConfig.Control` 注入底层选项(`go/main.go`):

```go
lc := net.ListenConfig{
    Control: func(_ string, _ string, c syscall.RawConn) error {
        return c.Control(func(fd uintptr) {
            syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, 15 /* SO_REUSEPORT */, 1)
        })
    },
}
```

## 性能与边界

- **`TIME_WAIT` 时长不可调**:Linux 内核 `TCP_TIMEWAIT_LEN` 写死 60s(`include/net/tcp.h`),`net.ipv4.tcp_fin_timeout` 控制的是 `FIN_WAIT_2`,不是 `TIME_WAIT`。
- **`SO_REUSEPORT` 分发哈希**:默认按四元组 hash,在多核下连接相对均匀;但若只有 1 个客户端源 IP,所有连接都落到同一 worker → 失去负载分担意义;可用 `SO_ATTACH_REUSEPORT_CBPF`(Linux 4.6+)挂 BPF 改 hash。
- **短连接高并发**:QPS 1 万时,`TIME_WAIT` 累计可达 60 万(60s × 1 万 / s);影响:
  - 客户端:端口耗尽 → 调大 `net.ipv4.ip_local_port_range`(默认 32768-60999,扩到 1024-65535);
  - 服务端:仅占用 socket 结构,内存开销小。

## 注意事项与常见坑

- ❌ **忘了设 `SO_REUSEADDR`,开发服务器频繁重启**:每次都得等 60s,体验极差。
- ❌ **`SO_REUSEADDR` bind 到 `0.0.0.0`**:同主机同 uid 的进程都能抢;**生产环境应 bind 特定 IP**(如 `127.0.0.1:3306`)。
- ❌ **用了 `tcp_tw_recycle`**:Linux 4.12 已移除,在 NAT 后多客户端共享 IP 场景下会导致连接失败;**永远不要开**。
- ❌ **`SO_REUSEPORT` 在不同进程**:**必须所有 socket 都设**才能成功;漏设一个会被 `EADDRINUSE`。
- ❌ **短连接 server 满 `TIME_WAIT` 不优化**:很多教程改 `net.ipv4.tcp_max_tw_buckets`,但这是限制最大 `TIME_WAIT` 数量而非加速清理;触发后会拒绝新连接,通常不是好办法。

## 参考资料(实际阅读过的权威来源)

- [RFC 793 §3.9 — TIME-WAIT state, 2MSL wait(1981)](https://datatracker.ietf.org/doc/html/rfc793#section-3.9) — TIME_WAIT 协议层定义
- [Linux man page: socket(7) — SO_REUSEADDR / SO_REUSEPORT](https://man7.org/linux/man-pages/man7/socket.7.html) — Linux 内核对两选项的官方语义
- [Linux man page: ip(7) — IP_TRANSPARENT / IP_FREEBIND 等](https://man7.org/linux/man-pages/man7/ip.7.html) — IP 层相关选项
- [Linux kernel commit — SO_REUSEPORT for TCP and UDP(Linux 3.9, 2013)](https://lwn.net/Articles/542629/) — LWN 对 REUSEPORT 的详细设计分析
- [systemd.socket(5) — ReusePort=](https://manpages.ubuntu.com/manpages/jammy/en/man5/systemd.socket.5.html) — systemd 中如何映射 REUSEPORT
- [tsight.io — 内核视角下的 Socket 绑定困局:TIME_WAIT 机制与复用策略深度解析](https://tsight.io/articles/13503241) — 中文工程视角总结(与内核源码对照)
- [StackOverflow — bind fails after SO_REUSEADDR for wildcard in TIME_WAIT](https://stackoverflow.com/a/71830970/9652400) — Linux 与 BSD REUSEADDR 语义差异细节