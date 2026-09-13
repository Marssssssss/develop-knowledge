# TCP Echo Server(最小阻塞回环服务)

## 简介

- 最小可工作的 TCP Echo Server:接受客户端连接,把收到的字节原样回写;客户端关闭 FIN 后服务端关闭连接。
- 演示 BSD socket API 的标准服务端流程:`socket()` → `bind()` → `listen()` → `accept()` 循环 → `recv()`/`send()` 回环。
- 关键概念:三次握手完成后服务端在内核为每条连接分配独立 socket;`accept()` 返回的是新 fd,与监听 fd 分开;**单 accept loop = 串行服务多个客户端**(每个客户端必须等前一个结束;并发需多进程 / 线程 / IO 多路复用)。
- 历史背景:4.2BSD(1983)首次以通用 API 形式提供 BSD socket;RFC 793(1981)定义 TCP 协议;两者结合诞生 Unix 网络编程范式。

## 原理详解

### 服务端流程(每次启动顺序固定)

1. `socket(AF_INET, SOCK_STREAM, 0)` — 创建 IPv4 TCP socket(fd=3)
2. `bind(fd, "0.0.0.0:PORT", ...)` — 绑定本机地址与端口;失败常见 EADDRINUSE
3. `listen(fd, backlog=16)` — 进入 LISTEN 状态;`backlog` 是内核未完成 + 已完成握手队列容量上限
4. `accept(fd)` — 阻塞等待客户端完成三次握手;返回新 fd 代表这条连接
5. `recv(new_fd, buf, N, 0)` — 阻塞读(返回 0 表示对方 FIN)
6. `send(new_fd, buf, n, 0)` — 阻塞写;**可能短写**(Partial Write),生产代码需循环 `send` 直到写完
7. `close(new_fd)` — 触发服务端 FIN,进入 FIN_WAIT_1 → FIN_WAIT_2 → TIME_WAIT 序列

### 状态机(单连接视角)

```
              三次握手
LISTEN ─────────────────► ESTABLISHED ◄──── recv/send 回环
                                │
                                │ 客户端 close()
                                ▼
                           CLOSE_WAIT ──────► 服务端 close() ─► LAST_ACK ─► CLOSED
                                                      (主动关闭方走 TIME_WAIT)
```

### 关键 API

| C 函数 | Python `socket` | Go `net` | 作用 |
| --- | --- | --- | --- |
| `socket(AF_INET, SOCK_STREAM, 0)` | `socket.socket(AF_INET, SOCK_STREAM)` | `net.Listen("tcp", addr)` | 创建 socket |
| `bind(fd, addr, len)` | `sock.bind((host, port))` | (含在 Listen 内) | 绑定地址 |
| `listen(fd, n)` | `sock.listen(n)` | (含在 Listen 内) | 监听 |
| `accept(fd)` | `sock.accept()` | `ln.Accept()` | 接受连接 |
| `recv(fd, buf, n, 0)` | `conn.recv(n)` | `conn.Read(buf)` | 读数据 |
| `send(fd, buf, n, 0)` | `conn.send(buf)` | `conn.Write(buf)` | 写数据 |
| `close(fd)` | `conn.close()` | `conn.Close()` | 关闭 |

### 底层发生了什么

- `bind` 写入内核 `sock` 结构的 `__sk_common.skc_rcv_saddr` / `skc_num`;若端口已被占用 → 内核在 `inet_csk_get_port` 中返回 `-EADDRINUSE`。
- `listen` 把 socket 状态置为 `TCP_LISTEN`,并分配 `accept_queue`(已建立连接队列)与 `syn_queue`(半连接队列)。
- `accept` 从 `accept_queue` 取走一个已完成握手的 socket;若队列空则阻塞在等待队列上,直到新握手完成。
- `recv` 内核走 `tcp_recvmsg` → 若 socket 是阻塞且无数据,加入 `sk_wq` 等待队列,调度器挂起进程。
- `send` 内核走 `tcp_sendmsg` → 写入 `sk_write_queue`(发送缓冲);若缓冲满则阻塞在 `sk_wq`。

## 对比 / 选型

| 模式 | 并发 | 复杂度 | 适用 |
| --- | --- | --- | --- |
| 单 accept loop(本 demo) | 1 客户端 | 最低 | 教学、串行服务 |
| `fork()` per connection | 1 客户端/进程 | 中 | 老 Apache mpm-prefork |
| `pthread_create()` per connection | 1 客户端/线程 | 中 | 数据库连接 |
| IO 多路复用(select/poll/epoll/kqueue) + 非阻塞 | N 客户端/单线程 | 高 | 高并发服务(Redis / Nginx worker) |

> 本 demo 故意保持最简;并发模型在 `01-游戏开发/01-服务端/网络编程/IO多路复用/` 系列 demo 中深入。

## 环境准备

- 操作系统:Linux / macOS / WSL2(Windows 需启用 WSL)
- 编译器:`gcc` ≥ 4.8
- Python:≥ 3.8
- Go:≥ 1.18

## 运行方式

### C

```bash
cd c
gcc -O2 -Wall -Wextra -o echo_server main.c
./echo_server 9090
```

另开终端测试:`nc 127.0.0.1 9090`,输入任意字符按回车。

### Python

```bash
cd python
python3 main.py 9090
```

另开终端测试:`nc 127.0.0.1 9090`。

### Go

```bash
cd go
go run . 9090
```

另开终端测试:`nc 127.0.0.1 9090`。

## 关键代码片段

C 版 accept loop(典型 BSD 风格,见 `c/main.c`):

```c
while (!g_stop) {
    int cfd = accept(lfd, (struct sockaddr *)&cli, &cli_len);
    if (cfd < 0) { if (errno == EINTR) continue; break; }

    // recv/send 回环;n == 0 → 客户端 FIN
    char buf[4096]; ssize_t n;
    while ((n = recv(cfd, buf, sizeof(buf), 0)) > 0) {
        ssize_t off = 0;
        while (off < n) {                       // 处理 partial write
            ssize_t k = send(cfd, buf + off, n - off, 0);
            if (k < 0) { if (errno == EPIPE) break; perror("send"); goto end; }
            off += k;
        }
    }
end:
    close(cfd);
}
```

Go 版最简(只用一个 `io.CopyBuffer`):

```go
func handle(c net.Conn) {
    defer c.Close()
    // io.CopyBuffer 在 client 端 Read 返回 io.EOF 时自动退出
    io.CopyBuffer(c, c, make([]byte, 4096))
}
```

## 性能与边界

- **串行吞吐量上限**:每条连接独占 accept loop,总吞吐 ≈ `1 / RTT × 客户端数量`,对延迟敏感场景无法扩展。
- **backlog**:Linux 实际有效 backlog 受 `/proc/sys/net/core/somaxconn`(默认 4096)限制;`listen(fd, 16)` 实际生效 ≥ 16 但 ≤ somaxconn。
- **TIME_WAIT 累积**:服务端主动 close → 进入 2×MSL(典型 60 s);高并发短连接场景会占满端口。生产中通常 `SO_REUSEADDR`(见同目录 `SO-REUSEADDR/`)。

## 注意事项与常见坑

- ❌ **不处理 SIGPIPE**:对端关闭后 `send` 会触发 `SIGPIPE`,默认杀进程 → 必须 `signal(SIGPIPE, SIG_IGN)`(C)/`SIG_IGN`(Python)/Go 不暴露。
- ❌ **不检查 `send` 返回值**:`send` 可能短写(partial write),生产代码必须循环写完;本 demo 已处理。
- ❌ **不处理 EINTR**:信号中断的系统调用应重试;本 demo 在 `accept`/`recv`/`send` 三处检查 EINTR。
- ❌ **fd 泄漏**:异常路径忘记 `close(cfd)` → 文件描述符耗尽。
- ❌ **bind 0.0.0.0 与 SO_REUSEADDR**:开发环境频繁重启必设 `SO_REUSEADDR=1`(本 demo Python/Go 版已设;C 版若重启被 EADDRINUSE 报请加 `setsockopt SO_REUSEADDR`,见同目录 `SO-REUSEADDR/`)。

## 参考资料(实际阅读过的权威来源)

- [W. Richard Stevens — UNIX Network Programming, Volume 1, Chapter 4 (Elementary Sockets)](https://www.unpbook.com/) — BSD socket 标准教材,本 demo 直接对应 §4.6 "Concurrent Servers" 之前的串行版本
- [Linux man page: socket(7)](https://man7.org/linux/man-pages/man7/socket.7.html) — socket 选项总览
- [Linux man page: tcp(7)](https://man7.org/linux/man-pages/man7/tcp.7.html) — TCP 协议在 Linux 的实现细节
- [RFC 793 — Transmission Control Protocol(1981)](https://datatracker.ietf.org/doc/html/rfc793) — TCP 协议原始定义;三次握手在 §3.4
- [Go net package — net.Conn](https://pkg.go.dev/net) — Go 高层网络 API;`net.Listen` / `net.Accept` 直接对应 BSD API
- [Python socket — Low-level networking interface](https://docs.python.org/3/library/socket.html) — Python 标准库 socket 模块;注意 `SO_REUSEADDR` 在 Windows 上语义不同
- [Beej's Guide to Network Programming — 5.2 `accept()`](https://beej.us/guide/bgnet/html/split/system-calls-or-bust.html) — 入门级 echo server 示例