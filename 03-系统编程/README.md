# 03 系统编程

聚焦操作系统、底层网络、并发、内存管理等"贴近硬件"的知识。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-网络编程/](./01-网络编程/) | Socket、IO 多路复用、协议 |
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