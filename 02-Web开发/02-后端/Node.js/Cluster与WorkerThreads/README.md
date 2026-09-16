# Cluster 与 Worker Threads（进程并行 vs 线程并行）

## 简介

- **cluster**：官方定位 "allows easy creation of child processes that all share server ports"——多个**子进程**共享同一监听端口，实现多核水平扩展；worker 经 `child_process.fork()` 派生，靠 IPC 与主进程通信。
- **worker_threads**：官方定位 "enables the use of threads that execute JavaScript in parallel"，且 "Unlike `child_process` or `cluster`, `worker_threads` **can share memory**. They do so by transferring `ArrayBuffer` instances or sharing `SharedArrayBuffer` instances."——CPU 密集型任务的线程级并行；对 I/O 密集型无用（"The Node.js built-in asynchronous I/O operations are more efficient than Workers can be"）。
- 选型官方口径：不需要进程隔离时用 `worker_threads`；需要独立进程/多核 HTTP 服务扩展用 `cluster`。

## 原理详解

### cluster 连接分发：两种方式与平台差异

官方文档两种分发方式：① **round-robin**（除 Windows 外所有平台默认）：主进程监听端口、accept 连接后轮询分发给 worker；② 主进程创建监听 socket 发给 worker 直接 accept。对方式② 官方明确警告："In practice however, distribution tends to be **very unbalanced** due to operating system scheduler vagaries. Loads have been observed where **over 70% of all connections ended up in just two processes, out of a total of eight**."

**本 demo 在 Windows(v22.22.2) 实测**：`cluster.schedulingPolicy === SCHED_NONE`（Windows 默认非 RR）；2 个 worker 共享端口时，**8 个串行请求 100% 落在同一个 worker**（`{"2":8}`）——方式②的不均衡不是理论警告，是默认行为。

### disconnect() vs kill()

- `worker.disconnect()`（优雅）：worker 内 "close all servers, wait for the `'close'` event on those servers, and then disconnect the IPC channel"；只管 server 连接，**客户端连接不会被自动关闭**；长连接会阻止退出，需配超时兜底。
- `worker.kill([signal])`（强杀）：默认 `SIGTERM`，"kills the worker process **without waiting for a graceful disconnect**"，别名 `worker.destroy()`。
- 区分标志 `exitedAfterDisconnect`："This property is `true` if the worker exited due to `.disconnect()`. If the worker exited any other way, it is `false`."——主进程据此决定是否 respawn（官方明确：主进程可不基于它重启）。本 demo 实测：disconnect 退出 → `true`；kill 退出 → `false` + `signal: 'SIGTERM'`。
- 其它要点：`cluster.workers` 在 worker **disconnect 且 exit 之后**才移除该条目；worker 全灭则 "existing connections will be dropped and new connections will be refused"；Node **不提供路由逻辑**，session 类内存态不能依赖单 worker。

### Worker Threads 消息语义（结构化克隆 + transfer）

`postMessage` 用 HTML 结构化克隆（非 JSON）。官方列举：环引用、`RegExp`/`BigInt`/`Map`/`Set`、TypedArray、`WebAssembly.Module` 可传；**原生对象仅白名单**（`MessagePort`/`net.Socket`/`FileHandle` 等）。克隆的代价（官方原文）："non-enumerable properties, property accessors, and **object prototypes are not preserved**. In particular, `<Buffer>` objects will be read as plain `<Uint8Array>`s on the receiving side, and instances of JavaScript classes will be cloned as plain JavaScript objects."

- **transferList**：转移 `ArrayBuffer` 的所有权，发送端视图作废（`byteLength` 变 0，本 demo C2 实测）；`SharedArrayBuffer` **不能**进 transferList——它是双端共享的（本 demo C3 用 `Atomics` 跨线程原子累加实测）。
- **事件序**：`'online'`（开始执行 JS）→ `'message'`（"All messages sent from the worker thread are emitted **before** the `'exit'` event"）→ `'exit'`（"This is the final event emitted by any `Worker` instance"）。`terminate()` 后 `exitCode === 1`（官方："If the worker was terminated, the `exitCode` parameter is `1`"）。

## 对比

| 维度 | cluster | worker_threads |
| --- | --- | --- |
| 并行单元 | 进程（fork + IPC） | 线程（MessagePort） |
| 共享内存 | 否（句柄传递除外） | 是（transfer ArrayBuffer / share SAB） |
| 典型用途 | 多核 HTTP 服务扩展 | CPU 密集计算（图像/压缩/加密） |
| 隔离性 | 进程级（独立 V8 堆，崩溃不互相影响） | 线程级（同进程，`resourceLimits` 限堆） |
| 启动开销 | 高（整进程） | 低（官方建议用线程池："use a pool of Workers"） |

## 环境

- Node.js ≥ 16（本 demo 实测 v22.22.2 / Windows）；无第三方依赖
- cluster 子进程占用本地端口 18742（跑前确认未被占用）
- TS 版用 Node 22 内置 `--experimental-strip-types` 直接运行

## 运行方式

```bash
cd js && node main.js                              # 约 4 秒,18 项断言
cd ts && node --experimental-strip-types main.ts   # TS 版同组断言
```

## 关键代码

- `js/cluster_app.js`：双角色脚本（`cluster.isPrimary` 分叉）——primary 完整跑 fork → 8 请求统计 → disconnect → kill → 重建恢复；worker 起共享端口 http server。
- `js/main.js`：A 段以子进程跑 cluster_app 并断言 JSON 结果；B 段同进程 `MessageChannel` 验证克隆语义；C 段 `new Worker(src, {eval: true})` 验证 transfer/SAB/terminate/事件序。

## 性能边界

- Windows SCHED_NONE 下连接分布不可依赖（实测 8/8 落单 worker）；需要均衡时可在 Linux 部署（默认 RR）或前置反代。
- Worker 线程数建议 ≈ 核数并复用（官方建议池化，"the overhead of creating Workers would likely exceed their benefit"）。

## 注意事项与常见坑

- **`terminate()` 的 Promise 在 `'exit'` 事件发出后才 resolve**：之后再 `once('exit')` 永远等不到（本 demo 开发期实跑暴露：挂起的 Promise 不阻止进程退出，事件循环排干后**静默退出 0**，无任何报错）——直接用 `await worker.terminate()` 的返回值当 exit code。
- **worker 停止后 `w.resourceLimits` 变空对象**（官方明文），须在运行中读取——本 demo C5 开发期实跑暴露。
- **Buffer 收到端是普通 Uint8Array**（`Buffer.isBuffer` 为 false）；类实例丢失原型（`instanceof` 为 false）——跨线程传"对象"前先想清楚接收端拿到什么。
- TS 的 `import * as cluster from 'node:cluster'` 在 ESM 下拿不到 EventEmitter 方法（`cluster.on is not a function`），要用默认导入；Node 22 strip-types 模式不支持构造器参数属性（`constructor(public c)` 报 `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX`）。
- `worker.kill()` 在 worker 内不等价于 `process.kill()`；`worker.disconnect()` 也不是 `process.disconnect()`（官方"易混淆点"清单）。

## 参考资料

- Node.js 官方文档 — Cluster module：<https://nodejs.org/api/cluster.html>（两种分发方式、70%/2-of-8 不均衡警告、disconnect/kill/exitedAfterDisconnect、listen 三种行为差异）
- Node.js 官方文档 — worker_threads：<https://nodejs.org/api/worker_threads.html>（与 cluster/child_process 的共享内存差异、结构化克隆规则与原型丢失、transferList、事件序、terminate exitCode=1、resourceLimits）
