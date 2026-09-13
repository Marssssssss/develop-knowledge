# kqueue — BSD/macOS IO 多路复用

## 简介

**kqueue** 是 BSD 派系(Darwin/macOS、FreeBSD、NetBSD、OpenBSD、DragonFly)内核提供的通用事件通知接口,
由 Jonathan Lemon 于 2000 年代初设计,后来被 Apple 移植到 XNU(macOS/iOS 内核)。
一次 `kqueue()` 系统调用即可监听**任意数量**的文件描述符上的读/写/异常事件,而没有 `select` 的 `FD_SETSIZE=1024` 上限,也没有 `poll` 的线性扫描开销。

- **关键概念清单**
  - `struct kevent` — 用户 ↔ 内核传递的事件结构(7 字段:ident / filter / flags / fflags / data / udata / ext[4])
  - `EV_SET(&ev, ident, filter, flags, fflags, data, udata)` — 初始化宏
  - `EVFILT_READ` / `EVFILT_WRITE` — 数据可读 / 可写 filter
  - `EV_ADD` / `EV_ENABLE` / `EV_DISABLE` / `EV_DELETE` — 加入 / 启用 / 禁用 / 删除
  - `EV_ONESHOT` — 只触发一次后自动删除
  - `EV_CLEAR` — 状态型 filter 重置(MAC 端口等场景)
  - `EV_EOF` / `EV_ERROR` — peer 关闭 / 出错通知
  - `EVFILT_VNODE` / `EVFILT_PROC` / `EVFILT_TIMER` / `EVFILT_AIO` — 文件 / 进程 / 定时器 / AIO 监听
- **历史背景**
  - BSD select(1983) → poll → kqueue(2000 前后 Jonathan Lemon FreeBSD) → epoll(Linux 2.6, 2003)
  - 解决了 `select` `FD_SETSIZE=1024` 与 `poll` `O(n)` 扫描的 C10K 痛点

## 原理详解

### `struct kevent` 字段

```
ident   uintptr_t  /* 事件源:通常就是文件描述符 */
filter  int16_t    /* EVFILT_* 决定事件类型 */
flags   uint16_t   /* EV_*  本次调用的动作:ADD/ENABLE/DISABLE/DELETE */
fflags  uint32_t    /* filter 专属 flag,如 NOTE_LOWAT */
data    int64_t     /* filter 返回数据(对 socket:可读字节数) */
udata   void *      /* 用户透传数据,内核不改 */
ext[4]  uint64_t    /* filter 扩展数据(macOS 5.12+ splice 用) */
```

### 工作流程

1. **创建队列**:`kq = kqueue()` 返回一个 fd;**不**被子进程继承,不能跨 UNIX socket 传递
2. **注册事件**:`kevent(kq, changelist, nchanges, NULL, 0, NULL)` —— changelist 一次性提交多个注册
3. **等待事件**:`kevent(kq, NULL, 0, eventlist, nevents, timeout)` 阻塞到事件就绪或超时
4. **分发**:遍历就绪 eventlist,按 `filter` / `flags` 分发到对应 handler
5. **修改 / 删除**:用 flag 组合 `EV_ADD | EV_DISABLE` 修改已有事件;`EV_DELETE` 移除

> 注:`changelist` 与 `eventlist` 可共用同一数组;`timeout = {0,0}` 是非阻塞 poll,`NULL` 永久阻塞。

### EVFILT_READ / EVFILT_WRITE 在 socket 上的语义

| fd 类型 | EVFILT_READ 触发条件 | `data` 字段含义 |
| --- | --- | --- |
| 监听 socket | 有新连接排队 | listen backlog 当前长度 |
| 已连接 socket | 接收缓冲区 ≥ `SO_RCVLOWAT`(默认 1B) | 当前可读字节数 |
| socket 单向关闭 | EV_EOF 标志置位 | 错误码(无则为 0) |
| pipe/fifo | writer 关闭后仍可读取已缓存数据 | 可读字节数 |
| 普通 vnode 文件 | 文件指针**不在末尾** | EOF 偏移(可负) |

`EV_EOF` 与"还有数据未读完"可以**同时**返回:必须 `recv()` 读到 `EAGAIN` 再判定 peer 真正关闭。

## 对比 / 选型

| 维度 | kqueue | epoll | select | poll |
| --- | --- | --- | --- | --- |
| 上线 | FreeBSD 4.1 / 2001 | Linux 2.6 / 2003 | BSD 4.2 / 1983 | System V |
| fd 上限 | 内存 | 内存 | `FD_SETSIZE=1024`(Linux 默认) | `RLIMIT_NOFILE` |
| 时间复杂度 | O(1) 就绪事件 | O(1) 就绪事件 | O(n) 每次扫描 | O(n) 每次扫描 |
| 触发模式 | level-triggered(默认) | LT / ET | LT | LT |
| 注册 fd | 动态 | 动态 | 必须最大 fd | 动态 |
| 用户透传数据 | `udata` | `epoll_event.data` | 无 | 无 |
| 跨平台性 | BSD 系 | Linux | 几乎全平台 | 几乎全 POSIX |
| 适用语言 | C/C++/libevent/libuv | C/C++/libevent/libuv | 跨平台 | 跨平台 |

游戏服**只在 macOS/iOS 上开发原型 + BSD 服务器上线**时,kqueue 是首选;
跨 Linux + macOS 的产品通常选 **libevent / libuv**(抽象层),或直接用 Go 的 `runtime` (`net` 包已封装)。

## 环境准备

- **C**:macOS 或 FreeBSD(`gcc -Wall -Wextra` 干净)
- **Python**:CPython 3.8+,可透明跑在 Linux/macOS
- **Go**:**必须 `GOOS=darwin` 或 FreeBSD**(Linux 编译期会缺 `syscall.EVFILT_*` 常量)

## 运行方式

### C(macOS)

```bash
gcc -O2 -Wall -Wextra c/kqueue_echo.c -o kqueue_echo
./kqueue_echo 9000
# 另起终端:
nc 127.0.0.1 9000
```

### Python(跨平台)

```bash
python3 python/kqueue_echo.py 9000
# backend 会显示 KqueueSelector / EpollSelector 等
```

### Go(macOS/FreeBSD)

```bash
cd go && GOOS=darwin go build -o kqueue_echo kqueue_echo.go
./kqueue_echo 9000
```

## 关键代码片段

C 版核心回路(完整 file `c/kqueue_echo.c` 已含):

```c
/* 创建队列 + 注册监听 socket */
kq = kqueue();
EV_SET(&ev, listen_fd, EVFILT_READ, EV_ADD | EV_ENABLE, 0, 16, NULL);
kevent(kq, &ev, 1, NULL, 0, NULL);

/* 主循环:阻塞在 kevent() */
struct kevent events[MAX_EVENTS];
struct timespec timeout = {1, 0};   /* 1s tick */
for (;;) {
    int nev = kevent(kq, NULL, 0, events, MAX_EVENTS, &timeout);
    for (int i = 0; i < nev; i++) {
        if (events[i].flags & EV_ERROR) close(events[i].ident);
        if (events[i].ident == listen_fd && events[i].filter == EVFILT_READ)
            accept_loop();          /* accept until EAGAIN */
        else
            read_echo_loop(events[i].ident);
    }
}
```

Python selectors 透明跨平台封装关键:

```python
sel = selectors.DefaultSelector()       # 自动选 KqueueSelector / EpollSelector
sel.register(listen_sock, selectors.EVENT_READ, data="listen")
while True:
    for key, mask in sel.select(timeout=1.0):
        if key.data == "listen":
            accept_handler(key, mask)  # drain accept() until EAGAIN
        else:
            read_handler(key, mask)    # drain recv() until EAGAIN
```

## 性能与边界

- **fd 数量**:无硬上限;受内核单进程 `RLIMIT_NOFILE`、`vm.kqueue.max` 影响(BSD 通常 0x20000 量级)
- **单次 `kevent()` 处理事件数** = `nevents` 参数(本 demo = 32);生产环境建议 64-256
- **chrono overhead**:BSD 内核报告 kqueue syscall ≈ `epoll_wait` 同量级,~1-5 µs
- **`udata` 自定义字段**避免每次 O(n) 遍历事件表;O(1) 通过 `ident` 定位
- **关闭 fd 自动注销**:`close(fd)` 触发 EV_DELETE 等价;无需手动 cancel

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `accept()` 返回 `EAGAIN` 没客户端 | 已 accept 完 | 必须 loop `until EAGAIN` |
| 同一 fd 注册多次 `EV_ADD` | 报重复,仅修改 | 用 `EV_ADD` 幂等语义,不建表手动去重 |
| 边缘触发式事件丢失 | BSD 默认 LT | 想 ET 行为需自己 `EV_CLEAR` + `EAGAIN` 判定 |
| goroutine `internal/poll` 误以为没 kqueue | Go `net` 抽象层封装好,无需手动 syscall | 仍要手动 syscall 时必须在 darwin/bsd |
| `kqueue` fd 占用文件描述符上限 1 位 | 大 fd 计数会绕过 `RLIMIT_NOFILE` | 用 `dup` 把 kqueue fd 复用 |
| `fork()` 后子进程无 kqueue | kqueue 不被继承 | 子进程再次 `kqueue()` |

## 参考资料

- [OpenBSD kqueue(2) man page](https://man.openbsd.org/OpenBSD-6.0/kqueue.2) — 最规范的 freebsd/openbsd 通用说明
- [macOS kqueue(2) man page](https://man.freebsd.org/cgi/man.cgi?query=kqueue&sektion=2&apropos=0&manpath=macOS+13.6.5) — Apple 移植版,扩展 `ext[4]` 字段
- [FreeBSD kqueue(2) (4.6-stable) historic](https://people.freebsd.org/~jmg/kqueue.historic.man.html) — Jonathan Lemon 原始 man page
- FreeBSD kqueue(2) (7-current + 12-stable 多版本页面) — 关于 `EV_RECEIPT`、`EV_KEEPUDATA`、`EV_DISPATCH` 的语义
