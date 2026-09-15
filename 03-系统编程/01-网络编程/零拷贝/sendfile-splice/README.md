# 零拷贝：sendfile / splice / mmap

## 简介

**零拷贝（zero-copy）**指在"把数据从磁盘送到网卡"这类**纯转发**场景里，让数据不经过
用户态缓冲区、甚至完全不经过 CPU 搬运，只在内核里传递页引用或描述符。

关键概念：

- **page cache**：内核页缓存。从文件读数据时，DMA 先把数据放进 page cache。
- **DMA 拷贝**：磁盘控制器/网卡直接读写内存，不占用 CPU（物理上省不掉）。
- **CPU 拷贝**：`memcpy`，占用 CPU 与内存带宽——零拷贝真正要消灭的就是这一项。
- **上下文切换**：用户态↔内核态的往返。`read+write` 每块数据要 4 次。
- **SG-DMA（scatter-gather DMA）**：网卡能按"地址+长度"的描述符从多处内存聚合成一个包，
  从而使 `sendfile` 达到 **0 CPU 拷贝**。

历史背景：1999 年 Linux 2.2 引入 `sendfile(2)`（Kafka/Nginx 静态文件服务的性能基础），
2006 年 2.6.17 引入更通用的 `splice(2)`，2019 年 5.1 引入 `io_uring`。这套机制也是
Kafka 用 `transferTo`、Nginx 用 `sendfile on` 就能把静态文件吐得飞快的原因。

## 原理详解

### 1. 传统 read + write：4 次拷贝、4 次上下文切换

```
read():  [DMA] 磁盘 ──→ page cache
         [CPU] page cache ──→ 用户缓冲区        ← 纯税：进程根本没看这些字节
write(): [CPU] 用户缓冲区 ──→ socket 缓冲区
         [DMA] socket 缓冲区 ──→ 网卡
```
`read` 与 `write` 各一次系统调用，每次进出内核态 → **4 次切换**。

### 2. mmap + write：省掉一次内核→用户拷贝

`mmap()` 把 page cache 映射进用户地址空间，用户态与内核态**共享同一块物理内存**，
于是 `mmap` 之后直接 `write` 即可，省掉 1 次 CPU 拷贝。但上下文切换**仍是 4 次**，
且缺页中断（page fault）+ TLB 刷新有额外成本。

### 3. sendfile：数据不出内核

```c
ssize_t sendfile(int out_fd, int in_fd, off_t *offset, size_t count);
```

- 一次系统调用完成搬运 → **2 次上下文切换**。
- `offset` 非 NULL 时，内核把该变量更新为"最后一个被读字节之后"的位置，**且不修改
  `in_fd` 自己的文件偏移**——正好适合循环分块发送；`offset` 为 NULL 则从文件当前偏移读并推进它。
- 网卡不支持 SG-DMA 时仍有 1 次 CPU 拷贝（page cache → socket 缓冲区）；支持时
  CPU 只写描述符 → **0 CPU 拷贝**。

**限制（man7 sendfile.2）**：

- `in_fd` 必须支持 mmap-like 操作，**不能是 socket**（Linux 5.12 起若 `out_fd` 是管道则
  退化为 `splice`）。所以 socket→socket 转发不能用它。
- 2.6.33 之前 `out_fd` 只能是 socket，之后可以是任意文件。
- 单次最多 `0x7ffff000`（2,147,479,552）字节，32/64 位系统一样；超了要自己拆。
- `offset` 非 NULL 但 `in_fd` 不可 seek → `ESPIPE`；`in_fd` 不是 mmap-like → `EINVAL`；
  `count` 过大 → `EOVERFLOW`。**建议在 `EINVAL`/`ENOSYS` 时回退到 read+write**。
- 要给文件内容前面拼响应头时，配合 `TCP_CORK`（见 `tcp(7)`）能显著减少包数。

### 4. splice：页引用经管道搬运，通用零拷贝

`sendfile` 只能"文件 → socket/文件"；要转发 socket→socket（代理、流媒体）就得用
`splice()`：数据以**页引用**的形式从源 fd 移入管道，再从管道移入目标 fd，**全程没有
CPU 拷贝**，而且**不要求网卡支持 SG-DMA**。代价是两端至少有一端必须是管道。

### 5. 搬运成本对照

| 方案 | CPU 拷贝 | DMA 拷贝 | 上下文切换/块 | 系统调用/块 | 内存搬运量 |
| --- | --- | --- | --- | --- | --- |
| read + write | **2** | 2 | **4** | 2 | 4× |
| mmap + write | 1 | 2 | 4 | 2 | 3× |
| sendfile（无 SG-DMA） | 1 | 2 | 2 | 1 | 3× |
| sendfile + SG-DMA | **0** | 2 | 2 | 1 | 2× |
| splice | **0** | 2 | 2 | 2 | 2× |

（本 demo 的 Python 版会把这张表算出来并断言，见"运行方式"。）

### 6. 相关但与零拷贝不同的选项

- **`TCP_CORK`**：置位时不发部分帧，清除时一齐发出；**200 ms 上限**后自动发送。
  与 `sendfile` 配合"预置头部 + 发文件"最合适。`TCP_NODELAY` 会强制刷新（即使 CORK 置位），
  而 CORK 会覆盖 NODELAY 的语义。
- **`tcp_autocorking`**（Linux 3.14+，默认开）：内核自动合并连续的小 `write`/`sendmsg`，
  减少包数——与 `TCP_CORK` 互补。
- **`MSG_ZEROCOPY` / `vmsplice` / `io_uring`**：源数据本就在用户态缓冲区时的选择
  （`MSG_ZEROCOPY` 用 errqueue 通知页释放，`io_uring` 可以连系统调用都省掉）。

## 环境准备

- 操作系统：C 版**仅 Linux**（sendfile/splice/loopback）；Python 版跨平台（纯模型 + 微基准）；
  Go 版跨平台，但只有 Linux 才能命中 sendfile/splice 快速路径。
- 语言版本：Python 3.8+；gcc/clang（`_GNU_SOURCE`）；Go 1.21+。
- 依赖：`<sys/sendfile.h>`（glibc 自带，无需额外库）。

## 运行方式

### Python（跨平台，先跑这个，10 项断言）

```bash
python3 main.py
```

输出：五种路径的 CPU/DMA 拷贝与切换次数表、大文件分块次数、本机用户态拷贝微基准、
"什么时候不能用零拷贝"清单。

### C（仅 Linux，真实系统调用 + 计时 + `/proc/self/io` 计数）

```bash
gcc -O2 -Wall -Wextra main.c -o zerocopy_demo
./zerocopy_demo 64        # 参数为文件大小（MiB），默认 64
```

### Go（`io.Copy` 快速路径 vs 用户态拷贝）

```bash
go run main.go
```

## 关键代码片段

```c
/* sendfile：offset 非 NULL 时分块循环 + offset 自动推进 */
off_t off = 0;
while (off < st.st_size) {
    ssize_t n = sendfile(out_fd, in_fd, &off, CHUNK);
    if (n < 0) {
        if (errno == EINTR) continue;
        if (errno == EINVAL || errno == ENOSYS)   /* man7 建议的回退路径 */
            return send_read_write(out_fd, path);
        break;
    }
    if (n == 0) break;
}

/* splice：注意 socket→socket 只能靠它，两端至少一端是管道 */
ssize_t n = splice(in_fd, NULL, pfd[1], NULL, CHUNK, SPLICE_F_MOVE);
/* …把 pfd[0] 的数据再 splice 到目标 fd，循环到 n == 0… */
```

```go
// Go：把 *os.File 直接交给 io.Copy，TCPConn.ReadFrom 会自动尝试 sendfile
io.Copy(conn, file)
// 想强制走用户态拷贝做对照？把 WriteTo 藏起来即可：
io.Copy(conn, readerOnly{file})   // type readerOnly struct{ io.Reader }
```

## 性能与边界

- 本 demo Python 版实测（本机 32 MiB，用户态路径代理基准）：逐块 `memcpy` 约 64 GB/s，
  只登记「引用 + 长度」约 212–279 GB/s——**拷贝成本 ∝ 字节数，描述符成本 ∝ 块数**。
- 量级参考：1 MB 静态文件、10 GbE 网卡上，`read+write` 单核跑满约 600 MB/s，
  `sendfile` 可到 1.1 GB/s 且 CPU 还有余量；文件已在 page cache 时收益最大。
- **大文件不是无脑零拷贝**：GB 级文件会把 page cache 挤爆，`sendfile` 的 DMA 入缓存
  这一步变成纯浪费；且一次性 `sendfile` 10 GB 会长时间占住网卡发送队列、饿死小文件。
  正解是**应用层分块**（本 demo 的 `chunked_sendfile`，建议 1–4 MiB）。
- 平台差异：`sendfile` 各 UNIX 语义与原型都不同（man7 明确写 "It should not be used in
  portable programs"）；Windows 用 `TransmitFile`/`WSASend` 的对应机制。

## 注意事项与常见坑

1. **"零拷贝"不是真的零**：DMA 那 2 次（磁盘→page cache、socket 缓冲区→网卡）是硬件
   层面的，省不掉。所有"零拷贝"都在说**消灭 CPU 拷贝**。
2. **`in_fd` 不能是 socket**：想写代理却发现只能用 `read+write`？换成 `splice` + 管道。
   本 demo C 版用 `socketpair` 实测了这条限制（返回 `EINVAL`）。
3. **`sendfile` 的 `offset` 语义容易写错**：传 `&off` 时不改 `in_fd` 的文件偏移，传 `NULL`
   才改。混用会导致"重复发前半段"或"跳过一段"。
4. **必须处理短写**：`sendfile` 成功也可能只发了一部分（"may write fewer bytes than
   requested; the caller should be prepared to retry"）。必须循环到全部发完。
5. **单次上限 `0x7ffff000`**：比这个数大不会报错，只会只发这么多——不检查就会静默丢尾巴。
6. **`out_fd` 带 `O_APPEND` 会失败**（`EINVAL`），这是 sendfile 明确不支持的组合。
7. **别在小文件上迷信零拷贝**：`splice` 需要建管道（2 次额外系统调用），小包场景下
   `read+write` 反而可能更快。用 `strace -c` 或 `perf stat` 量了再决定。
8. **Go 的"自动化"依赖类型系统**：`io.Copy` 走不走 sendfile，取决于 `src` 是否实现
   `WriterTo`、`dst` 是否实现 `ReaderFrom`；随手加一层 `bufio.Reader` 包装就会静默
   退回用户态拷贝。本 demo 用 `readerOnly` 把这件事显式化。

## 参考资料（实际阅读过的权威来源）

- [sendfile(2) — Linux manual page](https://man7.org/linux/man-pages/man2/sendfile.2.html)
  — 原型、`offset` 语义、`in_fd` 不能是 socket、2.6.33 起 `out_fd` 可任意 fd、
  单次 `0x7ffff000` 上限、`EINVAL`/`EOVERFLOW`/`ESPIPE` 错误码、与 `TCP_CORK` 的配合建议。
- [tcp(7) — Linux manual page](https://man7.org/linux/man-pages/man7/tcp.7.html)
  — `TCP_CORK`（200 ms 上限、可覆盖 `TCP_NODELAY`）、`TCP_NODELAY`、`tcp_autocorking`。
- [io.Copy / net.TCPConn.ReadFrom 的 sendfile 快速路径](https://github.com/golang/go/blob/master/src/net/tcpsock_posix.go)
  — Go 标准库：`TCPConn.readFrom` 先试 `splice`、再试 `sendFile`，失败才 `genericReadFrom`。
- [Kafka 的零拷贝设计（Confluent 官方文档）](https://docs.confluent.io/platform/current/kafka/design.html)
  — 生产者/消费者的 `sendfile`+page cache 路径，工业界最知名的应用案例。
