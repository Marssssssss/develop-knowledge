# 03 系统编程

聚焦操作系统、底层网络、并发、内存管理等"贴近硬件"的知识。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-网络编程/](./01-网络编程/) | Socket、IO 多路复用、协议、拥塞控制、零拷贝、连接管理、Unix socket |
| [02-进程与线程/](./02-进程与线程/) | 进程/线程同步、信号、调度 |
| [02-协程/](./02-协程/) | 用户态协程（C++20 / Go / Lua） |
| [03-内存管理/](./03-内存管理/) | 虚拟内存、分配器、垃圾回收 |
| [04-文件系统/](./04-文件系统/) | VFS 与 statx、Page Cache 与回写、稀疏文件与打洞、日志文件系统（ext4/JBD2）、CoW 文件系统（Btrfs）、O_DIRECT |

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
- ✅ 内存管理 5 个（2026-09-18 10:00 槽）— 见 [03-内存管理/](./03-内存管理/)：
  - mmap / brk 虚拟内存基础（三次"返回值口径"差异、`MAP_FIXED` 只丢弃重叠部分、`mallopt` 动态 mmap 阈值）— [03-内存管理/分配器/mmap-brk-虚拟内存/](03-内存管理/分配器/mmap-brk-虚拟内存/)
  - ptmalloc2 真实实现（chunk 头 / bins / tcache 精确匹配 / arena 上限 8×CPU；master 与 2.35 的 tcache 常量差异）— [03-内存管理/分配器/ptmalloc2/](03-内存管理/分配器/ptmalloc2/)
  - Cheney 半空间复制式 GC（无递归栈的双指针遍历、转发指针、BFS 层序、成本 ∝ 存活数）— [03-内存管理/垃圾回收/gc-cheney-copying/](03-内存管理/垃圾回收/gc-cheney-copying/)
  - 标记-压缩式 GC（Lisp2 保序滑动 + GHC `Compact.c` 线程化压缩的 O(1) 额外空间；保序 vs 层序的局部性 2 倍差）— [03-内存管理/垃圾回收/gc-mark-compact/](03-内存管理/垃圾回收/gc-mark-compact/)
  - 分代式 GC（写屏障/记忆集是正确性前提；实测分代扫 4008 字 vs 非分代基线 21708 字；CPython 三代 + 25% long_lived 启发式 + 默认阈值 3.13 起 700→2000）— [03-内存管理/垃圾回收/gc-generational/](03-内存管理/垃圾回收/gc-generational/)
- ✅ 文件系统 5 个（2026-09-19 10:00 槽）— 见 [04-文件系统/](./04-文件系统/)：
  - VFS 与 `statx(2)`（请求掩码 ≠ 返回掩码、`STATX__RESERVED`→EINVAL、属性位双重屏蔽）— [04-文件系统/VFS与statx/](03-系统编程/04-文件系统/VFS与statx/)
  - CoW 与快照（reflink 引用计数、extent 切三段、defrag 打断 reflink、CRC32C 初值 0）— [04-文件系统/CoW与快照/](03-系统编程/04-文件系统/CoW与快照/)
  - `O_DIRECT` 与对齐（三代对齐口径、静默退回 buffered、`O_DIRECT ≠ O_SYNC`）— [04-文件系统/O_DIRECT与对齐/](03-系统编程/04-文件系统/O_DIRECT与对齐/)
  - 稀疏文件与打洞（`SEEK_HOLE`/`SEEK_DATA`、fallocate 五模式、`FIEMAP`）— [04-文件系统/稀疏文件与打洞/](03-系统编程/04-文件系统/稀疏文件与打洞/)
  - 回写与脏页（`vm.dirty_*` 的 available 分母、counterpart 互斥、两页下限）— [04-文件系统/回写与脏页/](03-系统编程/04-文件系统/回写与脏页/)
- ✅ 网络编程第四批 5 个（2026-09-20 08:00 槽）— 见 [01-网络编程/](./01-网络编程/)：
  - 非阻塞 IO 与 `EAGAIN` 语义（`read(2)` 按 fd 类型分两条 ERRORS、`MSG_DONTWAIT` 按调用覆盖、短读/部分写、非阻塞 connect 三态）— [01-网络编程/IO模型/非阻塞Socket/](01-网络编程/IO模型/非阻塞Socket/)
  - TCP 流量控制与零窗口（RFC 9293 §3.8.6 收发双端 SWS、零窗口探测 RTO 起指数退避、RFC 7323 窗口扩大与回缩）— [01-网络编程/传输层/TCP流量控制与零窗口/](01-网络编程/传输层/TCP流量控制与零窗口/)
  - HTTP/2 帧与 HPACK（9 字节帧头、HPACK 整数「严格小于 2^N-1」边界、静态表 61 项、动态表逐出）— [01-网络编程/协议解析/HTTP2帧与HPACK/](01-网络编程/协议解析/HTTP2帧与HPACK/)
  - `io_uring` 网络 IO（`res` = `-errno` 单通道、`user_data` 必需、`IOSQE_IO_LINK`、SQPOLL 零系统调用、IOPOLL 不适用于网络）— [01-网络编程/IO多路复用/io_uring网络IO/](01-网络编程/IO多路复用/io_uring网络IO/)
  - Unix socket 凭证传递（`SO_PASSCRED` 逐消息 vs `SO_PEERCRED` connect 时刻快照、autobind 抽象地址上限 2^20）— [01-网络编程/进程间通信/凭证传递/](01-网络编程/进程间通信/凭证传递/)