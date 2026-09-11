# Node.js Event Loop（6 阶段 + 微任务）

## 简介

- Node.js Event Loop 是 Node 运行时（基于 libuv）的核心调度模型：单线程 JS 主线程循环执行 6 个**阶段**（phase），每个阶段负责处理一类异步回调（timer、I/O、immediate、close 等），并把微任务（microtask）在阶段边界处插队清空。
- 它解决的问题：在 **JS 是单线程**的前提下，既不阻塞主线程，又能把网络/磁盘 I/O 这类耗时操作的结果**异步**回喂给业务代码；并提供 setTimeout / setImmediate / process.nextTick / Promise.then 这几套"语义不同"的调度 API。
- 适用场景：所有 Node.js 服务端代码；尤其是排查 I/O 顺序、回调时序、`setTimeout(fn, 0)` 与 `setImmediate` 谁先执行等问题时，必须先理解 Event Loop。
- 关键概念（先记这 6 个）：
  - **phase**：6 个固定阶段，每阶段有独立 FIFO 队列，依次循环（详见原理详解）。
  - **nextTick queue**：Node 独有的微任务队列，优先级**高于** Promise 微任务。
  - **microtask queue**：标准 JS 微任务队列（Promise.then / queueMicrotask）。
  - **libuv**：用 C 实现 Event Loop 的库，6 阶段划分来源于 libuv 的设计。
  - **poll phase**：唯一可能"阻塞等待新事件"的阶段，复杂度最高。
  - **min-heap of timers**：timers 用最小堆按到期时间排序。
- 历史背景：libuv 起源于 Node.js 0.9 之前 Joyent / libev / libio 时代（libuv 文档追溯到 2013 年初的 1.0.0 release），目的是把 macOS 上 kqueue、Linux 上 epoll、Windows 上 IOCP 统一成一套 API；Node.js 官方文档明文写"the most important parts are here"指的就是下面这张 6 阶段图。

## 原理详解

### 工作机制分步

Node.js 进程启动后按下列顺序运转（依据 [Node.js 官方文档](https://nodejs.org/en/learn/asynchronous-work/event-loop-timers-and-nexttick)）：

1. 初始化 Event Loop；处理输入脚本（含 `process.nextTick` 与 timer 注册），进入循环。
2. 执行 **timers** 阶段：跑已到期的 `setTimeout` / `setInterval` 回调（按最小堆顺序）。
3. 执行 **pending callbacks**：跑被延到下一轮的系统级 I/O 回调（如 Unix 上 ECONNREFUSED）。
4. 执行 **idle, prepare**：仅 Node 内部使用。
5. 执行 **poll**：计算阻塞时长；执行 poll 队列里的 I/O 回调（详见下文 poll 详解）。
6. 执行 **check**：跑 `setImmediate()` 回调。
7. 执行 **close callbacks**：跑 `socket.on('close')` 之类的关闭回调。
8. 回到 timers，进入下一轮。
9. **每个阶段回调之间**还会先 drain `nextTickQueue`，再 drain `microtask queue`，然后才进入下一阶段（见 process.nextTick 节）。

### 6 阶段 ASCII 流程图

```
   ┌───────────────────────────┐
   │           timers          │
   └─────────────┬─────────────┘
                 │ (queue drain: nextTick -> Promise microtasks)
                 v
   ┌───────────────────────────┐
┌─>│     pending callbacks     │
│  └─────────────┬─────────────┘
│  ┌─────────────┴─────────────┐
│  │       idle, prepare       │
│  └─────────────┬─────────────┘
│  ┌─────────────┴─────────────┐      ┌───────────────┐
│  │           poll            │<─────┤  incoming:   │
│  └─────────────┬─────────────┘      │  connections, │
│  ┌─────────────┴─────────────┐      │   data, etc.  │
│  │           check           │      └───────────────┘
│  └─────────────┬─────────────┘
│  ┌─────────────┴─────────────┐
│  │      close callbacks      │
│  └─────────────┬─────────────┘
│  ┌─────────────┴─────────────┐
└──┤           timers          │
   └───────────────────────────┘
```

> 图来自 Node.js 官方文档，保留原 ASCII 风格以便与官方交叉对照。

### poll 阶段详解（官方原文）

Node.js 官方文档对 poll 的描述极重要，**逐字保留**：

> When the event loop enters the **poll** phase *and there are no timers scheduled*, one of two things will happen:
> - If the **poll** queue *is not empty*, the event loop will iterate through its queue of callbacks executing them synchronously until either the queue has been exhausted, or the system-dependent hard limit is reached.
> - If the **poll** queue *is empty*, one of two more things will happen:
>   - If scripts have been scheduled by `setImmediate()`, the event loop will end the **poll** phase and continue to the **check** phase to execute those scheduled scripts.
>   - If scripts **have not** been scheduled by `setImmediate()`, the event loop will wait for callbacks to be added to the queue, then execute them immediately.

要点提炼：

- poll 既要算"还要等多久就该执行 timers"，也要"执行 poll 队列里堆积的 I/O 回调"。
- 队列空且有 setImmediate → 直接跳 check。
- 队列空且没有 setImmediate → **阻塞等待**新事件（直到最近 timer 到期 / 新连接 / 文件可读）。
- libuv 对 poll 的阻塞有**系统相关硬上限**，避免 starve 整个 loop。

### 核心 API 与参数

| API | 阶段 / 队列 | 关键参数 | 返回值 |
| --- | --- | --- | --- |
| `setTimeout(cb, ms)` | timers | `ms` 是**阈值**，不是精确时间 | `Timeout` 对象 |
| `setInterval(cb, ms)` | timers（重复） | 同上 | `Timeout` 对象 |
| `setImmediate(cb)` | check | 无 | `Immediate` 对象 |
| `process.nextTick(cb)` | nextTick 队列（**非**阶段） | cb 排在当前 operation 完成后立即执行 | `void` |
| `queueMicrotask(cb)` | microtask 队列 | 标准 Web API | `void` |
| `Promise.then(cb)` | microtask 队列 | 每次 `.then` 注册一个微任务 | `Promise` |

权威注脚：

- 官方对 timers 的精确性：**"may be executed rather than the exact time"**，受 OS 调度与其他回调影响。
- 官方对 nextTick：**"not technically part of the event loop"** —— 它在每个 C/C++ → JS 边界后立刻执行，不属于 6 阶段。
- 官方对 setImmediate vs setTimeout 在 I/O 中：**"the immediate callback is always executed first"**。

### 底层发生了什么（内核视角）

- 6 阶段是 libuv 的 C 实现。Node.js 文档明确："libuv, the C library that implements the Node.js event loop and all of the asynchronous behaviors of the platform"。
- poll 阶段监听新事件的底层：
  - Linux → `epoll` / `eventfd`
  - macOS / BSD → `kqueue`
  - Windows → IOCP
- 这些多路复用机制让单线程主循环能"阻塞等一组 fd 中任一可读/可写"，避免 busy poll。
- libuv **1.45.0 起（Node.js 20+）**有个细节变化：timers 默认在 **poll 之后**才执行；但为保持向后兼容，**首次进入主循环前**仍会先跑一次 timers。
  原文："Starting with libuv 1.45.0 (Node.js 20), timers are run after the poll phase in each event loop iteration. In earlier versions, timers were run before polling."
- nextTick 不属于 libuv，是 Node 在 C/C++ → JS 边界用 `_tickCallback` 排队的；在所有官方介绍里都不出现在 6 阶段图里。

## 对比 / 选型

| 维度 | Node.js (libuv) | 浏览器 (HTML Standard) |
| --- | --- | --- |
| 阶段/队列模型 | 6 阶段 + 多种 FIFO 队列 | 多个 **task queue**（集合）+ 1 个 **microtask queue** |
| 宏任务来源 | timers / poll (I/O) / check (immediate) / close | script / 事件回调 / fetch / setTimeout 等 |
| 微任务队列数 | 2 个：`nextTickQueue` + Promise/queueMicrotask | 1 个（按 ECMAScript job → microtask 合并） |
| 微任务执行时机 | **每个阶段回调之间**，且先 nextTick 再 Promise | 每个 task 跑完后做 microtask checkpoint |
| 是否阻塞 | poll 阶段可以阻塞等待 I/O | 永不阻塞（task 队列空就 idle 等渲染） |
| 渲染时机 | 不适用 | microtask 清空后、下一个 task 前可选择渲染 |
| 跨线程 | 主线程 + libuv 内部 thread pool (默认 4) | 主线程 + Web Workers / Service Workers |

为什么 nextTick 比 Promise 早：

- HTML / ECMAScript 标准只规定了 1 个 microtask 队列（实现可以是浏览器的 microtask queue）。
- Node 在此之上又叠了一个 nextTick 队列，且明确 **"resolve all nextTick before continuing the event loop"**，所以从观察上看 nextTick 永远早于 Promise.then。

为什么 I/O 回调里 setImmediate 必早于 setTimeout(0)：

- poll 阶段 → 下一阶段就是 check（immediate）→ 再下一轮才回 timers。
- 主模块里二者顺序不确定：Node.js 官方文档原话："the order in which the two timers are executed is non-deterministic"。

## 环境准备

- 操作系统：Windows / macOS / Linux 均可（libuv 在 Windows/Unix 上有微小差异，本 demo 不依赖差异行为）
- Node.js 版本：**≥ 18** 推荐；20+ 能完整演示 libuv 1.45.0 的 timers-after-poll 行为
- 依赖：无第三方依赖；只使用 `node:fs` / `node:path` 内置模块
- TypeScript（可选）：仅 `ts` 目录需要 `tsc`；本机版本 ≥ 5.0 推荐

## 运行方式

### JS 版（直接跑）

```bash
cd "D:\开发研究\02-Web开发\02-后端\Node.js\Event-Loop"
node js/event_loop_demo.js
```

预期看到 5 个 demo 标题，每个标题下若干带标签日志，标签对应阶段名（timers/check/poll/nextTick/promise/sync/microtask/sleep）。

### TS 版（编译后跑）

```bash
cd "D:\开发研究\02-Web开发\02-后端\Node.js\Event-Loop\ts"
tsc                         # 用本目录 tsconfig.json 编译到 ./dist
node dist/event_loop_demo.js
```

或一行：

```bash
npx -y tsx event_loop_demo.ts    # 需要 Node 18+ + npx
```

## 关键代码片段

### Demo 1：观察 sync / nextTick / Promise / timers / check 的相对顺序

```js
console.log(tag('sync'), '1. main script');

process.nextTick(() => {
  console.log(tag('nextTick'), '2. nextTick (nextTick queue)');
});

Promise.resolve().then(() => {
  console.log(tag('promise'), '3. promise.then (microtask queue)');
});

setTimeout(() => {
  console.log(tag('timers'), '4. setTimeout 0 (timers phase)');
}, 0);

setImmediate(() => {
  console.log(tag('check'), '5. setImmediate (check phase)');
});

console.log(tag('sync'), '6. main script end');
```

为什么这样排：

- 主模块同步代码（`1` 和 `6`）先跑完。
- 然后 drain nextTick queue（`2`）→ drain Promise 微任务（`3`）。
- 才进入第一轮 timers（`4`），再 poll，再 check（`5`）。
- 这与 Node 官方"after each phase's callbacks the nextTickQueue and microtask queue are drained"的语义一致。

### Demo 3：I/O 回调里 setImmediate 必早于 setTimeout(0)

```js
fs.readFile(__filename, () => {
  // poll 阶段里跑的回调
  setTimeout(() => console.log('timeout'), 0);   // 下一轮 timers
  setImmediate(() => console.log('immediate'));  // 紧跟本轮的 check
});
```

- 官方原话："if you move the two calls within an I/O cycle, the immediate callback is always executed first"。
- 注意主模块里顺序不确定；这个稳定顺序**只在 I/O 回调里成立**。

### Demo 5：libuv 1.45.0 行为

```js
setImmediate(() => {
  // 在 check 里再注册一个 setTimeout 0：在 Node 20+ 上,
  // 由于 timers 在 poll 之后跑, 这个 timer 会等下一轮才到 timers,
  // 所以下一次 check 里那个 immediate 会先跑 (见输出顺序)。
  setTimeout(() => console.log('timers inside check'), 0);
  setImmediate(() => console.log('next immediate inside check'));
});
```

- 在 Node 18 上 timers 可能先于"next immediate"输出；在 Node 20 上"next immediate"先输出。
- 推荐用 `node --version` 看一眼当前运行时版本。

## 性能与边界

- **time complexity**：单次 loop iteration 的代价 = `O(timers 堆顶到期判定) + O(poll 队列长度) + O(微任务队列长度)`。
- **starvation**：递归 `process.nextTick` 会让 loop 永远不进入 poll — 官方文档原话："it allows you to 'starve' your I/O"。
- **timers 阈值**：`setTimeout` 的 `ms` 是 Node 实现上的最小 1ms 阈值（虽然 `setTimeout(fn, 0)` 被规范化为 1ms）。在浏览器里 HTML Standard 强制 4ms 最小延迟（嵌套 5 层以上）；Node 不受此约束。
- **handle 上限**：libuv 默认进程最多 64k 文件描述符（受 `ulimit -n` 影响）；Event Loop 本身无队列上限，但每个队列里的回调执行到 `libuv` 配置的"硬上限"会切下一阶段。
- **thread pool**：libuv 默认 4 个工作线程用于 fs / dns / 用户 `uv_queue_work`，通过 `UV_THREADPOOL_SIZE` 环境变量调整。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `setTimeout(()=>{}, 0)` 输出顺序忽闪忽闪 | 主模块里它与 `setImmediate` 执行顺序不确定 | 放进 I/O 回调里就能稳定 immediate 先 |
| 业务回调里大量 `nextTick` 后 I/O 卡死 | nextTick 不属于 6 阶段，递归会阻塞 poll | 把"高优先级"用 `setImmediate` 替代；nextTick 只用于"在当前 operation 后立即执行"的语义 |
| `await` 之后预期立即执行的代码被"卡" | 每个 `await` 是 1 个 microtask，排在 Promise.then 队列 | 区分 microtask（Promise/await）和 nextTick 语义；不要假设"马上同步" |
| Node 18 升级到 Node 20 后某些 timer 行为变了 | libuv 1.45.0 把 timers 默认移到 poll 之后 | 升级时跑回归；关键逻辑不要依赖 timers/immediate 的相对顺序 |
| 浏览器里 promise 比 setTimeout 0 早；Node 里大多数情况也如此，但 nextTick 又比 promise 更早 | Node 多个微任务队列的层级 | 跨环境代码避免依赖 nextTick；要么用 Promise 要么 setImmediate |

跨平台差异：

- Windows 上 IOCP 实现与 Unix 的 epoll/kqueue 性能曲线不同，但 **6 阶段对外行为一致**。官方原话："There is a slight discrepancy between the Windows and the Unix/Linux implementation, but that's not important for this demonstration"。
- 在 WSL1 (旧版 WSL) 下某些 I/O 通知路径走 9P，可能影响 poll 阶段的回调到达时机；WSL2 是真 Linux 内核，无此问题。

生产环境建议：

- 用 `clinic.js` / `node --trace-event-categories=node.async` 看每阶段停留时长。
- 用 `perf_hooks.monitorEventLoopDelay()` 监控 loop 延迟；超过几十毫秒就要查 CPU 密集任务。
- 不要把 CPU 密集计算放进主线程；交给 `worker_threads` 或独立进程。

## 参考资料（实际阅读过的权威来源）

- [The Node.js Event Loop — nodejs.org Learn](https://nodejs.org/en/learn/asynchronous-work/event-loop-timers-and-nexttick)
  完整阅读：6 阶段 ASCII 图、pending/poll/check/close/timers 详解、`setImmediate` vs `setTimeout` 官方示例、`process.nextTick` 不是阶段成员的原文、libuv 1.45.0 (Node 20) timers-after-poll 行为变化。
- [JavaScript execution model — MDN](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Event_loop)
  完整阅读：Job queue 与 event loop 的语义、HTML Standard 拆 task/microtask 两类的原文措辞、Run-to-completion 与"a script is taking too long to run"警告、spec 交叉引用。

> 另：HTML Standard 的 Event Loop 章节 `https://html.spec.whatwg.org/multipage/webappapis.html#event-loop-processing-model`（章节 8.1.7）确认包含 Definitions / Queuing tasks / Processing model / Generic task sources / Dealing with the event loop from other specifications 等子节；本轮为节省 WebFetch 配额未抓取该长文档完整正文，仅与上述两源交叉印证章节编号与名称。
