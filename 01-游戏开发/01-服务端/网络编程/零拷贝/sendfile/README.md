# sendfile — Linux 内核级零拷贝

## 简介

**`sendfile(2)`** 是 Linux 2.2 引入的内核态文件传输 syscall,
将数据从**文件**(fd 必须支持 mmap)**直接管道进 socket**(早期)/任意 fd(2.6.33+),
数据**不再经过用户缓冲**,在支持 scatter-gather DMA 的 NIC 上甚至**不经过主 socket 缓冲区**。
Nginx、Tornado、HAProxy、srs(Simple Rtmp Server)等大量静态资源服务器默认开启 `sendfile on`。

- **关键概念清单**
  - **`sendfile(out_fd, in_fd, offset, count)`**:内核态数据移交,**不**过用户态
  - **零拷贝**:传统 read+write = 4 次 copy(磁盘→pagecache→user→skbuff→NIC);sendfile 可压到 0 次 CPU copy
  - **`tcp(7) TCP_CORK`**:让 sendfile + 前置 header 合并到一个 TCP 段
  - **`splice(2)`**:管道 page-ref 传递,补充文件→socket 之外的场景(Linux 2.6.17+)
  - **`copy_file_range(2)`**:文件→文件零拷贝,支持 Btrfs XFS reflink(Linux 4.5+)

## 原理详解

### 传统 read+write 路径

```
       ┌──────┐  DMA  ┌────────┐  CPU  ┌─────────┐  CPU  ┌─────────┐  DMA  ┌─────┐
       │ 盘   │ ────► │page    │  ───► │user buf │  ───► │socket   │  ───► │NIC  │
       └──────┘  copy  │cache   │  copy └─────────┘  copy │sk_buff  │  copy └─────┘
                       └────────┘                         └─────────┘
                       4 copies, 4 context switch (=read_user + write_user)
```

### sendfile 路径(NIC 支持 SG-DMA)

```
       ┌──────┐  DMA  ┌────────┐  DMA  ┌─────┐
       │ 盘   │ ────► │page    │  ────► │NIC  │
       └──────┘        │cache   │ SG-DMA └─────┘
                       └────────┘
                       0 CPU copy, 1 context switch (sendfile syscall)
```

> **CPU copies: 0**;**context switches: 1**。NIC 读的是**page cache** 的物理地址(scatter-gather DMA)。

NIC 不支持 SG-DMA 时,内核**退化为 1 次内核→sk_buff 拷贝**,仍比传统 read+write 少 1 次。

### 函数签名

```c
#include <sys/sendfile.h>
ssize_t sendfile(int out_fd, int in_fd, off_t *offset, size_t count);
```

- `out_fd`:socket(早期 2.6.33 前唯一选项)或任何 file(2.6.33+;pipe 在 5.12+)
- `in_fd`:必须是 `mmap(2)`-able 的文件(普通文件、块设备;**不是 socket**)
- `offset`:NULL = 从当前文件偏移开始;非 NULL = 显式 offset 且**不修改文件偏移**,退出时**更新为下一次起始**;`ESPIPE` 若文件不可 seek
- `count`:最多 0x7ffff000 (≈2 GiB) 字节;**返回实际拷贝**字节数;若 < count 调用方需重试

### 限制与降级

| 限制 | 说明 |
| --- | --- |
| `in_fd` 不能是 socket | 文件 mmap 限制 |
| 早期 out_fd 仅限 socket | 2.6.33 后可任意 |
| 单次最大 `count` | `0x7ffff000`(2 GiB) |
| `in_fd` 被修改 | 接收方需保留 page cache 直到它消费完 |

如果 sendfile 失败 `EINVAL` 或 `ENOSYS`,应用应**降级**到 read+write。

### `splice(2)` 补充

- 数据流 **file ↔ pipe ↔ socket** 全互转
- 至少一个 fd 必须是 pipe
- 在 NIC 不支持 SG 时,splice 通常比 sendfile 更优(因为 pipe 也是 page-ref 形式)
- Linux 5.12+ sendfile 自动 desugar 到 splice(in_fd=pipe)

### `copy_file_range(2)`

文件→文件零拷贝,Btrfs/XFS **reflink** 时瞬时完成(extent 共享,COW 触发才物理拷贝)。

## 对比 / 选型

| 场景 | syscall | 拷贝次数 | 复杂度 |
| --- | --- | --- | --- |
| 文件 → TCP socket | **sendfile** | 0 CPU(有 SG)/ 1 内核 | 简单 |
| 文件 → pipe / pipe → socket | **splice** | 0 CPU | 中等 |
| 文件 → 文件(同 FS) | **copy_file_range** | 0 CPU(reflink) / 1 | 简单 |
| 文件 → 文件(跨 FS) | sendfile + read+write | 多次 | 降级 |
| 用户构造数据 → socket | write + TCP_CORK | ≥1 CPU | 简单 |
| **跨平台**(macOS/BSD) | 无 sendfile wrapper → `sendfile()` 仍在但语义差异 | 1 | 中等 |

Nginx 的 `sendfile on;` 默认开启,搭配 `tcp_nopush on;` 让大文件落 1 段。

## 环境准备

- **C**:Linux 2.6.33+(`SYS_SENDFILE`)
- **Python**:3.3+,Linux 上 `os.sendfile` 可用;Windows 降级到 `read+write`
- **Go**:1.18+(Linux `syscall.Sendfile`)

## 运行方式

### C(Linux)

```bash
gcc -O2 -Wall -Wextra c/sendfile_server.c -o sendfile_server
./sendfile_server 9000 1024      # 1 MiB file
# Client: nc 127.0.0.1 9000 ; press enter -> receives ~1 MiB
```

### Python

```bash
python3 python/sendfile_server.py 9000 1024
```

### Go(Linux)

```bash
cd go && go run sendfile_server.go 9000 1024
```

## 关键代码片段

### C — 用户态 header + 内核态 body

```c
char hdr[128];
int hlen = snprintf(hdr, sizeof(hdr),
                    "X-Source: sendfile\r\nContent-Length: %zu\r\n\r\n",
                    file_size);
writev(client_fd, (struct iovec[]){{ hdr, hlen }}, 1);     /* header */

off_t offset = 0;
while (remaining > 0) {
    ssize_t s = sendfile(client_fd, file_fd, &offset, remaining);
    if (s == 0) break;
    remaining -= (size_t)s;
}
```

### Python — `os.sendfile` wrapper

```python
header = f"X-Source: sendfile\r\nContent-Length: {file_size}\r\n\r\n".encode()
client.sendall(header)

sent, offset = 0, 0
while sent < file_size:
    n = os.sendfile(client.fileno(), file_fd, offset, file_size - sent)
    sent += n; offset += n
```

### Go — `syscall.Sendfile`

```go
header := fmt.Sprintf("X-Source: sendfile\r\nContent-Length: %d\r\n\r\n", fileSize)
c.Write([]byte(header))                              // header
syscall.Sendfile(c.(*net.TCPConn).Fd(), file.Fd(), nil, fileSize)  // body
```

## 性能与边界

- **单次 sendfile 最多 `0x7ffff000` ≈ 2 GiB**;跨越大文件 loop 重试
- **EAGAIN** 出现在 socket 缓冲满;非阻塞 sendfile 由 `EAGAIN` 提示"稍后再试"
- **TCP_CORK** 合并紧接其后的 write,确保 header + 第 1 段 sendfile 在 1 个 TCP 段中发出
- **吞吐量提升**:静态文件场景 read+write → sendfile 通常快 1.5x-3x(实测 Nginx benchmark)
- **CPU 占用**:无 SG-DMA 的 NIC 上少 1 次拷贝 200 MB 文件,主频 modern CPU 可见节省 ~30ms

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `EINVAL` | in_fd 不可 mmap(socket) 或 count 负 | 改用 splice(read+write 降级) |
| `ENOSYS` | 内核无 sendfile | 降级 read+write |
| `EAGAIN` 非阻塞 | socket buffer 满 | 用 epoll 重试或 O_NONBLOCK 客户端 |
| `ESPIPE` | 文件不可 seek + offset 非 NULL | 置 offset=NULL 让 sendfile 用文件偏移 |
| 文件被截短 | offset 越界 | 调用前 stat 显式 size |
| 多线程共享 file_fd | offset 是 per-fd | 用 pread + 显式 offset |
| TCP 段扎堆 | header + body 分开发送 | 用 TCP_CORK 合并 |
| 跨平台 panic | macOS sendfile 协议不同 | try/except 降级到 `os.read` + `client.sendall` |

## 参考资料

- [sendfile(2) Linux man page](https://manpages.org/sendfile/2) — **权威**,in_fd 必须是 mmap-able,count 0x7ffff000 上限,SPLICE_F_MOVE 等关联
- [Zero-copy I/O: splice, sendfile, and friends - kernel-internals.org](https://kernel-internals.org/io/splice-sendfile/) — 教科书级讲解,带 ASCII 数据流图 + nginx 配置
- [splice, sendfile, and copy_file_range - kernel-internals.org/vfs](https://kernel-internals.org/vfs/splice-sendfile) — VFS 视角,tee()/splice() 协同 + Btrfs reflink
- [Linux 高级 I/O 函数 sendfile() - cnblogs](https://www.cnblogs.com/fortunely/p/16211187.html) — 中文实战代码 + 与 read+write 比较
