# epoll — Linux 可扩展 I/O 事件通知机制

> 本文内容依据 man7.org 的 [epoll(7)](https://man7.org/linux/man-pages/man7/epoll.7.html) 与 [epoll_ctl(2)](https://man7.org/linux/man-pages/man2/epoll_ctl.2.html) 手册页、[Python selectors 官方文档](https://docs.python.org/zh-cn/3/library/selectors.html)及 [Go runtime 源码 netpoll_epoll.go](https://github.com/golang/go/blob/go1.24.5/src/runtime/netpoll_epoll.go) 归纳总结，参考资料见文末。

## 简介

- epoll 是 Linux（内核 2.6+）提供的**可扩展 I/O 事件通知机制**，用于监视多个文件描述符，看是否可以在其中任何一个上进行 I/O——任务与 `poll(2)` 类似，但**在监视大量 fd 时扩展性显著更好**（手册原文："scales well to large numbers of watched file descriptors"）。
- 关键概念：
  - **epoll 实例（epoll instance）**：内核中的数据结构，可视为一个包含两个列表的"容器"
  - **interest list（兴趣列表）**：进程声明要监视的 fd 集合
  - **ready list（就绪列表）**：内核因 I/O 活动而**动态填充**的已就绪 fd 集合
  - **水平触发（LT）**：只要缓冲区还有数据就一直通知——默认模式，语义等同"更快的 poll"
  - **边缘触发（ET）**：仅在状态变化（新数据到达）时通知一次
- 历史背景：Linux 2.5.44 引入（2002 年），为解决 select/poll 在万级连接下的性能瓶颈而生；Nginx、Redis、Node.js、Go runtime 的 Linux 后端均基于它。

## 原理详解

### 工作机制（三步）

```text
用户态                        内核态
──────                        ──────
epoll_create1(EPOLL_CLOEXEC)
  └────────────────────────→ 创建 epoll 实例
                               ┌─────────────────────┐
epoll_ctl(epfd, ADD, fd, ev)   │   epoll 实例        │
  └────────────────────────→   │  interest list      │ ← 增删改 O(log n)
                               │   [fd1][fd2][fd3]…  │
                               │         │ 就绪回调   │
                               │  ready list  ←──────┼── 网卡数据到达时
                               │   [fd2]             │    内核主动挂入
epoll_wait(epfd, events, N, t) │                     │
  └────────────────────────→   └─────────────────────┘
  ←───────────────────────── 只拷贝就绪的 fd（通常 O(1)）
```

1. `epoll_create1(flags)` 创建 epoll 实例，返回引用它的 fd；
2. `epoll_ctl(epfd, op, fd, event)` 把目标 fd 加入/修改/移出 interest list——**注册只做一次**，此后无需像 select/poll 那样每次调用都重传整个集合；
3. `epoll_wait(epfd, events, maxevents, timeout)` 等待事件：调用可理解为"从 ready list 取项"（手册原文），返回就绪 fd 数，失败返回 -1 并设 errno。

### 核心 API

```c
#include <sys/epoll.h>

int epoll_create1(int flags);            // flags: EPOLL_CLOEXEC（exec 时自动关闭）
                                         // 旧接口 epoll_create(size) 的 size 已无意义
int epoll_ctl(int epfd, int op, int fd, struct epoll_event *event);
int epoll_wait(int epfd, struct epoll_event *events,
               int maxevents, int timeout);  // timeout: -1 无限阻塞 / 0 立即返回 / >0 毫秒
```

`epoll_ctl` 的 `op` 参数：

| op | 作用 |
| --- | --- |
| `EPOLL_CTL_ADD` | 把 fd（及其 open file description 引用 + event 设置）加入 interest list |
| `EPOLL_CTL_MOD` | 修改已注册 fd 的设置（如 ET 模式下用 ONESHOT 后"重新武装"） |
| `EPOLL_CTL_DEL` | 把 fd 移出 interest list（event 参数被忽略，Linux 2.6.9+ 可传 NULL） |

`struct epoll_event` 的 `events` 位掩码（返回的事件类型）：

| 标志 | 含义 | 备注 |
| --- | --- | --- |
| `EPOLLIN` | 可读 | |
| `EPOLLOUT` | 可写 | |
| `EPOLLRDHUP` | 对端关闭连接或关闭写半 | 2.6.17+；ET 模式下检测对端关闭很有用 |
| `EPOLLPRI` | 异常条件（带外数据） | |
| `EPOLLERR` | fd 上发生错误 | **总是会上报，无需注册** |
| `EPOLLHUP` | fd 被挂断 | **总是会上报，无需注册** |

输入标志（影响行为、不会被返回）：

| 标志 | 含义 |
| --- | --- |
| `EPOLLET` | 启用边缘触发（默认水平触发） |
| `EPOLLONESHOT` | 事件上报一次后该 fd 被禁用，须 `EPOLL_CTL_MOD` 重新武装 |
| `EPOLLWAKEUP` | 事件处理期间阻止系统休眠（需 CAP_BLOCK_SUSPEND） |
| `EPOLLEXCLUSIVE` | 4.5+，多个 epoll 监视同一 fd 时只唤醒部分，缓解惊群 |

`data` 字段是内核**原样保存、就绪时原样返回**的 64 位数据，惯用法是放 fd 本身或指向连接上下文的指针。

主要错误码：`EEXIST`（ADD 时已注册）、`ENOENT`（MOD/DEL 时未注册）、`EINVAL`（epfd 不是 epoll 实例 / fd==epfd）、`EBADF`、`ENOMEM`、`ENOSPC`（超过 `max_user_watches` 限制）、`EPERM`（目标 fd 不支持 epoll，如普通文件）、`ELOOP`（epoll 嵌套监视成环，深度上限 5）。

### LT vs ET：一个管道例子（来自手册原文）

注册 rfd 后写入 2 kB 数据 → `epoll_wait` 报告就绪 → reader 只读了 1 kB → 再次调用 `epoll_wait`：

- **ET（EPOLLET）**：第二次调用**可能挂起**——尽管缓冲区还剩 1 kB。因为事件在数据到达时产生一次、在上次 `epoll_wait` 中被消费，部分读取不会再产生新事件。ET 的语义是"每个数据块到达时通知一次"。
- **LT（默认）**：会再次报告就绪，直到读完。手册明确："LT 模式下 epoll 就是一个更快的 poll(2)，可在任何使用 poll 的地方使用，因为它们语义相同。"

**ET 模式的官方使用范式**（手册原文强调）：

> (1) 使用非阻塞 fd；(2) 只有在 `read(2)`/`write(2)` 返回 `EAGAIN` 之后才再次等待事件。即收到事件后应循环读写直到 `EAGAIN`。

### 各语言如何用 epoll

| 语言 | 暴露方式 | 权威依据 |
| --- | --- | --- |
| C | 直接系统调用 `epoll_create1/epoll_ctl/epoll_wait` | man7.org epoll(7)/epoll_ctl(2) |
| Python | `selectors.DefaultSelector` 自动选平台最优实现（Linux→epoll）；`select.epoll` 为低层封装 | Python 官方文档：selectors "建立在 select 模块原型之上，推荐用户改用此模块" |
| Go | 不直接暴露；runtime **netpoller** 在 Linux 上用 epoll 封装进调度器，用户写阻塞式 API 即可 | Go 源码 `netpollopen`：`ev.Events = EPOLLIN \| EPOLLOUT \| EPOLLRDHUP \| EPOLLET`——**Go 用的是 ET 模式** |

## 对比 / 选型

| 维度 | select | poll | epoll |
| --- | --- | --- | --- |
| 平台 | POSIX 全平台 | POSIX | 仅 Linux |
| fd 数量上限 | 单进程默认 1024（FD_SETSIZE） | 无硬上限 | `/proc/sys/fs/epoll/max_user_watches`（默认约可用内存/4% / 每 fd 90~160 字节，可达数十万） |
| 每次调用开销 | O(n)：每次重传全部 fd + 内核遍历 | O(n)：仍需遍历全部 fd | 就绪返回 O(就绪数)；注册 O(log n) 只做一次 |
| fd 集合传递 | 每次调用全量拷入拷出 | 每次调用全量拷入拷出 | 内核持久持有 interest list，事件经 ready list 增量上报 |
| 触发模式 | 仅水平触发 | 仅水平触发 | LT（默认）/ ET（EPOLLET） |
| 适用场景 | 跨平台原型、fd 数很少 | fd 中等规模的 POSIX 可移植代码 | Linux 高并发生产环境（Nginx/Redis/Go runtime 均基于它） |

macOS/BSD 对应物是 kqueue；Windows 是 IOCP（异步模型，范式不同）。

## 环境准备

- 操作系统：Linux（内核 ≥ 2.6；EPOLLRDHUP 需 ≥ 2.6.17，EPOLLEXCLUSIVE 需 ≥ 4.5）
- C：gcc / clang
- Python：≥ 3.4（selectors 模块引入版本）
- Go：任意现代版本（demo 仅用标准库）
- Go/Python 版可在非 Linux 平台运行（Go/Python 会自动落到各自平台的高效实现），C 版仅 Linux

## 运行方式

### C（LT 模式）

```bash
gcc -O2 -Wall -Wextra -o epoll_echo epoll_echo.c
./epoll_echo 9000
```

### Python

```bash
python3 epoll_echo.py 9000
```

### Go

```bash
go run epoll_echo.go 9000
```

测试：另开终端 `nc 127.0.0.1 9000`，输入任意文本应原样回显。

## 关键代码片段

C 版事件循环骨架（对应"原理详解"三步工作流）：

```c
int epfd = epoll_create1(EPOLL_CLOEXEC);          // ① 创建实例
ev.events = EPOLLIN; ev.data.fd = listen_fd;
epoll_ctl(epfd, EPOLL_CTL_ADD, listen_fd, &ev);   // ② 注册监听 fd

for (;;) {
    int n = epoll_wait(epfd, events, MAX_EVENTS, -1);  // ③ 取就绪列表
    for (int i = 0; i < n; i++) {
        if (events[i].data.fd == listen_fd) {
            /* accept 新连接 → setblocking(False) → EPOLL_CTL_ADD */
        } else {
            /* read → 回写；读到 0 或 EPOLLERR/EPOLLHUP → EPOLL_CTL_DEL + close */
        }
    }
}
```

Python 版的回调注册模型（data 字段与 C 的 `epoll_event.data` 设计思想一致）：

```python
sel = selectors.DefaultSelector()            # Linux 上自动是 epoll
sel.register(sock, selectors.EVENT_READ, accept)   # data=回调
for key, mask in sel.select():               # 等价于 epoll_wait
    callback = key.data                      # 取回注册时绑定的数据
    callback(key.fileobj, mask)
```

Go 版说明：`io.Copy(conn, conn)` 里的 Read 阻塞时 goroutine 让出线程（gopark），fd 事件由 netpoller 的 epoll ET 上报后唤醒——用户不需要手写事件循环。

## 性能与边界

- **每 fd 内存成本**：手册给出的权威数字——interest list 中每个已注册 fd 在 32 位内核上约占 **90 字节**、64 位内核上约 **160 字节**；上限默认按可用内存的一定比例推算（`/proc/sys/fs/epoll/max_user_watches`），超大内存机器上可达百万级。
- **就绪返回复杂度**：`epoll_wait` 只返回就绪的 fd，开销与监视总数无关（对比 select/poll 的 O(n) 全量遍历）。
- **LT 是默认且安全**：语义与 poll 相同；ET 是高性能选项但要求非阻塞 + 读到 EAGAIN 的纪律。
- 边界：epoll 不支持普通文件（`EPERM`）；fd 复制（dup/fork）后事件仍会上报给所有指向同一 open file description 的 fd。

## 注意事项与常见坑

1. **ET 模式读一半就等事件 → 挂起**。现象：流水线时通时断。原因：ET 只在"新数据到达"时通知，缓冲区残留数据不再触发。规避：非阻塞 fd + 循环读写到 `EAGAIN`。
2. **ET 模式单 fd 排干导致饥饿（Starvation）**。手册专门警告：忙于排干一个大流量 fd 会让其他 fd 得不到处理。规避：维护应用层就绪队列，在就绪 fd 之间轮转（round robin）。
3. **事件缓存陷阱（Event Cache）**。一次 `epoll_wait` 返回 100 个事件，处理到第 47 个时关闭了第 13 个事件对应的 fd，后续再用缓存判断 fd 13 就会错乱。规避：关闭时立即 `EPOLL_CTL_DEL` 并在缓存中标记。
4. **close ≠ 立即移出 interest list**。只有当**所有**指向同一 open file description 的 fd 副本（dup/fork 继承）都关闭后才真正移除，期间事件仍可能上报。
5. **EPOLLERR/EPOLLHUP 不需要注册**，内核总是上报；EPOLLIN + EPOLLRDHUP 组合可在 ET 模式下可靠检测对端关闭。
6. **多线程惊群**：多个线程阻塞在同一个 epoll fd 的 `epoll_wait` 上时，ET 事件只会唤醒其中一个线程（内核优化，天然缓解 thundering herd）；跨进程多 epoll 实例监视同一 fd 的场景用 `EPOLLEXCLUSIVE`（4.5+）。
7. **信号中断**：`epoll_wait` 可能被信号打断返回 -1/EINTR，应重试（Python selectors 在 PEP 475 后自动重试）。

## 参考资料（实际阅读过的权威来源）

- [epoll(7) - Linux manual page](https://man7.org/linux/man-pages/man7/epoll.7.html) — epoll 语义总览：interest/ready list、LT/ET 管道示例、ET 使用范式、Starvation、事件缓存、惊群优化（man-pages 6.19）
- [epoll_ctl(2) - Linux manual page](https://man7.org/linux/man-pages/man2/epoll_ctl.2.html) — epoll_ctl/epoll_wait/epoll_create1 完整签名、全部事件标志与输入标志、错误码、EPOLLEXCLUSIVE 细节
- [selectors — 高层级 I/O 复用（Python 3 官方文档）](https://docs.python.org/zh-cn/3/library/selectors.html) — DefaultSelector 平台自动选择、register/SelectorKey/select 语义、回显服务器官方示例
- [netpoll_epoll.go（Go 官方仓库，go1.24.5）](https://github.com/golang/go/blob/go1.24.5/src/runtime/netpoll_epoll.go) — Go runtime 在 Linux 上的 netpoller 实现：EpollCreate1 初始化、netpollopen 注册 `EPOLLIN|EPOLLOUT|EPOLLRDHUP|EPOLLET`（ET 模式）
