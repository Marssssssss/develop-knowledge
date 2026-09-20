# io_uring 网络 IO：与 epoll 的取舍

`epoll` 已经不阻塞了，io_uring 的卖点不是「更不阻塞」，而是**把系统调用本身也省掉**。这个 demo 把 SQ/CQ 两个环形队列、CQE 的 `res` 语义、以及「完成顺序不可假设」这几条拆成可验证的判据。

## 一、原理详解

### 1.1 通信方式从「系统调用」换成「共享内存环形队列」

`io_uring(7)` 开篇：

> *"io_uring gets its name from ring buffers which are shared between user space and kernel space. This arrangement allows for efficient I/O, while avoiding the overhead of copying buffers between them, where possible."*

编程模型四步：

1. `io_uring_setup(2)` 拿到 `ring_fd`，再 `mmap` 出 SQ 和 CQ（`IORING_OFF_SQ_RING` / `IORING_OFF_CQ_RING` / `IORING_OFF_SQES`）。
2. 每个想要的操作填一个 **SQE**，加到 SQ **尾部**。
3. `io_uring_enter(2)` 通知内核从 SQ **头部**取走。
4. 内核把**恰好一个** CQE 加到 CQ **尾部**，应用从 CQ **头部**读。

方向别记反：`io_uring(7)` 原文 *"You add SQEs to the tail of the SQ. The kernel reads SQEs off the head of the queue"*、*"The kernel adds CQEs to the tail of the CQ. You read CQEs off the head"*。

`io_uring_setup(2)` 给出的 mmap 写法里有个细节：SQ 映射长度是 `sq_off.array + sq_entries * sizeof(__u32)` —— 因为**环结构位于整个结构体的末尾**，所以要加上 `sq_off.array` 这个偏移。

### 1.2 res 字段：errno 在 io_uring 里根本不存在

> *"Given that io_uring is an async interface, errno is never used for passing back error information. Instead, res will contain what the equivalent system call would have returned in case of success, and in case of error res will contain -errno."*

| 场景 | 传统 syscall | io_uring CQE.res |
| --- | --- | --- |
| `read` 读到 1024 字节 | 返回 1024 | `res = 1024` |
| `read` 失败 `EINVAL` | 返回 -1，`errno = EINVAL` | `res = -EINVAL`（即 -22） |

**这是最容易写错的一处**：`if (cqe->res < 0)` 才算失败，而**绝不是** `res == -1`。demo 断言了成功时 `errno == 0`、失败时 `errno == -res`。

### 1.3 完成顺序不可假设 —— user_data 是必需品

> *"I/O requests submitted to the kernel can complete in any order ... When you dequeue CQEs off the CQ, you should always check which submitted request it corresponds to. The most common method for doing so is utilizing the user_data field in the request, which is passed back on the completion side."*

demo 用注入乱序的方式做了**双向验证**：

- 负向：`CQE 顺序 != 提交顺序` 这条**必须失败**（证明乱序真的注入了）；
- 负向：按位置猜测 `user_data` 会错配；
- 正向：按 `user_data` 建索引后，每个请求仍能取到自己的结果。

如果第一条负向断言「意外通过」，说明乱序注入没生效，整个顺序实验就是假的 —— 这正是「通过 ≠ 验到」的典型例子。

### 1.4 流式 socket 上的同方向重叠是危险的

> *"for sends and receives on a stream-oriented TCP socket, it is generally unsafe to have more than one outstanding send, or more than one outstanding receive (the two directions are independent) on a given socket at a time, as the kernel may reorder their execution if poll arming or other background kernel activities are involved."*

三点要读准：

1. **读和写两个方向是独立的** —— 一个在飞的 send + 一个在飞的 recv 没问题。
2. **同方向两个在飞才危险**：两个 send 的执行顺序可能被颠倒，TCP 字节流就乱了。
3. **io_uring 不会替你拒绝**：demo 断言了两个 send 都成功进了 SQ，即内核不报错 —— 这是应用的责任。

解决办法是 `IOSQE_IO_LINK`：

> *"If the requests are submitted in a single batch, the application may use IOSQE_IO_LINK to enforce an execution order in the kernel."*

`IOSQE_IO_LINK` 把该 SQE 与**下一个**绑成一条链，链内严格按提交顺序执行。demo 里 4 个 SQE 全部打上 LINK 后，`exec_log` 恒为 `[0,1,2,3]`；不打 LINK 且注入乱序时完成顺序变成 `[2,1,0]`。

注意原文还补了一句：*即便用了这些特性，应用仍必须保证不在同一个文件上重叠不同的 send 或 receive* —— LINK 保的是执行顺序，不能用来把两个并发 send 变得「安全」。

### 1.5 SQPOLL：真正的一次系统调用都不做

> *"When this flag is specified, a kernel thread is created to perform submission queue polling ... the application can submit and reap I/Os without doing a single system call."*

代价是内核线程会睡：

> *"If the kernel thread is idle for more than sq_thread_idle milliseconds, it will set the IORING_SQ_NEED_WAKEUP bit in the flags field of the struct io_sq_ring. When this happens, the application must call io_uring_enter(2) to wake the kernel thread."*

所以正确的提交序列是「先填 SQ，再检查 `IORING_SQ_NEED_WAKEUP`，置位了才进内核」。demo 断言了三种状态：

| 状态 | 系统调用次数 |
| --- | --- |
| 线程醒着，轮询线程自己消费 SQ | **0** |
| 线程睡了，应用 `io_uring_enter` 唤醒 | 1 |
| 普通（无 SQPOLL）模式每批提交 | 1 |

### 1.6 IOPOLL 是给块设备 O_DIRECT 的，不是给网络的

> *"Currently, this feature is usable only on a file descriptor opened using the O_DIRECT flag (if using the IORING_OP_{READ,WRITE}(V)(_FIXED) opcodes)."*

很多人想当然地给网络环加 `IORING_SETUP_IOPOLL`。demo 里模型直接拒绝在 IOPOLL 环上准备网络 opcode —— **网络的低延迟路径是 SQPOLL，不是 IOPOLL**。

### 1.7 系统调用次数的量级差异

| 路径 | 4 个连接、4 次事件的系统调用数 |
| --- | --- |
| `epoll` | `epoll_ctl`×4 + `epoll_wait`×4 + `recv`×4 = **12** |
| `io_uring` | 4 个操作合成 1 批 → **1** |

关键不是常数因子，而是**随批量线性增长 vs 恒定**：`epoll` 每个事件至少一次 `recv`；`io_uring` 把 N 个 SQE 塞进一个 `io_uring_enter`。`io_uring_enter(2)` 的 `min_complete` + `IORING_ENTER_GETEVENTS` 还能让「提交」和「等待完成」合并在一次调用里。

## 二、与 epoll / 非阻塞 IO 的对比

| | 阻塞 IO | `epoll` + 非阻塞 | `io_uring` |
| --- | --- | --- | --- |
| 谁等 | 线程在内核挂起 | 线程在 `epoll_wait` 挂起 | 可完全不等（SQPOLL） |
| 每次 IO 的系统调用 | 1 | `epoll_wait` + `recv` = 2 | 1/N（批量摊薄） |
| 报错通道 | 返回值 + errno | 返回值 + errno | **`res` 单通道**（负数即 -errno） |
| 请求关联 | 天然同步 | fd → 连接 | **`user_data` 必须自己管** |
| 顺序保证 | 有 | 有 | **无**（要 LINK） |
| 适合 | 低并发 | 万级连接、通用 | 极高 IOPS、延迟敏感 |

## 三、环境要求

- Linux 5.10+（SQPOLL / 多数网络 opcode 需较新内核；本 demo 为模型，可在任意平台运行）
- Python 3.8+（仅标准库）
- Go 1.18+（本机无 Go 工具链，人工审查 + 结构校验；运行用 `go run .`，两个 `.go` 同属 `package main`）

## 四、运行方式

```bash
cd io_uring网络IO
python selfcheck_iouring.py    # 36 项断言
go run .                       # Go 版 22 项断言
```

## 五、关键代码

```python
def submit(self, min_complete=0, flags=0):
    """io_uring_enter(2)：一次系统调用同时提交并（可选）等待完成。"""
    self.syscalls += 1
    self.enters += 1
    self.thread_idle_since = 0.0
    self.thread_asleep = False
    self.sq_flags &= ~IORING_SQ_NEED_WAKEUP
    return self._run_kernel(min_complete, flags)

@staticmethod
def _split_chains(batch):
    """IOSQE_IO_LINK 把 SQE 与**下一个**绑成链；未链接的自成一组。"""
    chains, cur = [], []
    for i, sqe in enumerate(batch):
        cur.append(sqe)
        if (sqe.flags & IOSQE_IO_LINK) and i != len(batch) - 1:
            continue
        chains.append(cur)
        cur = []
    if cur:
        chains.append(cur)
    return chains
```

链的**切分**是这个模型的核心：链内保序，链间（未链接的组之间）允许乱序 —— 这正好复现了「LINK 保证执行顺序，但不阻止独立请求之间乱序」。

## 六、性能边界

- **批量越大越划算**：单次 `io_uring_enter` 的固定开销被 N 个 SQE 摊薄。但批量会**增加尾延迟**（要等凑够一批），延迟敏感场景要设上限。
- **SQPOLL 的代价是一整个核**：轮询线程持续占用 CPU。低负载场景反而不如普通模式省电。
- **`sq_thread_idle` 默认通常是几百毫秒到 1 秒**，调太小会频繁唤醒，调太大会让首次提交延迟变高。
- **挂起操作会占用内核资源**：demo 的 `pending` 队列就是对应物，无界堆积会耗尽内核的 SQE 池。
- 本 demo 只建模队列与状态机，**不涉及真实内核时序与实测吞吐**。

## 七、注意事项与常见坑

1. **`res < 0` 才是失败**，别写 `res == -1`；也别去读 `errno`（io_uring 根本不设它）。
2. **必须用 `user_data` 关联请求**。按 CQ 顺序猜是哪个请求，在乱序下会静默错配。
3. **同一 socket 上不要同时挂两个 send / 两个 recv**；读与写两个方向可以同时挂。
4. **`IOSQE_IO_LINK` 只保执行顺序**，不等于「两个并发 send 就安全了」。
5. **`IORING_SETUP_IOPOLL` 不能用于网络**，那是 O_DIRECT 块设备的东西。
6. **SQPOLL 下提交前要查 `IORING_SQ_NEED_WAKEUP`**，置位了才调 `io_uring_enter`，否则你的 SQE 会一直躺在 SQ 里。
7. **`-EAGAIN` 也可能出现在 `res` 里**（比如配合 poll 相关的语义），要当成「重试」而不是「致命错误」。
8. **CQ 溢出**：内核会记 `io_cqring_offsets.overflow`，CQE 数超过 `cq_entries` 时会丢；`IORING_SETUP_CQSIZE` 可显式指定，值**必须大于 `entries`** 且会被向上取整到 2 的幂。

## 八、参考资料

- `io_uring(7)` — https://man7.org/linux/man-pages/man7/io_uring.7.html （编程模型、res/-errno、完成顺序、同方向重叠警告、`IOSQE_IO_LINK`）
- `io_uring_setup(2)` — https://man7.org/linux/man-pages/man2/io_uring_setup.2.html （`io_uring_params`、`IORING_SETUP_SQPOLL` / `IOPOLL` / `CQSIZE`、mmap 长度写法）
- `io_uring_enter(2)` — https://man7.org/linux/man-pages/man2/io_uring_enter.2.html （`to_submit` / `min_complete` / `IORING_ENTER_GETEVENTS`、各 opcode）
- `socket(7)` — https://man7.org/linux/man-pages/man7/socket.7.html （对照：传统非阻塞路径）
