# archive/completed.md — 已完成 demo 全本

> 由自动化任务每轮 append。**默认 agent 不读此文件**,当 `STATE.md` 第三节回答不了"做过吗"或"总数"时才 Grep 此处。
> rotate 规则见 `STATE.md §4`:> 100 行 或 > 50 KB → 截断至最近 100 行。

## 迁移说明(2026-09-11 拆分时)

| 来源 | 处理 |
| --- | --- |
| 旧 `_docs/SEARCH_PROGRESS.md` 第二节"已完成 demo 记录"(20 条) | 一次性写入本文档,保留全部原始 detail |
| ID 分配规则 | 001-999 主 demo 用,1000+ 留给"修订/补全"类副任务(未启用) |
| 完成日期 | 保留首次完成日期;补全/修订不增 ID |

## 已完成 demo 列表

| ID | 路径 | 知识点 | 语言 | 完成日期 |
| --- | --- | --- | --- | --- |
| 001 | `01-游戏开发/01-服务端/网络编程/IO多路复用/select/` | IO 多路复用 · `select` | C / Python / Go | 2026-09-11 |
| 002 | `09-语言学习/Python/装饰器/` | Python · 装饰器(基础 + 带参数) | Python | 2026-09-11 |
| 003 | `01-游戏开发/01-服务端/网络编程/IO多路复用/epoll/` | IO 多路复用 · `epoll`(LT/ET) | C / Python / Go | 2026-09-11 |
| 004 | `07-数据存储/04-搜索引擎/elasticsearch/` | Elasticsearch · 倒排索引与 NRT(REST API) | Python / Go | 2026-09-11 |
| 005 | `01-游戏开发/02-渲染/图形管线/深度缓冲/` | Z-Buffer 与 Z-Fighting(1/z 深度模型 + 量化精度) | C / Python / Go | 2026-09-11 |
| 006 | `01-游戏开发/03-UI/文本渲染/SDF/` | SDF 文本渲染(有符号距离场 + 双线性重建 + smoothstep AA) | C / Python / Go | 2026-09-11 |
| 007 | `01-游戏开发/04-游戏引擎/ECS/` | ECS 架构(sparse set 存储 + swap-remove + 最小集合查询) | C / Python / Go | 2026-09-11 |
| 008 | `01-游戏开发/05-物理/碰撞检测/GJK/` | GJK 碰撞检测算法(Minkowski 差 + support 函数 + simplex 演化) | C / Python / Go | 2026-09-11 |
| 009 | `01-游戏开发/06-AI/寻路算法/A-star/` | A* 启发式搜索(f=g+h + 可采纳性 + Dijkstra/A*/Greedy 三模式对比) | C / Python / Go | 2026-09-11 |
| 010 | `01-游戏开发/07-音频/音频压缩/IMA-ADPCM/` | IMA ADPCM(自适应差分 + 4-bit 量化 + 步长双查表 + 块随机访问) | C / Python / Go | 2026-09-11 |
| 011 | `01-游戏开发/08-动画/IK算法/FABRIK/` | FABRIK 启发式 IK(位置空间反向/正向两阶段 + 不可达目标退化) | C / Python / Go | 2026-09-11 |
| 012 | `02-Web开发/01-前端框架/Vue/reactive/` | Vue 3 Proxy 响应式最小实现(reactive/ref/effect/track/trigger + lazy 嵌套代理 + cleanup + effect 栈) | TypeScript / JavaScript | 2026-09-11 |
| 013 | `02-Web开发/02-后端/Node.js/Event-Loop/` | Node.js Event Loop(6 阶段 + nextTick/Promise 微任务 + libuv 1.45.0 行为变化 + I/O 中 setImmediate 必早于 setTimeout) | JavaScript / TypeScript | 2026-09-11 |
| 014 | `02-Web开发/03-数据库/B+树索引/` | B+ 树索引(M-way + 叶子兄弟链 + copy-up/push-up 分裂 + borrow/merge 重平衡 + bulk-load O(N)) | C / Python / Go | 2026-09-11 |
| 015 | `02-Web开发/04-API设计/WebSocket/握手协议/` | WebSocket 握手协议 RFC 6455 §4(HTTP Upgrade + SHA-1+GUID → Sec-WebSocket-Accept + 101 Switching Protocols + 子协议协商) | C / Python / Go | 2026-09-11 |
| 016 | `03-系统编程/01-网络编程/Socket基础/Nagle算法/` | Nagle 算法 vs `TCP_NODELAY`(RFC 896"inhibit sending when unacknowledged data exists"+Linux tcp(7) man page `tcpi_segs_out` 段计数实测;100 次 1 字节 send 在 Nagle on → ~8 段 vs `TCP_NODELAY=1` → 100 段) | C / Python / Go | 2026-09-11 |
| 017 | `03-系统编程/02-进程与线程/哲学家就餐/` | 哲学家就餐问题(Dijkstra 1965;Naive 死锁 + Resource Hierarchy 资源分级破循环等待 + Tanenbaum 监视器 1 mutex + N condvar + state[];pthread_cond_wait 原子释放 + 谓词循环 + 三种语言 mutex/cond 标准库对照) | C / Python / Go | 2026-09-11 |
| 018 | `03-系统编程/03-内存管理/分配器/bump-allocator/` | Bump (arena) 分配器(mmap 单块 + 单调 offset + 二进制 `(x+a-1)&~(a-1)` 向上对齐 + O(1) 分配/重置;alignment 必须为 2 的幂;多块版需 next 链表,匿名 struct 不能自引用) | C / Python / Go | 2026-09-11 |
| 019 | `03-系统编程/03-内存管理/分配器/slab-allocator/` | Slab 分配器(Bonwick 1994 简化版:kmem_cache + 三链表 partial/full/free + slab 内 bitmap + ctz 找第一空位;SLAB_OBJ_MAX=126/slab;O(1) partial 头分配;生产加 slab coloring + per-CPU array) | C / Python / Go | 2026-09-11 |
| 020 | `03-系统编程/03-内存管理/垃圾回收/gc-tri-color/` | 三色标记 GC(Dijkstra 1978 "On-the-Fly GC":white/gray/black 三色 + tri-color 不变式"无黑→白"边 + worklist gray 栈;stop-the-world 版 demo 5 个图:可达链/循环/断连子图/钻石共享/孤立子树;O(E+V) mark + O(heap) sweep;能回收 refcount 不能的循环) | C / Python / Go | 2026-09-11 |
| 021 | `03-系统编程/04-文件系统/mmap内存映射/` | mmap(2) 文件/匿名内存映射(MAP_SHARED 共享 page cache + msync 持久化;MAP_PRIVATE 写时复制 CoW 不影响源文件;MAP_ANONYMOUS|MAP_SHARED 父子进程 IPC;ftruncate 后访问越界 → SIGBUS 边界;offset 必须页对齐、length 页向上取整;fd 可立即 close 不影响映射) | C / Python / Go | 2026-09-11 |
| 022 | `03-系统编程/04-文件系统/ext4-Journaling/` | ext4 Journaling / JBD2(预写日志:journal_header_t 12 字节 magic=0xC03B3998 + blocktype + sequence;5 类块 descriptor/commit/jsb v1+2/revoke;descriptor 含 blocknr 标签描述 data block 最终落盘位置;recovery 重放已 commit 事务丢弃 incomplete;JBD2 大端 vs ext4 小端;JBD2_FLAG_ESCAPE 处理 data 前 4B 与 magic 碰撞) | C / Python / Go | 2026-09-11 |
| 023 | `03-系统编程/04-文件系统/Page-Cache/` | Linux page cache + writeback(`posix_fadvise` 6 advice: NORMAL/RANDOM/SEQUENTIAL/WILLNEED/DONTNEED/NOREUSE;`sync_file_range` 精细 writeback 3 flag 组合;dirty page 触发条件:`vm.dirty_background_ratio` 10% / `dirty_ratio` 20% / `dirty_expire_centisecs` 30s / 显式 fsync / umount;/proc/meminfo Cached/Dirty 状态观察;Linux-only demo,Windows 直接 exit 提示) | C / Python / Go | 2026-09-11 |
| 024 | `04-移动开发/01-iOS/内存管理/ARC/` | Swift / Objective-C ARC 内存管理(strong retain 默认 / weak 可选自动置 nil / unowned 非可选不自动置 nil / Person-Apartment + Customer-CreditCard + HTMLElement 闭包 3 例;引用计数仅适用类实例,struct/enum 不归 ARC 管;Objective-C 对应 `__weak` / `__unsafe_unretained` / Block `__weak typeof(self)` 捕获列表;CFGetRetainCount 验证 retain/release 等价行为) | Swift / Objective-C | 2026-09-11 |
| 025 | `04-移动开发/01-iOS/并发编程/GCD同步原语/` | GCD DispatchQueue / DispatchSemaphore / DispatchGroup / DispatchWorkItemFlags.barrier(主队列 vs 全局并发 vs 自定义;QoS 5 等级;.barrier 在并发队列独占但全局并发队列无效;dispatch_semaphore 限并发上限 3 时 10×200ms 任务实测 ≈ 0.67s;DispatchGroup enter/leave 配对 + notify;main.sync 死锁警告) | Swift / Objective-C | 2026-09-11 |
| 026 | `04-移动开发/01-iOS/事件循环/RunLoop/` | RunLoop modes / sources / timers / observers(每线程一实例 lazy 创建;CFRunLoopObserver 监听 kCFRunLoopActivity 6 个位 entry/beforeTimers/beforeSources/beforeWaiting/afterWaiting/exit;5 个标准 mode default/connection/modal/eventTracking/commonModes;Timer scheduledTimer 必须 invalidate 否则 RunLoop↔Timer↔Closure↔Self 循环;ScrollView 滚动切到 eventTracking mode 使 default mode 的 timer 暂停 → 注册到 commonModes 解决) | Swift / Objective-C | 2026-09-11 |
| 027 | `04-移动开发/02-Android/并发编程/Handler消息机制/` | Android Handler / Looper / MessageQueue 异步消息机制(Handler(Looper) post/sendMessage/postDelayed + HandlerThread 自带 Looper 的工作线程 + nativePollOnce epoll 阻塞唤醒 + Message.sPool 对象池复用 + onNewIntent 与 setIntent 配合 + 5 个 demo:post Runnable 跨线程 / sendMessage 四元组 / HandlerThread 串行 / postDelayed 定时 / 内存泄漏静态内部类 + WeakReference 修复) | Kotlin / Java | 2026-09-11 |
| 028 | `04-移动开发/02-Android/组件生命周期/Activity启动模式/` | Android Activity launchMode 5 种(standard 默认创建新实例 + singleTop 栈顶复用 onNewIntent + singleTask 同 affinity 唯一并清空上方 + singleInstance 全设备独占任务 + singleInstancePerTask API 31+ 文档型)+ Intent flags 运行时优先级(NEW_TASK / SINGLE_TOP / CLEAR_TOP)+ onNewIntent 必须 setIntent(intent) 更新 getIntent() + taskAffinity 自定义任务分组) | Kotlin / Java | 2026-09-11 |
| 029 | `04-移动开发/02-Android/UI框架/Compose重组/` | Jetpack Compose 重组机制(mutableStateOf 自动重组 + by 委托 setValue + remember(key1, key2) 缓存昂贵计算 + derivedStateOf 仅在结果变化时触发下游重组避免滚动每帧重组 + Modifier.offset{} / drawBehind{} lambda 版本跳过 Composition 阶段直接 Layout/Draw + Backwards write 反模式无限重组循环 + Snapshot 系统 MVCC 多版本快照追踪 + Slot Table 增量更新) | Kotlin | 2026-09-11 |

---

> 写入规则:每 demo 完成后 append 一行(用现有 ID 续号)。监控:agent 巡查时若行数 > 100 → 触发 rotate(见 STATE.md §4)。
