# async_hooks 与 AsyncLocalStorage（异步上下文追踪）

## 简介

- **async_hooks**（Stability: 1 - Experimental）：Node.js 观测**异步资源生命周期**的底层钩子——`init` / `before` / `after` / `destroy`（+ `promiseResolve`）五类回调覆盖资源从创建到销毁的全过程，`executionAsyncId()` 回答"现在在谁的执行上下文里"，`triggerAsyncId` 回答"这个资源**为什么**被创建"。
- **AsyncLocalStorage**（Stability: 2 - Stable，v13.10.0+）：构建在异步追踪之上的**请求级上下文**——官方定位 "These classes are used to associate state and propagate it throughout callbacks and promise chains. … It is similar to thread-local storage in other languages."，是替代手写 async_hooks 的首选："While you can create your own implementation on top of the `node:async_hooks` module, `AsyncLocalStorage` should be preferred as it is a performant and memory safe implementation."

## 原理详解

### 四个 Hook 回调的语义（官方原文要点）

- `init`："Called when a class is constructed that has the *possibility* to emit an asynchronous event"——构造未完成时就可能触发，资源字段可能未填充。
- `before`：资源回调执行前。"It can be called **0-N times for handles** (such as TCPWrap), and will be called **exactly 1 time for requests** (such as FSReqCallback)."——本 demo E1 实测：一个 `Timeout` 恰好 `before`/`after` 各 1 次。
- `after`：回调完成后；若回调抛未捕获异常，`after` 在 `'uncaughtException'` 之后跑。
- `destroy`：资源销毁后；**依赖 GC 的资源可能永不触发**（在 `init` 里持有 `resource` 引用会导致 `destroy` 不来 + 内存泄漏），且启用 destroy hook 有额外开销（GC 追踪 Promise）。

### executionAsyncId vs triggerAsyncId

官方原文："`executionAsyncId()` … only shows *when* a resource was created, while `triggerAsyncId` shows *why* a resource was created."——执行时机 vs 因果来源。本 demo E2 实测因果链：`Promise.resolve()` 创建 PROMISE id=10（trigger=6 顶层），`then()` 创建 id=11（trigger=10 父 Promise），再链 id=12（trigger=11）。

### Promise 追踪的开关行为

官方原文："By default, promise executions are not assigned `asyncId`s due to the relatively expensive nature of the promise introspection API provided by V8."——**安装任意 hook（哪怕空 init）即启用** Promise 追踪；且 "before and after callbacks are run **only on chained promises**"。本 demo E2 实测：装 hook 后 then 回调拿到独立 eid（6→11）。

### AsyncLocalStorage 的关键语义（E3 全项实测）

| API | 语义（官方原文要点） | 实测 |
| --- | --- | --- |
| `run(store, cb)` | 同步执行 cb，store 对 cb 内创建的异步操作可见；cb 抛错 → `run()` 重抛且**上下文退出** | ✓ |
| `getStore()` | 在 run/enterWith 之外调用返回 `undefined` | ✓ |
| `exit(cb)` | 脱离上下文跑 cb；cb 抛错 → 重抛且**上下文重新进入**（与 run 相反） | ✓ |
| `enterWith(store)` | 实验性；持续整个同步执行并穿透后续异步与**事件处理器**（"That is why `run()` should be preferred"） | ✓ 泄漏实测 |
| `disable()` | 此后 `getStore()` 恒 undefined；**必须先 disable 实例才能被 GC**（内存泄漏陷阱；store 对象不受此限制） | ✓ |

### 迷你 ALS 原理复刻（E5）

用 `createHook({ init })` + `Map<asyncId, store>` 即可复刻核心机制：`init` 时把**当前执行上下文**的 store 继承给新资源 id；`getStore()` 读当前 `executionAsyncId()` 对应的 store。实测跨 setTimeout 链传播且并行上下文互不串扰。官方实现远比这复杂（嵌入存储 + 性能优化），此复刻仅用于理解原理，且**故意不做 destroy 清理**（Map 只增不减，生产代码禁止这样做）。

## 对比

| 维度 | async_hooks | AsyncLocalStorage |
| --- | --- | --- |
| 稳定性 | Experimental | Stable |
| 用途 | 观测/诊断（资源生命周期、因果图） | 业务上下文传播（请求号/trace/租户） |
| 性能 | 每次 init/before/after 都进 JS 回调 | 官方优化过的存储实现 |
| 典型使用 | APM、诊断工具、异步泄漏检测 | Web 框架中间件、日志打点 |

## 环境

- Node.js ≥ 14（本 demo 实测 v22.22.2）；无第三方依赖
- TS 版用 Node 22 内置 `--experimental-strip-types` 直接运行

## 运行方式

```bash
cd js && node main.js                              # 约 1 秒,16 项断言
cd ts && node --experimental-strip-types main.ts   # TS 版同组断言
```

## 关键代码

- `js/main.js`：E1 Timeout 生命周期事件序；E2 Promise 因果链；E3 ALS 六项语义；E4 三个交错异步请求的请求号追踪（实测交错序 `1,2,3 → 2,2,2 → 3,3,3 → 1,1,1`，各日志归属零串扰）；E5 createHook 迷你 ALS。

## 性能边界

- async_hooks 有全局开销（每个异步资源至少一次 init 回调）；高频短生命周期资源的场景（每秒百万定时器/微任务）会放大它——官方也因此把 Promise 追踪做成"装 hook 才开"。
- ALS 官方实现经过优化，但 `enterWith` 会延长 store 生命周期（泄漏面更大），禁用后仍需 `disable()` 才能回收实例。

## 注意事项与常见坑

- **hook 回调内禁用 `console.log`**（官方：console.log 是异步操作，会无限递归）——调试用 `fs.writeFileSync`；hook 回调**不得是 async 函数**（Promise 本身是被追踪资源）。
- **`run()` 传 async 回调要 return 出去**：`als.run(s, asyncCb)` 返回的是 asyncCb 的 Promise，不 return/await 的话调用方等不到内部异步完成（本 demo E4 开发期实跑暴露：日志只有 start，断言跑在半截数据上）。
- **`destroy` 不可依赖**：GC 相关资源可能永不销毁；在 init 里存 resource 引用 = 内存泄漏。
- **`disable()` 是 GC 前置条件**：长生命周期进程里反复 new ALS 而不 disable 会泄漏。
- 回调式代码丢上下文时，官方建议先 `util.promisify()`（转原生 Promise 后通常恢复），不行再用 `AsyncResource` 手动绑定。

## 参考资料

- Node.js 官方文档 — async_hooks：<https://nodejs.org/api/async_hooks.html>（init/before/after/destroy 语义、executionAsyncId vs triggerAsyncId、Promise 追踪开关、destroy 的 GC 陷阱）
- Node.js 官方文档 — async_context（AsyncLocalStorage）：<https://nodejs.org/api/async_context.html>（run/getStore/exit/enterWith/disable 语义与内存泄漏警告、HTTP 请求号示例、上下文丢失排查）
