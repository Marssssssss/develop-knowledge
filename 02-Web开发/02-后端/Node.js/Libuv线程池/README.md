# Libuv 线程池（UV_THREADPOOL_SIZE）

## 简介

- Node.js 的"单线程"只指 **JS 主线程**；libuv 内部维护一个**全局线程池**，把基于同步系统 API 构建的异步 API 放到池线程里跑，跑完再把回调送回事件循环所在的主线程。
- 官方清单（Node.js CLI 文档 `UV_THREADPOOL_SIZE=size`）：使用线程池的 Node.js API 是 **所有 fs API**（文件监视器与显式同步版除外）、**异步 crypto API**（`crypto.pbkdf2()` / `crypto.scrypt()` / `crypto.randomBytes()` / `crypto.randomFill()` / `crypto.generateKeyPair()`）、**`dns.lookup()`**、**所有 zlib API**（显式同步版除外）。libuv 文档原文：该池"internally used to run all file system operations, as well as getaddrinfo and getnameinfo requests"。
- 默认 **4** 个线程；通过环境变量 `UV_THREADPOOL_SIZE` 调整，绝对上限 **1024**（libuv 1.30.0 起从 128 提到 1024）。线程池**全局且被所有事件循环共享**（多 Worker 线程各自有独立循环，但共享同一线程池实体——libuv 原文 "The threadpool is global and shared across all event loops"）。

## 原理详解

### 为什么要线程池

libuv 文档（Thread pool work scheduling）：libuv 提供 threadpool "which can be used to run user code and get notified in the loop thread"。Node.js CLI 文档进一步解释：异步系统 API 不可用时，libuv 用线程池"create asynchronous node APIs based on synchronous system APIs"——即在主线程之外用阻塞系统调用模拟出异步语义，完成后经 `uv_queue_work` 的 after 回调在主循环线程上通知。

### 三个关键事实

1. **池大小固定、在进程启动时确定**：CLI 文档原文 "the threadpool would have been created as part of the runtime initialisation much before user code is run"——所以 `process.env.UV_THREADPOOL_SIZE = '8'` 这种**进程内运行时设置不保证生效**，必须在启动 `node` 之前设进环境。本 demo 实验 C 用吞吐基线实测验证了这一点（实测见下）。
2. **跨 API 传染**：CLI 文档原文 "Because libuv's threadpool has a fixed size, it means that if for whatever reason any of these APIs takes a long time, other (seemingly unrelated) APIs that run in libuv's threadpool will experience degraded performance"——一个慢 `pbkdf2` 会拖慢"看似无关"的 `dns.lookup` 与 `fs.readFile`，因为它们共用同一个 4 线程的池。缓解手段是调大 `UV_THREADPOOL_SIZE`（> 4）。
3. **预分配与内存代价**：libuv 原文 "When a particular function makes use of the threadpool … libuv preallocates and initializes the maximum number of threads allowed by `UV_THREADPOOL_SIZE`. More threads usually means more throughput but a higher memory footprint. Thread stacks grow lazily on most platforms though."（1.45.0 起每线程 8 MB 栈，但多数平台栈是惰性增长的；1.50.0 起线程默认名 `libuv-worker`。）

### 本 demo 的三个实验

- **A 吞吐扩展**：8 个并发 `pbkdf2(1,000,000 迭代)` 在 pool=1/4/8 下计时（16 核机器实测 931/422/280ms——串行 → 4 并行 → 8 并行近似线性加速，受主线程调度与共享缓存影响有衰减）。
- **B 跨 API 传染**：先发 1 个慢 `pbkdf2(5,000,000)` 占池，20ms 后**并发**发 `dns.lookup('localhost')` + `fs.readFile`。pool=1 时两者延迟 ≈ 慢任务剩余时长（实测 553/550ms）；pool=2 时立刻恢复（7/4ms）。
- **C 运行时设置无效**：子进程内 `process.env.UV_THREADPOOL_SIZE='8'` 后再测 8 任务吞吐，实测结果贴近 **pool=4 基线**而非 pool=8 基线 → 未生效，与官方口径一致。

## 对比

| 维度 | 事件循环（主线程） | libuv 线程池 |
| --- | --- | --- |
| 并行度 | 1（JS 执行串行） | 4（默认）～ 1024（上限） |
| 承载的 API | JS 回调、定时器、网络 I/O（epoll/IOCP 事件驱动，**不占线程池**） | fs / 异步 crypto / dns.lookup / zlib |
| 阻塞后果 | 阻塞所有 JS 执行 | 占满池后拖累其它线程池 API（跨 API 传染） |
| 扩容手段 | 无（改用 Worker） | 启动前设 `UV_THREADPOOL_SIZE` |

> 注意：网络 I/O 走事件驱动（epoll/IOCP），**不占**线程池；这也是"Node 单线程也能扛高并发网络"的原因。DNS 要分家：`dns.lookup()`（getaddrinfo，占线程池）与 `dns.resolve()`（c-ares 异步解析）不同。

## 环境

- Node.js ≥ 10（本 demo 实测 v22.22.2 / Windows / 16 核）
- 无第三方依赖；TS 版用 Node 22 内置 `--experimental-strip-types` 直接运行

## 运行方式

```bash
cd js && node main.js                  # JS 版自检（约 8 秒，含 6 个子进程）
cd ts && node --experimental-strip-types main.ts   # TS 版自检
```

## 关键代码

- `js/pool_probe.js`：子进程探针。`throughput` 模式并发计时；`contaminate` 模式先占池再并发测 lookup/readFile 延迟；`runtimeset` 模式在进程内设置环境变量验证不生效。
- `js/main.js`：用 `spawnSync` 以不同 `UV_THREADPOOL_SIZE` 启动探针，输出三组实验数据并跑 9 项断言。

## 性能边界

- 实验数据受 CPU 核数（本机 16 核）与系统负载影响；断言阈值已留裕量（A1 > 1.5x / A2 > 1.2x / B > 200ms / 恢复 < 100ms），但在弱机或满载机器上 B3/B4 的 100ms 上限偶有超时风险。
- pool=8 在 < 8 核机器上加速比会低于本机实测；pool 大小超过核数后吞吐不再提升，只剩内存开销（每线程预留栈，惰性增长）。

## 注意事项与常见坑

- **`UV_THREADPOOL_SIZE` 必须在进程启动前设置**；运行时改 `process.env` 不保证生效（线程池已随运行时初始化创建）。PM2/Docker/k8s 场景要写进环境变量清单，不是写在代码里。
- **慢 crypto 会拖慢 fs**：`pbkdf2`/`scrypt` 迭代数大时，同时段的 `fs.readFile`、`zlib`、`dns.lookup` 全部排队——排查"文件读取突然变慢"时要看有没有大计算量的 crypto 在跑。
- **多 Worker 线程不增加线程池容量**：libuv 线程池是进程级共享实体，起 8 个 Worker 还是那 4 个池线程。
- **上限是 1024** 且只在启动前生效；1024 个 8MB 栈的理论内存上限不是免费午餐。

## 参考资料

- libuv 官方文档 — Thread pool work scheduling：<https://docs.libuv.org/en/v1.x/threadpool.html>（默认 4 / 上限 1024 / 全局共享 / 预分配 / fs + getaddrinfo + getnameinfo）
- Node.js 官方文档 — CLI: `UV_THREADPOOL_SIZE=size`：<https://nodejs.org/api/cli.html#uv_threadpool_sizesize>（使用线程池的 API 清单、跨 API 传染警告、运行时设置不保证生效）
