# Reactor 模式 — 单线程事件驱动服务器

## 简介

**Reactor 模式**(Reactor Pattern)是 Douglas C. Schmidt 1995 年博士论文提出的事件驱动并发模式,
ACE 框架核心,也奠定了 Netty、Node.js、Nginx、Twisted、Redis(早期版)、libuv 的架构基础。
一个**单线程**的事件循环阻塞在系统级"同步事件多路复用器"(epoll/kqueue/IOCP/select)上,
**就绪**事件触发对应 handler 回调,**绝不阻塞**。

- **关键概念清单**
  - **Handle**(资源):sockets、fifos、timers 等可被 OS 监视的句柄
  - **Synchronous Event Demultiplexer**:`select` / `poll` / `epoll` / `kqueue` / IOCP
  - **Initiation Dispatcher**(Reactor 主):维护 handler 注册表,跑事件循环
  - **Event Handler** 接口:`handle_read` / `handle_write` / `handle_accept`
  - **Concrete Handler**:业务实现(如玩家会话、Redis client)
  - **Reactor Pattern 是单线程的**(by definition);多线程需要 Leader/Followers 或多个 Reactor 实例
- **历史背景**
  - 1983 BSD select → 1995 Schmidt 博士论文发表 Reactor → 2003 epoll → 2009 Node.js(libuv reactor)
  - Schmidt 当时在加州大学欧文分校做博士,目标是 C++ 网络应用框架

## 原理详解

### 静态结构(Schmidt 论文 OMT 图)

```
            ┌──────────────────────────┐
            │     Initiation           │
            │     Dispatcher           │ ← Reactor 主循环
            │     (Reactor)            │
            └────────┬─────────────────┘
                     │ owns
        ┌────────────┼────────────┐
        │ dispatches │            │
   ┌────▼─────┐  ┌───▼──────┐  ┌──▼──────┐
   │ Handle 1 │  │ Handle 2 │  │ Handle N │  ← fd/资源
   └────┬─────┘  └───┬──────┘  └──┬──────┘
        │ bound to   │            │
   ┌────▼────────┐ ┌─▼─────────┐ ┌▼─────────┐
   │ Handler_1   │ │ Handler_2 │ │ Handler_N│
   └─────────────┘ └───────────┘ └──────────┘
```

### 工作流程(3 阶段循环)

1. **Phase 1 — Initialization**:`Reactor.register(fd, handler)` 把 (fd, handler) 写入注册表
2. **Phase 2 — Demultiplexing**:`demultiplexer.select()` **阻塞**等 fd 就绪;返回就绪 fd 列表
3. **Phase 3 — Dispatch**:对每个就绪 fd 查 handler 表,调用 `handler.handle_event()`;handler 必须**非阻塞 IO**

```
while running:
    events = demultiplexer.select(timeout)
    for ev in events:
        handler = registry[ev.fd]
        handler.handle_event(ev)        # 不许阻塞!
```

### 复杂度

| 操作 | 复杂度 | 备注 |
| --- | --- | --- |
| 注册 fd | O(1) 平摊 | 内部 hash/数组 |
| 注销 fd | O(1) ~ O(log n) | epoll 用 RB-tree O(log n);hash 表 O(1) |
| 单事件分发 | O(1) | 注册表直接查表 |
| 单轮事件循环 | O(1) per ready + demultiplexer 开销 | epoll_wait ≈ O(1) on ready FDs |

### 关键不变性

> **永远不要在 handler 里阻塞。** 否则一个慢客户端挂起整个服务器。

阻塞操作强制**委派**到线程池(如 ExectorService),然后通过 `handler.response()` 回调回到 Reactor 线程。

### 三种 Reactor 线程模型变体

```
单 Reactor 单线程      主 Reactor(accept)        
─────────────────       ─────────────────────       
       All-in           ┌────────────┐        
       One              │  Main R×N  │ ← accept + 分发 fd
                        └─────┬──────┘        
                       ┌──────┼──────┐       
                       ▼      ▼      ▼      
                    ┌─────┐ ┌─────┐ ┌─────┐
                    │Sub1 │ │Sub2 │ │Sub3 │  ← 子 Reactor:read/write
                    └─────┘ └─────┘ └─────┘
                        Worker Thread Pool
                        ───────────────────────
                              业务线程池
```

Netty 默认是**主从多 Reactor**,Nginx worker 是**单 Reactor 多线程**(`ngx_worker_process` 各自一套)。

## 对比 / 选型

| 模型 | 线程数 | 适用 | 代表 |
| --- | --- | --- | --- |
| **Reactor**(SD) | 1 | 简单业务,无阻塞依赖 | Redis 早期、Twisted |
| **Reactor + Worker Pool** | 1+N | 业务有阻塞 IO/CPU 计算 | Netty、BFE |
| **主从 Multi-Reactor** | M+N | 高并发长连接 | Netty 默认、Nginx |
| **Proactor**(AD) | 1+N | OS 支持完成通知 | Windows IOCP、Boost.Asio |
| **Thread-per-connection** | N | 业务极短(已淘汰) | Apache(早期) |

## 环境准备

- **C**:`gcc -Wall -Wextra`,Linux(`epoll`)
- **Python**:`selectors` 自带,3.8+
- **Go**:go 1.18+

## 运行方式

### C(Linux)

```bash
gcc -O2 -Wall -Wextra c/reactor.c -o reactor
./reactor 9000
# Other terminal: nc 127.0.0.1 9000
```

### Python(跨平台)

```bash
python3 python/reactor.py 9000
```

### Go

```bash
cd go && go build -o reactor reactor.go && ./reactor 9000
```

## 关键代码片段

### C Reactor main loop

```c
/* --- Reactor main loop (Phase 2 + 3) --- */
struct epoll_event events[MAX_EVENTS];
for (;;) {
    int nev = epoll_wait(reactor.epfd, events, MAX_EVENTS, 1000);
    for (int i = 0; i < nev; i++) {
        struct session *s = (struct session *)events[i].data.ptr;
        if (s->listen) {
            if (events[i].events & EPOLLIN) on_accept(&reactor, s);
        } else {
            if (events[i].events & EPOLLIN) on_read(&reactor, s);
        }
    }
}
```

### Python Handler interface

```python
class Handler:
    def handle_accept(self, listener): ...
    def handle_read(self, sess): ...
    def handle_close(self, sess): ...

class EchoHandler(Handler):
    def handle_read(self, sess):
        data = sess.sock.recv(4096)
        if not data:
            self.handle_close(sess); return
        sess.sendall(b"echo:" + data)
```

### Go mapping

Go 的 `net.Listener.Accept()` 把"Phase 2 demultiplexer"封装进了 runtime net poller;
每条连接 `go r.serve(conn)` 是天然的 "Phase 3 dispatch"。理解了这个映射,
就用 Go 复刻了 Reactor 的精神(尽管没有显式显式循环 epoll_wait)。

## 性能与边界

- **单线程 Reactor** 可处理 ~10K-100K 长连接(取决于 handler CPU 占用)
- **NGINX 单 worker 8 核可扛 50K+ HTTP 长连**
- **epoll_wait 返回就绪 fd** 不需遍历注册表 → O(就绪数)而非 O(总 fd 数)
- **瓶颈**通常在 handler 阻塞;用 `--callgrind` / `perf` 抓长尾

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 服务卡顿 | handler 阻塞在磁盘/网络 IO | 把 IO 移到 Worker 池,完成后通过 callback 回到 Reactor |
| thundering herd | `accept()` 唤醒后多个 worker 抢同一 fd | 用 `SO_REUSEPORT`(Linux 3.9+)分流 |
| fd 表过大 | 数组/字典查 O(1) 仍 O(1),但事件循环单次 epoll_wait 体内分配不少 | 用 `epoll_pwait2` + thread pool 或 SO_REUSEPORT 切到多 Reactor |
| 连接泄漏 | handler 异常没路由到 unregister | 用 RAII/defer;或 wrapper free in close path |
| 单核打满 | 单 Reactor 跑满一核 | Main/Sub 模型 |

## 参考资料

- [Reactor Pattern - cstopics.com](https://cstopics.com/encyclopedia/programming/design-patterns/concurrency-patterns/reactor-pattern) — Douglas C. Schmidt 1995 模式总结
- [Reactor: An Object Behavioral Pattern for Demultiplexing and Dispatching Handles for Synchronous Events](https://www.cs.wustl.edu/~schmidt/PDF/reactor-siemens.pdf) — Schmidt 原始论文 (Siemens 报告版 PDF)
- [Reactor Pattern for Scaling I/O Bound Server - Hilash](https://hilash.github.io/2019/12/28/reactor.html) — 高质量博客 + libuv / Node / Nginx 实战映射
- [The C10K Problem - Dan Kegel](http://www.kegel.com/c10k.html) — 多路复用器选型经典
- [Reactor pattern - en-academic](https://en-academic.com/dic.nsf/enwiki/6867023) — Apache MINA / Twisted / EventMachine / POE 等历史实现列表
