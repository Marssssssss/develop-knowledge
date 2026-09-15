# scatter-gather IO：readv / writev 与 TCP_CORK

## 简介

**scatter-gather IO（分散-聚集 IO）**指用**一次系统调用**读写多个缓冲区：`readv()`
把一次读分散进多个缓冲区（scatter input），`writev()` 把多个缓冲区的数据聚集写出
（gather output）。它解决的是"数据天然分成几块，但我不想为了发出去而在用户态拼一遍"
这个非常常见的需求——HTTP 响应就是"头 + 正文"两块。

关键概念：

- **`iovec`**：一个"基址 + 长度"的描述符，`readv`/`writev` 接受它的数组。
- **`IOV_MAX`**：一次调用能带的 iovec 数量上限，现代 Linux 是 **1024**（Linux 2.0 时代是 16）。
- **原子性**：`writev()` 写出的数据是**单一数据块**，不会与其他进程的写输出交错。
- **短写**：返回值可能小于请求量，**这不是错误**，调用方必须循环续写。
- **`TCP_CORK`**：置位时不发部分帧，清除时一齐发出；有 **200 ms 上限**，达上限自动发送。

历史背景：`readv`/`writev` 属于 4.2BSD 时代的经典接口（POSIX.1-2001 标准化），而
`TCP_CORK` 是 Linux 2.2 引入的、与 Nagle 互补的组包手段——man7 `sendfile(2)` 明确
推荐"要在文件内容前面拼响应头时，用 `TCP_CORK` 把包数压到最少"。

## 原理详解

### 1. 为什么 TCP 是字节流，包数却取决于写法

TCP 不保留应用层消息边界：内核按 MSS 切段。所以**段数**由"总字节数 ÷ MSS"决定，
而**实际发出几个包**取决于**写入节奏**——一次调用写 692 字节是 1 个段，分两次写
就是 2 个段（Nagle 关闭时立刻发出；Nagle 开启时第二个小包要等 ACK，反而引入延迟）。

```
头 180 B + 体 512 B，MSS 1460：

A) write(header); write(body)          →  seg0: header[180]        (180 B 的段！浪费)
                                          seg1: body[512]
B) memcpy 合并; write(merged)          →  seg0: merged[692]        ← 1 段，但多一次 memcpy
C) writev([header, body])              →  seg0: header[180]+body[512]  ← 1 段，0 次 memcpy
D) write(header); CORK; write(body); uncork → seg0: 同 C
```

### 2. readv 的语义（man7 原文要点）

> "Buffers are processed in array order. This means that readv() completely fills
> iov[0] before proceeding to iov[1], and so on. (If there is insufficient data,
> then not all buffers pointed to by iov may be filled.)"

也就是说 `readv(fd, iov, 3)` 且 `iov_len = {100, 900, 1000}` 时：先用前 100 字节填满
`iov[0]`，再填 `iov[1]`，再填 `iov[2]`；数据不够时后面几个缓冲区只被部分填充，
**这不算错误**。

原子性也有明确保证：

> "The data transfers performed by readv() and writev() are atomic"

### 3. iovec 的边界条件

| 条件 | 结果 |
| --- | --- |
| `iovcnt < 0` 或 `> IOV_MAX`（1024） | `EINVAL` |
| 所有 `iov_len` 之和溢出 `ssize_t` | `EINVAL` |
| 返回值小于请求量 | 不是错误：短读/短写，`read`/`write` 同理 |

### 4. TCP_CORK：与 Nagle / TCP_NODELAY 的关系

man7 `tcp(7)` 的三条关键规定：

- **TCP_CORK**（Linux 2.2+）：「If set, don't send out partial frames. All queued
  partial frames are sent when the option is cleared again.」并且「there is a **200
  millisecond ceiling** on the time for which output is corked」。
- **TCP_NODELAY**：「This option is **overridden by TCP_CORK**; however, setting this
  option **forces an explicit flush** of pending output, even if TCP_CORK is
  currently set.」（两者自 Linux 2.5.71 起可以组合）
- **tcp_autocorking**（Linux 3.14+，默认开）：内核在"该流还有包排在 qdisc/设备队列里"
  时自动合并连续小写。**这会让"两次 write 必然两个包"不成立**——本 demo 的 C 版因此
  只断言单调性而不写死 `==2`。

### 5. 头 + 正文 == 最典型的用法

man7 `sendfile(2)` NOTES 原文：

> "If you plan to use sendfile() for sending files to a TCP socket, but need to send
> some header data in front of the file contents, you will find it useful to employ
> the TCP_CORK option, described in tcp(7), to minimize the number of packets and to
> tune performance."

即：`setsockopt(TCP_CORK, 1)` → `write(header)` → `sendfile(file)` →
`setsockopt(TCP_CORK, 0)`。本 demo 的 C 版就是这条路径的 write 版实现。

## 环境准备

- 操作系统：Python 版跨平台（模型 + 事件循环，`os.writev` 在 Windows 上不存在时自动
  退化为纯用户态等价物）；C 版仅 Linux（`TCP_INFO`/`TCP_CORK`/`fork`）；
  Go 版跨平台，但 `TCP_CORK` 只在 Linux 生效。
- 语言版本：Python 3.8+；gcc/clang（需 `<sys/uio.h>`、`<netinet/tcp.h>`）；Go 1.21+。
- C 版的包数统计依赖 `tcpi_segs_out`（Linux 4.6+ 的内核 + 匹配的头文件）。

## 运行方式

### Python（先跑这个，10 项断言）

```bash
python3 main.py
```

### C（仅 Linux，用内核 `tcpi_segs_out` 实测包数；包内实现在 `scatter_impl.h`，由 `main.c` 原地 `#include`）

```bash
gcc -O2 -Wall -Wextra main.c -o scatter_gather_demo
./scatter_gather_demo
```

### Go（`net.Buffers` == writev，附 `TCP_CORK` 的 Go 坑；同包多文件用 `.`）

```bash
go run .
```

## 关键代码片段

```c
/* C：writev 聚集写 + 循环处理短写 */
struct iovec iov[2];
iov[0].iov_base = hdr;  iov[0].iov_len = header_len;
iov[1].iov_base = body; iov[1].iov_len = body_len;
ssize_t n = writev(fd, iov, 2);        /* 一次系统调用，单一数据块（原子） */

/* C：TCP_CORK 把"头 + 正文"压成一个包（man7 sendfile(2) NOTES 推荐的组合） */
setsockopt(fd, IPPROTO_TCP, TCP_CORK, &one, sizeof(one));
write(fd, hdr, header_len);
write(fd, body, body_len);              /* 真正生产里这一步换成 sendfile(fd, file_fd, ...) */
setsockopt(fd, IPPROTO_TCP, TCP_CORK, &zero, sizeof(zero));   /* uncork → 一齐发出 */

/* C：用内核统计的已发送段数验证"包数"，而不是靠应用层推断 */
struct tcp_info info; socklen_t len = sizeof(info);
getsockopt(fd, IPPROTO_TCP, TCP_INFO, &info, &len);
printf("已发送段数 = %u\n", info.tcpi_segs_out);
```

```go
// Go：net.Buffers 的 WriteTo 在 Linux 上走 writev(2)
bufs := net.Buffers{header, body}
n, err := bufs.WriteTo(conn)     // 一次系统调用写出两块

// Go 的坑：syscall 包没有导出 TCP_CORK，必须自己定义（内核 uapi: TCP_CORK = 3）
rc, _ := tcp.SyscallConn()
rc.Control(func(fd uintptr) {
    syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, 3 /* TCP_CORK */, 1)
})
```

## 性能与边界

本 demo 实测输出（Python 版，头 180 B + 体 512 B）：

| 策略 | 系统调用 | 包数 | 代价 |
| --- | --- | --- | --- |
| `write(header)` + `write(body)` | 2 | **2** | 多一个包、多一次切换 |
| memcpy 合并 + `write(整体)` | 1 | 1 | 一次全量用户态 memcpy |
| `writev(header, body)` | 1 | 1 | —（无额外拷贝） |
| `TCP_CORK: write+write+uncork` | 3 | 1 | 多一次 `setsockopt`（cork/uncork） |

大响应体（头 180 B + 体 1 MiB）时**包数趋同**（719 vs 719——TCP 按 MSS 切段），此时
`writev` 的价值是**省掉用户态那 1 MiB memcpy**，而不是减少包数。本机微基准中"合并到
用户态缓冲"比"只收集 iovec 引用"慢约 **4.4×**。

## 注意事项与常见坑

1. **`iovcnt` 超 `IOV_MAX` 会被 `EINVAL` 顶回来**。现代 Linux 是 1024，但 Linux 2.0
   时代只有 16；跨平台代码应该用 `sysconf(_SC_IOV_MAX)` 或保守地分批。
2. **必须处理短写**。`writev` 返回值可能小于求和后的 `iov_len`。一个 MSS 内的小响应
   通常一次写完，1 MiB 的正文就会短写——不循环就会静默丢尾巴。
3. **`writev` 是原子的，但"原子"只对**「不被其他进程的输出交错」**而言**，不保证一次
   全写完。两件事别混。
4. **`TCP_CORK` 与 `TCP_NODELAY` 优先级相反的两个方向**：CORK 会覆盖 NODELAY 的语义，
   但**设置** NODELAY 会强制 flush（即使 CORK 正置位）。写 cork/uncork 的代码时别在
   中间去设 NODELAY。
5. **`TCP_CORK` 有 200 ms 上限**。别把它当"手动控制发送时机"的长期开关：达到 200 ms
   内核会自己发出去。要长时间累积请用应用层缓冲。
6. **`tcp_autocorking` 会让"两次 write 必然两个包"不成立**（Linux 3.14+ 默认开）。它只会
   在"该流还有包排在队列里"时合并，所以基准测试里两次写之间隔得够久仍然会是两个包。
   本 demo 的 C 版因此只断言"两次 write 的段数 ≥ writev 的段数"，不写死等于 2。
7. **Go 里 `syscall` 没有 `TCP_CORK`**：只有 `TCP_NODELAY`。要用 CORK 得自己写常量
   （`include/uapi/linux/tcp.h` 里是 3）或引入 `golang.org/x/sys/unix`。本仓库不引第三方
   依赖，所以直接定义常量并在注释里给出出处。
8. **别在小响应上迷信 CORK**：`setsockopt` 本身也是系统调用。头 + 正文都在用户态时，
   `writev` 通常比"cork + 两次 write + uncork"更划算（1 次调用 vs 3 次）。

## 参考资料（实际阅读过的权威来源）

- [readv(2) — Linux manual page](https://man7.org/linux/man-pages/man2/readv.2.html)
  — iovec 的"按数组顺序填满"语义、`IOV_MAX` 现代为 1024（Linux 2.0 为 16）、
  短读短写不是错误、`readv`/`writev` 的原子性保证、`EINVAL` 条件。
- [tcp(7) — Linux manual page](https://man7.org/linux/man-pages/man7/tcp.7.html)
  — `TCP_CORK`（不发部分帧、200 ms 上限、可覆盖 TCP_NODELAY、自 2.5.71 可与 NODELAY
  组合）、`TCP_NODELAY`（设置时强制 flush）、`tcp_autocorking`（3.14+ 默认开）。
- [sendfile(2) — Linux manual page](https://man7.org/linux/man-pages/man2/sendfile.2.html)
  — NOTES 中"要在文件内容前拼响应头时应配合 TCP_CORK 减少包数"的官方建议。
- [net.Buffers 与 TCPConn.ReadFrom（Go 标准库）](https://pkg.go.dev/net#Buffers)
  — `Buffers.WriteTo` 在支持 writev 的平台上一次系统调用写出多块；
  `TCPConn.ReadFrom` 先试 `splice` 再试 `sendFile`。
