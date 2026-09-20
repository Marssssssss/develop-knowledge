# 非阻塞 IO 与 EAGAIN 语义

把 `O_NONBLOCK` 之后那句「all operations that would block will (usually) return with EAGAIN」拆成可逐条验证的判据。重点不是「非阻塞更快」这种口号，而是**返回值语义变了以后，上层循环该怎么写才不错**。

## 一、原理详解

### 1.1 非阻塞是 fd 上的一个标志，不是一种调用

`socket(7)` 原文：*"It is possible to do nonblocking I/O on sockets by setting the `O_NONBLOCK` flag on a socket file descriptor using `fcntl(2)`. Then all operations that would block will (usually) return with `EAGAIN` (operation should be retried later)"*。

三个容易被忽略的点：

1. **它是 fd 属性**，一旦设置，`recv` / `send` / `accept` / `connect` 全部受影响 —— 不存在「只让读非阻塞」这回事。
2. **`(usually)` 这个限定词是规范的**：`connect()` 的路径就不返回 `EAGAIN` 而是 `EINPROGRESS`。
3. **`MSG_DONTWAIT` 是按调用的覆盖位**。`recv(2)`：*"Enables nonblocking operation; if the operation would block, the call fails with `EAGAIN` or `EWOULDBLOCK`. This provides similar behavior to setting..."* —— 阻塞 fd 上单次调用也能临时变成非阻塞，**反之不成立**（非阻塞 fd 没法靠 flag 变回阻塞）。

demo 里 `_nonblocking_now(s, flags)` 这一行就是全部规则的浓缩：`s.nonblock or (flags & MSG_DONTWAIT)`。

### 1.2 `EAGAIN` 与 `EWOULDBLOCK`：同一编号，两种措辞

`read(2)` 的 ERRORS 章节把 fd 分成两类：

| fd 类型 | 原文 | 编号（Linux/x86-64） |
| --- | --- | --- |
| 非 socket | `EAGAIN` | 11 |
| socket | `EAGAIN or EWOULDBLOCK` | 11 |

在 Linux 上两者**是同一个数**，所以 `switch (errno) { case EAGAIN: case EWOULDBLOCK: ... }` 会编译报重复 case。但 POSIX 只要求 `EAGAIN` 存在，`EWOULDBLOCK` 允许取不同值（历史上某些 UNIX 如此），因此**可移植代码必须两个都判**，只是同一个分支里。

自检里的负向判据是「errno 恰为 `EAGAIN` 且不为 5（`EIO`）」—— 光断言「返回了错误」是没有鉴别力的。

### 1.3 短读：recv 绝不等满你要的长度

`recv(2)`：*"The receive calls normally return any data available, up to the requested amount, rather than waiting for receipt of the full amount requested."*

这行决定了所有非阻塞读循环的形状：

```python
while len(got) < want:
    r = recv(fd, want - len(got))
    if r.ok:
        got += r.val          # 可能只有 1 字节
    elif r.errno == EAGAIN:
        break                 # 不是错误，是「现在没有更多了」
    else:
        raise OSError(r.errno)
```

把 `if len(chunk) < n: break` 写成「对端关了」是错的：短读只说明**此刻内核缓冲里就这么多**。要区分「对端关闭」只能看返回值 `0`。

### 1.4 部分写：send 的返回值是字节数

同理，`send(2)` 在发送缓冲只剩 100 字节而你写 300 字节时**返回 100**，不是报错也不是全写。非阻塞下缓冲满则 `EAGAIN`。

自检里专门断言「缓冲满 + 非阻塞 → `EAGAIN`，**不是 0 字节成功**」：这两者在上层看来天差地别，一个要重试，一个会被误当成写成功而丢数据。

### 1.5 非阻塞 connect：三态机 + SO_ERROR

| 调用时刻 | 返回 |
| --- | --- |
| 首次发起 | `EINPROGRESS`（不是 0，也不是 `EAGAIN`） |
| 握手未完成又调一次 | `EALREADY` |
| 已建立后再调 | `EISCONN` |

关键后果：**`connect()` 返回 `EINPROGRESS` 时连接既没成功也没失败**。成功与否只能等 fd 变可写后 `getsockopt(SO_ERROR)` 取 —— `connect` 的返回值完全不携带这个信息。demo 里 `getsockopt_so_error` 返回 `ECONNRESET` 的那条断言就是这个意思。

### 1.6 非阻塞本身不省系统调用，就绪通知才省

这是最容易被搞混的一层。设 `O_NONBLOCK` 只让调用**不挂起**，如果上层靠 `while True: recv()` 硬转，那么每一次 `EAGAIN` 都是一次**真实的用户态/内核态切换**。

demo 用同一份到达剧本对比：

| 写法 | 结果 | 等待次数 |
| --- | --- | --- |
| 忙轮询（`O_NONBLOCK` + 重试） | 200 字节 | 7 次空转 |
| 就绪通知（`epoll_wait` + 读） | 200 字节 | 1 次挂起 |

**数据面完全等价**（`got == got9` 断言），差的只是等待方式。真正的性能来源是 `epoll`/`io_uring` 提供的「一次等待多个 fd」，而不是 `O_NONBLOCK` 本身 —— 后者只是让「不挂起」成为可能的前提。

## 二、与相关机制的对比

| 机制 | 谁在等 | 一次能等几个 fd | 典型误用 |
| --- | --- | --- | --- |
| 阻塞 IO | 线程在内核里挂起 | 1 | 一个连接一个线程，C10K 下线程开销爆炸 |
| `O_NONBLOCK` + 忙轮询 | 谁都不等，CPU 空转 | N（但要挨个试） | 把 `EAGAIN` 当错误处理直接断开 |
| `select`/`poll` | 线程挂起在内核 | N | 每次调用都要重传整个 fd 集合 |
| `epoll` | 线程挂起在内核 | N | 忘了 `EPOLLONESHOT` / 没处理 `EPOLLHUP` |
| `io_uring` | 可完全不进内核 | N | 见 `IO多路复用/io_uring网络IO/` |

## 三、环境要求

- Python 3.8+（仅标准库）
- Go 1.18+（`errors.As`；本机无 Go 工具链，代码为人工审查 + 结构校验）

## 四、运行方式

```bash
cd 非阻塞Socket
python selfcheck_nbio.py     # 33 项断言
go run nbio.go               # Go 版 24 项断言
```

## 五、关键代码

```python
def recv(net, s, n, flags=0):
    net._enter()
    if not s.rcv:
        if _nonblocking_now(s, flags):
            return Err(EAGAIN)        # 不挂起，交给调用方
        net._block(s)                 # 阻塞 fd：挂起等数据
        if not s.rcv:
            return Err(EAGAIN)
    data = bytes(s.rcv[:n])           # 短读：有多少给多少
    del s.rcv[:n]
    return Ok(data)
```

Go 版把 `errno` 单通道换成 `(值, error)` 双通道：

```go
func Recv(n *Net, s *Sock, want int, flags int) ([]byte, error) {
	if len(s.Rcv) == 0 {
		if nonblockingNow(s, flags) {
			return nil, Errno(EAGAIN)
		}
		n.block(s)
	}
	k := want
	if len(s.Rcv) < k { k = len(s.Rcv) }
	out := make([]byte, k)
	copy(out, s.Rcv[:k])
	s.Rcv = s.Rcv[k:]
	return out, nil
}
```

## 六、性能边界

- **`EAGAIN` 不是错误，是状态**：把它当异常处理会切断本来健康的连接。实测中一次 200 字节的接收产生了 5 次 `EAGAIN`，全部是正常现象。
- **忙轮询的代价随并发线性放大**：N 个连接各转一圈就是 N 次系统调用；`epoll_wait` 一次返回全部就绪 fd。
- **`MSG_DONTWAIT` 有成本**：它是一次额外参数传递，在已经非阻塞的 fd 上是冗余；只在「同一 fd 上绝大多数时候想阻塞、偶尔不想」的场合才划算。
- 真正的零等待要等 `io_uring` + `IORING_SETUP_SQPOLL`，见同大类下的 `io_uring` demo。

## 七、注意事项与常见坑

1. **`EAGAIN` ≠ `EWOULDBLOCK` 在跨平台代码里不能假设相等**。Linux 相等，POSIX 不保证。
2. **非阻塞是 fd 属性，不是连接属性**。`dup()`、`fork()`、以及 Unix socket 的 `SCM_RIGHTS` 传 fd，传出去的是**同一个 open file description**，`O_NONBLOCK` 一起被共享（这与 `SCM_RIGHTS` demo 里偏移量共享是同一个道理）。
3. **`accept` 返回 `EAGAIN` 时不能认为监听 socket 坏了**，只是完成队列空了。
4. **非阻塞 connect 后必须配 epoll 等可写事件再取 `SO_ERROR`**，轮询 `connect()` 直到返回 `EISCONN` 是很常见的反模式。
5. **别把短读当 EOF**。返回 `0` 才是对端关闭。
6. 本 demo 只建模返回值语义，**不涉及真实内核时序**（`wait`/`spin` 计数是模型内的记账，不是实测耗时）。

## 八、参考资料

- `read(2)` — https://man7.org/linux/man-pages/man2/read.2.html （ERRORS：`EAGAIN` / `EAGAIN or EWOULDBLOCK` 两条）
- `recv(2)` — https://man7.org/linux/man-pages/man2/recv.2.html （短读语义、`MSG_DONTWAIT`）
- `socket(7)` — https://man7.org/linux/man-pages/man7/socket.7.html （`O_NONBLOCK` 与 `SO_RCVBUF` 翻倍记账）
- `io_uring(7)` — https://man7.org/linux/man-pages/man7/io_uring.7.html （对比用的「零系统调用」路径）
