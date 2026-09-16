# 03 系统编程

聚焦操作系统、底层网络、并发、内存管理等"贴近硬件"的知识。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-网络编程/](./01-网络编程/) | Socket、IO 多路复用、协议、拥塞控制、零拷贝、连接管理、Unix socket |
| [02-进程与线程/](./02-进程与线程/) | 进程/线程同步、信号、调度 |
| [02-协程/](./02-协程/) | 用户态协程（C++20 / Go / Lua） |
| [03-内存管理/](./03-内存管理/) | 虚拟内存、分配器、垃圾回收 |
| [04-文件系统/](./04-文件系统/) | VFS、Page Cache、日志文件系统 |

## 与游戏服务端的关系

IO 多路复用、协程等通用机制在游戏服务端也是基石。通用 demo 沉淀在本目录，游戏场景的封装放在 [01-游戏开发/01-服务端/](../01-游戏开发/01-服务端/)。

## 已完成 demo

- ✅ IO 多路复用 · `select` — 见 [01-游戏开发/01-服务端/网络编程/IO多路复用/select/](../01-游戏开发/01-服务端/网络编程/IO多路复用/select/)
- ✅ IO 多路复用 · `epoll` — 见 [01-游戏开发/01-服务端/网络编程/IO多路复用/epoll/](../01-游戏开发/01-服务端/网络编程/IO多路复用/epoll/)
- ✅ WebSocket 握手协议 — 见 [02-Web开发/04-API设计/WebSocket/握手协议/](../02-Web开发/04-API设计/WebSocket/握手协议/)
- ✅ Nagle 算法 vs `TCP_NODELAY` — 见 [03-系统编程/01-网络编程/Socket基础/Nagle算法/](01-网络编程/Socket基础/Nagle算法/)
- ✅ 哲学家就餐问题 — 见 [03-系统编程/02-进程与线程/哲学家就餐/](02-进程与线程/哲学家就餐/)（C / Python / Go：Naive + Resource Hierarchy + Tanenbaum 监视器三方案）
- ✅ Bump (arena) allocator — 见 [03-系统编程/03-内存管理/分配器/bump-allocator/](03-内存管理/分配器/bump-allocator/)（mmap 单块 + `(x+a-1)&~(a-1)` 二进制对齐 + O(1) 分配/重置）
- ✅ Slab allocator — 见 [03-系统编程/03-内存管理/分配器/slab-allocator/](03-内存管理/分配器/slab-allocator/)（Bonwick 1994 简化版：kmem_cache + 三链表 + slot bitmap）
- ✅ 三色标记 GC — 见 [03-系统编程/03-内存管理/垃圾回收/gc-tri-color/](03-系统编程/03-内存管理/垃圾回收/gc-tri-color/)（Dijkstra 1978：white/gray/black + 不变式 + worklist）
- ✅ mmap 内存映射 — 见 [03-系统编程/04-文件系统/mmap内存映射/](03-系统编程/04-文件系统/mmap内存映射/)（MAP_SHARED/PRIVATE/ANONYMOUS + msync + CoW + SIGBUS 边界;C / Python / Go）
- ✅ ext4 Journaling (JBD2) — 见 [03-系统编程/04-文件系统/ext4-Journaling/](03-系统编程/04-文件系统/ext4-Journaling/)（descriptor/data/commit 块结构 + recovery 重放 + 大端/JBD2 vs 小端/ext4 + ESCAPE flag;C / Python / Go）
- ✅ Page Cache 与 writeback — 见 [03-系统编程/04-文件系统/Page-Cache/](03-系统编程/04-文件系统/Page-Cache/)（`posix_fadvise` 6 advice + `sync_file_range` 精细 writeback + readahead 调优;Linux-only demo）
- ✅ 网络编程 · 传输层 5 个（2026-09-15 18:00 槽）— 见 [01-网络编程/](./01-网络编程/) 的「已完成 demo」表：
  - TCP 拥塞控制 Reno vs CUBIC（RFC 5681 / RFC 9438）— [传输层/TCP拥塞控制/](01-网络编程/传输层/TCP拥塞控制/)
  - 零拷贝 sendfile / splice — [零拷贝/sendfile-splice/](01-网络编程/零拷贝/sendfile-splice/)
  - backlog 与 SYN 队列 — [连接管理/backlog与SYN队列/](01-网络编程/连接管理/backlog与SYN队列/)
  - scatter-gather IO（readv / writev / TCP_CORK）— [IO模型/scatter-gather/](01-网络编程/IO模型/scatter-gather/)
  - SCM_RIGHTS 传文件描述符 — [进程间通信/SCM-RIGHTS-fd传递/](01-网络编程/进程间通信/SCM-RIGHTS-fd传递/)
- ✅ 进程线程协程 5 个（2026-09-17 06:00 槽）：
  - fork 与僵尸进程（COW/stdio 双份输出/WNOHANG/SIG_IGN→ECHILD）— [02-进程与线程/fork与僵尸进程/](02-进程与线程/fork与僵尸进程/)
  - 生产者消费者（mutex+condvar while 谓词 vs channel happens-before）— [02-进程与线程/生产者消费者/](02-进程与线程/生产者消费者/)
  - 读者写者锁（读者/写者偏好双策略、tryrdlock EBUSY、递归读锁）— [02-进程与线程/读者写者锁/](02-进程与线程/读者写者锁/)
  - 最小有栈协程（ucontext 四件套 + 有栈/无栈对照）— [02-协程/最小有栈协程/](02-协程/最小有栈协程/)
  - CFS 调度器（vruntime/最左选取/min_vruntime 放置/权重=份额）— [02-进程与线程/CFS调度器/](02-进程与线程/CFS调度器/)