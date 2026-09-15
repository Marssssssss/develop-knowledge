# SCM_RIGHTS：在进程之间传递文件描述符

## 简介

`SCM_RIGHTS` 是 `AF_UNIX` socket 的控制消息类型，用来把**文件描述符**从一个进程交给
另一个进程。它是 Unix 世界最优雅的 IPC 原语之一：接收方拿到的是**对同一个打开文件
描述（open file description）的引用**，偏移量、状态、锁全部共享 —— 而不是"打开同一个
路径"。

最常被写错的三件事：

1. 以为传的是 fd **编号**（实际编号由接收方自己分配，两边通常不同）；
2. 以为传 `fd` 就行（**流式 socket 上必须同时发送 ≥1 字节真实数据**）；
3. 以为控制缓冲按 `sizeof(int) * n` 预留就够（少算一个对齐填充就一个 fd 都收不到）。

典型用途：`systemd`/`nginx` 的监听套接字继承、`sd_notify` 的 `FDSTORE`、seccomp 用户
通知（`seccomp_unotify`）、把 `pidfd` 或已打开的设备交给降权子进程。

## 原理详解

### 1. 传的是 open file description 的引用，不是 fd 号

man7 `unix(7)` 原话：

> what is being passed is a reference to an **open file description** … and in the
> receiving process it is likely that a different file descriptor number will be
> used. Semantically, this operation is equivalent to duplicating (**dup(2)**) a
> file descriptor into the file descriptor table of another process.

```
发送方: fd=3 ─┐
              ├─► 同一个 OFD（偏移量=5，引用计数=2）
接收方: fd=5 ─┘   对照：按路径自己 open → 另一个 OFD（偏移量=0）
```

**可观测结论**：发送方读走 5 字节后传 fd，接收方刚拿到时偏移就是 **5**，接着读到第 5 字节
之后的内容（实测 `-WORL`）；而按路径 re-open 的那份从偏移 0 读到 `HELLO`。发送方 `close`
也不影响接收方 —— 两张表各持一份引用（实测 refcount 2 → 1）。

### 2. 控制消息的布局与三个尺寸宏

控制数据是一串 `cmsghdr`：`cmsg_len`（含头的字节数）+ `cmsg_level`（`SOL_SOCKET`）+
`cmsg_type`（`SCM_RIGHTS`）+ 后跟 `int[]` fd 数组。传 1 个 fd 时（x86-64）：

| 宏 | 值 | 含义 |
| --- | --- | --- |
| `sizeof(struct cmsghdr)` | 16 | `size_t`(8) + `int`(4) + `int`(4) |
| `CMSG_LEN(4)` | **20** | 写进 `cmsg_len` 的值，**不含**尾部填充 |
| `CMSG_SPACE(4)` | **24** | 这一项**实际占用**的缓冲字节数，**含**填充 |

反直觉的结果：**对齐粒度 8 字节，而每个 fd 只占 4 字节**，所以
`CMSG_SPACE(4) == CMSG_SPACE(8) == 24` —— **1 个 fd 和 2 个 fd 占的缓冲一样大**。
按 `CMSG_LEN`（20）预留缓冲，内核认为放不下，直接截断成 0 个 fd。

### 3. 三条硬约束

| 约束 | 出处 | 违反时 |
| --- | --- | --- |
| 流式 socket 必须夹带 **≥1 字节真实数据** | `unix(7)`："At least one byte of real data should be sent when sending ancillary data" | `EINVAL`（实测） |
| 单次最多 **`SCM_MAX_FD` = 253** 个 fd（Linux < 2.6.38 为 255） | `unix(7)` | `EINVAL`（实测 254 个即失败） |
| 发送的 fd 必须有效 | `unix(7)` | `EBADF` |

数据报（`SOCK_DGRAM`）上不强制夹带数据（man7 仍建议带上，为了可移植）。**接收侧**同样有
约束：`SOCK_STREAM` 上必须在**同一个 `recvmsg`** 里收到 ≥1 字节非辅助数据，否则控制数据
不交付。

### 4. 控制数据是字节流的屏障

man7 的例子：发送方依次 `sendmsg` 4 字节（无控制）、1 字节（带 fd）、4 字节（无控制）；
接收方每次用 20 字节缓冲 `recvmsg`，得到：

```
第 1 次 → "AAAAB"（5 字节）+ 那 1 个 fd      ← 屏障：到此为止
第 2 次 → "CCCC"
```

即**带控制数据的记录会把字节流截断**：前面积压的字节与它一起交付，但它后面的字节绝不会
被顺带吞进来。另外 `sendmsg` 每次**每种控制类型最多带一项**（`unix(7)`："only one item
of each of the above types may be included"）。

### 5. 溢出与限流的四种处理

| 情况 | 行为 | 标志 / 错误 |
| --- | --- | --- |
| 控制缓冲装不下全部 fd | 截断，**多余的 fd 在接收进程里被自动关闭** | 置 `MSG_CTRUNC` |
| 接收方 `RLIMIT_NOFILE` 不够 | 装不下的 fd **被自动关闭** | man7 只说"自动关闭"，**未规定**置标志 |
| 发送方在途（in-flight）fd 超 `RLIMIT_NOFILE` | `sendmsg` 失败（Linux ≥ 4.5 起诊断） | `ETOOMANYREFS`（除非有 `CAP_SYS_RESOURCE`） |
| 收到的新 fd 要 `FD_CLOEXEC` | 用 `MSG_CMSG_CLOEXEC`（2.6.23+）**原子设置** | 否则 `recvmsg` 后再 `fcntl`，中间有 exec 泄漏窗口 |

"在途 fd" 指已 `sendmsg` 但尚未被对端 `recvmsg` 接走的那些。4.5 之前靠"发完立刻 `close`
本地 fd"可绕过记账，在途放无限多个 fd —— 本 demo 的模型正是在复现这个成因。

## 对比 / 选型

| 方案 | 共享什么 | 代价 |
| --- | --- | --- |
| **`SCM_RIGHTS` 传 fd** | 同一个 OFD（偏移、状态、锁全共享） | 仅限本机；接收方要自己管 `CLOEXEC` |
| 传路径，对端自己 `open` | 只有文件本身 | 偏移独立；需权限；有 TOCTOU 风险 |
| `memfd_create` + 传 fd | 同一块内存 | 要自己做同步 |
| 传 `pid` 再 `ptrace`/发信号 | 什么都没有 | 需权限；有 PID 复用竞态（`pidfd` 正为此而生） |

## 环境准备

- Python 版：跨平台（纯模型，3.8+，仅标准库），**推荐先跑它**。
- C / Go 版：Linux（`AF_UNIX` + `SCM_RIGHTS` 真实系统调用；`MSG_CMSG_CLOEXEC` 亦然）。
- 无需第三方依赖。

## 运行方式

```bash
# Python：34 项断言
cd python && python3 main.py

# C：真实 sendmsg/recvmsg（scenarios_impl.h 由 main.c 原地 #include，只编译这两个 .c）
cd c && gcc -std=c11 -Wall -Wextra -pedantic -O2 main.c scm_fd.c -o scm_demo && ./scm_demo

# Go：syscall.UnixRights/ParseUnixRights（同包多文件，用 . 让 go 收全包）
cd go && go run .
```

## 关键代码片段

```c
/* 发送：控制缓冲包在 union 里借它的对齐，且必须先清零（CMSG_NXTHDR 依赖） */
union { char buf[CMSG_SPACE(sizeof(int))]; struct cmsghdr align; } u;
memset(&u, 0, sizeof(u));
msg.msg_control    = u.buf;
msg.msg_controllen = sizeof(u.buf);          /* ← CMSG_SPACE，不是 CMSG_LEN */
cmsg = CMSG_FIRSTHDR(&msg);
cmsg->cmsg_level = SOL_SOCKET;
cmsg->cmsg_type  = SCM_RIGHTS;
cmsg->cmsg_len   = CMSG_LEN(sizeof(int));
memcpy(CMSG_DATA(cmsg), &fd, sizeof(fd));    /* CMSG_DATA 不保证对齐，用 memcpy */
```

```python
# 接收侧：装配的 fd 数由【控制缓冲】决定，装不下的会被内核自动关闭
fit = fds_fit(ctrl_buf_size, nfds)           # 依据 CMSG_SPACE，不是 4*nfds
if fit < nfds:
    msg_flags |= MSG_CTRUNC
cloexec = bool(flags & MSG_CMSG_CLOEXEC)
```

## 性能与边界

本 demo（Python 版，34 项断言全通过）实测：

**控制缓冲的真实容量**（传 1 个 fd）：

| `msg_controllen` | 4 | 20 | 23 | 24（=CMSG_SPACE） | 32 | 48 | 1032 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 装下几个 fd | **0** | **0** | **0** | **2** | 4 | 8 | 253 |

n 个 fd 需要 `CMSG_SPACE(4n)`（`1→24 2→24 3→32 4→32 5→40 6→40 7→48 8→48`）：按
`sizeof(int)` 或 `CMSG_LEN` 预留都会**一个 fd 都收不到**，而且**不报错**。

**截断与限流的引用计数变化**（OFD refcount）：

| 场景 | 发送前 → 接收后 | 结论 |
| --- | --- | --- |
| 3 个 fd，缓冲只够 2 个 | `[2,2,2]` → `[2,2,1]` | 第 3 个丢了在途引用、没建新引用 |
| 同上但发送方先 `close` | `[1,1,1]` → `[1,1,0]` | **最后一份引用消失 ⇒ OFD 当场释放** |
| 接收方 `RLIMIT_NOFILE=3`、10 个 fd | 装 2 个、自动关闭 8 个 | `recvmsg` **不报错** |

**在途 fd 记账**（发送方 `RLIMIT_NOFILE=8`，发完即 `close`）：前 8 次 `sendmsg` 全部成功
且发送方 fd 表恒为 0 项、在途量从 1 涨到 8；**第 9 次报 `ETOOMANYREFS`**。

## 注意事项与常见坑

1. **别用 `CMSG_LEN` 预留接收缓冲**：`CMSG_LEN` 是"写进 `cmsg_len` 的值"，`CMSG_SPACE`
   才是"要占的字节数"。少算 4 字节 → 一个 fd 都收不到，且不报错只置 `MSG_CTRUNC`。
2. **对齐粒度 8 字节、一个 fd 只占 4 字节**：1 个和 2 个 fd 都要 24 字节。想按字节数反推
   容量会算错 —— 用 `CMSG_SPACE` 正推，或干脆按最大 fd 数一次性预留 1032 字节。
3. **`CMSG_FIRSTHDR`/`CMSG_NXTHDR` 要求控制缓冲先清零**（cmsg(3) 明确要求），否则
   `CMSG_NXTHDR` 可能走进未初始化内存；而 **`CMSG_DATA` 返回的指针不能假定对齐**
   （cmsg(3)：*cannot be assumed to be suitably aligned*）—— 用 `memcpy`，别强转 `int *`。
4. **`MSG_CTRUNC` 不告诉你丢了多少**：只能数 `SCM_RIGHTS` 项的 `cmsg_len`；而"因
   `RLIMIT_NOFILE` 丢 fd"连 `MSG_CTRUNC` 都**不保证**置位（man7 未规定）。
5. **被截断的 fd 是在接收方被关闭的**，不会退回发送方。若发送方已先 `close`，那份引用
   就是最后一份，OFD 当场释放 —— 这也正是"传完就 close"安全的原理。
6. **`MSG_CMSG_CLOEXEC` 应是默认姿势**：先 `recvmsg` 再 `fcntl(FD_CLOEXEC)` 之间存在窗口，
   此时若其他线程 `fork + exec`，fd 会泄漏进子进程。该 flag 让内核原子地设好。
7. **接收侧也要"夹带真实数据"**：同一个 `recvmsg` 必须读到 ≥1 字节，否则控制数据不交付
   （见 §3 的三条硬约束）。

## 参考资料（实际阅读过的权威来源）

- [unix(7) — Linux manual page](https://man7.org/linux/man-pages/man7/unix.7.html)
  — `SCM_RIGHTS` 的"open file description 引用 / 等价 dup"语义、`SCM_MAX_FD = 253`
  （2.6.38 前 255）、"至少 1 字节真实数据"、控制缓冲过小时"多余 fd 被自动关闭 + MSG_CTRUNC"、
  `RLIMIT_NOFILE` 溢出同样自动关闭、`ETOOMANYREFS` 与"Linux 4.5 起诊断"、`EBADF`、
  控制数据作为流的屏障（4/1/4 字节例子）、每种类型最多一项。
- [cmsg(3)](https://man7.org/linux/man-pages/man3/cmsg.3.html) 与
  [send(2)](https://man7.org/linux/man-pages/man2/send.2.html) — 前者的
  `CMSG_FIRSTHDR`/`CMSG_NXTHDR`/`CMSG_DATA`/`CMSG_SPACE`/`CMSG_LEN`/`CMSG_ALIGN` 确切语义、
  `cmsghdr` 字段、缓冲须先清零、`CMSG_DATA` 不要假定对齐、`optmem_max` 上限，以及传 fd 的
  官方示例；后者给出 `sendmsg(2)` 的错误集（`EINVAL`/`EBADF`/`EMSGSIZE`/`EOPNOTSUPP`）。
- [recvmsg(2) — Linux manual page](https://man7.org/linux/man-pages/man2/recvmsg.2.html)
  — `MSG_CMSG_CLOEXEC`（自 Linux 2.6.23，用于 `SCM_RIGHTS` 收到的 fd）、`MSG_CTRUNC`
  与 `MSG_TRUNC` 的区别、`msghdr` 各字段返回时的含义。
