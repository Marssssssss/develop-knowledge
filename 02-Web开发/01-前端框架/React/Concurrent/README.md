# React 并发渲染 — Lane 模型 / 时间切片 / Suspense

## 简介

React 18 最重要的变化不是某个 API,而是**渲染变成了可中断的**(Concurrent React)。官方原文:

> "A key property of Concurrent React is that rendering is interruptible. ... React may start rendering an update, pause in the middle, then continue later. It may even abandon an in-progress render altogether."
> —— [react.dev/blog/2022/03/29/react-v18](https://react.dev/blog/2022/03/29/react-v18)

本 demo 用 ~290 行零依赖 JS 把四件事拆开实现并逐项断言:

- **Lane 位掩码**:用 32 位整数同时表达"优先级"和"批"(React 18 取代 React 16 的 expirationTime)
- **时间切片**:render 阶段每 5ms 让出主线程,commit 阶段同步不可中断
- **transition 被打断**:紧急更新插队 → 未提交的渲染工作**整段丢弃** → 从头重渲染
- **Suspense 边界**:子组件"抛 thenable",边界捕获后先渲染 fallback,数据就绪后重试

## 原理详解

### 1. Lane:一个整数同时表达"优先级"和"批"

React 16 用 `expirationTime`(线性时间戳)表示优先级:优先级 = 过期时间的先后。问题是它把**排序**和**批**两个概念耦合在一起 —— 想表达"u1、u2、u3 同属一批但不含 u4"这种不相邻集合就做不到。

React 18 改成位掩码:**越低的位 = 越高的优先级**,`1` 个 bit 是一条 lane:

```js
const SyncLane = 0b0000000000000000000000000000001;               // bit0  离散事件 click/keydown
const InputContinuousLane = 0b0000000000000000000000000000100;    // bit2  drag/scroll
const DefaultLane = 0b0000000000000000000000000010000;            // bit4  网络/普通 setState
const TransitionLane1 = 0b0000000000000000000000001000000;        // bit6  useTransition
const TransitionLanes = 0b0000000001111111111111111000000;        // bit6..bit21,共 16 条
const IdleLane = 0b0100000000000000000000000000000;               // bit29
const OffscreenLane = 0b1000000000000000000000000000000;          // bit30
```

位运算直接给出 O(1) 的调度原语:

| 需求 | 实现 | 代价 |
| --- | --- | --- |
| 取最高优先级 | `lanes & -lanes` | 1 条指令(补码性质) |
| 合并成一批 | `a \| b` | 1 条指令(可跨不相邻位) |
| 判包含 | `(set & subset) === subset` | 1 条指令 |
| 清除已完成 | `lanes & ~subset` | 1 条指令 |

`TotalLanes = 31`:`1 << 31` 在有符号 32 位里是负数,所以最高只能用 bit30。

### 2. 两段优先级转换

React 内部优先级(`EventPriority`)与 Scheduler 优先级**不互通**,要转两次:

```
lanes → lanesToEventPriority() → EventPriority → eventPriorityToSchedulerPriority() → Scheduler
```

`DiscreteEventPriority → ImmediateSchedulerPriority`(点击)、`Continuous → UserBlocking`(滚动)、`Default → Normal`、`Idle → Idle`。这样 React 的细粒度 lane 才能映射到 Scheduler 的任务队列上。

### 3. 过期升级:防饿死

用户持续打字会不断产生 `SyncLane`,低优 transition 可能永远排不上。React 给每条 lane 一个**最迟完成时间**:首次被观察到时写入 `currentTime + timeout`(Sync 0ms / 连续输入 250ms / 其余 5000ms / Idle、Offscreen 永不过期),超时后 lane 进 `expiredLanes`,而 `getNextLanes` **优先返回 expiredLanes** —— 即强制同步渲染。

关键:过期**不是取消**。`pendingLanes` 不变,只是插队。demo 里断言了这一点,也断言了"过期点 = 首次观察时刻 + 5000,而不是绝对的 5000ms"。

### 4. 时间切片

render 阶段把工作拆成 Fiber 单元,单元之间检查预算:

```js
shouldYield() { return now - sliceStart >= 5; }   // React Scheduler 默认 5ms 帧预算
```

实测(1200 个单元 × 0.05ms):

| 模式 | 时间片数 | 让出次数 | 单帧最长阻塞 |
| --- | --- | --- | --- |
| 同步渲染 | 1 | 0 | 60.0ms(**掉 4 帧**) |
| 并发渲染 | 12 | 11 | 5.05ms(**不掉帧**) |

总工作量不变,墙钟反而从 60ms 涨到 236ms —— 切片买到的是**响应性**,不是吞吐。

### 5. transition 被打断 → 丢弃陈旧渲染

官方原文:"A state update marked as a Transition **will be interrupted by other state updates**"。demo 断言了三件事:

1. 被打断时已完成的 100 个单元**被丢弃**(`discardedUnits = 100`,不提交)
2. 提交顺序恒为 `input:urgent → transition:results`(输入框先响应)
3. transition 一共 render 两次:1 次作废 + 1 次重来。**中间态从未上屏**

这也是为什么 `startTransition` 只能包**非受控输入**的 state:"Transition updates can't be used to control text inputs."

### 6. Suspense 边界

子组件 `throw` 一个 thenable,`try/catch` 捕获后先渲染 fallback:

| 场景 | 首屏可见内容 | 慢区域 |
| --- | --- | --- |
| 不用边界 | 只有整页骨架(header/nav/aside/footer 全被替换) | `full-page` 骨架 |
| 用边界 | header / nav / aside / footer **全部可见** | `posts` 骨架 |

数据就绪后重试同一边界:`readCount = 2`,`fallback` 消失、内容出现,而**兄弟节点从未被卸载** —— 这就是"区域级加载"和"全屏加载"的差别。抛出的对象是 thenable 本身而非 Error,是这条路径与 Error Boundary 的本质区别。

## 对比:expirationTime vs Lane

| 维度 | expirationTime(React 16) | Lane(React 18) |
| --- | --- | --- |
| 数据结构 | 单调递增时间戳 | 31 位掩码 |
| 优先级比较 | 比大小 | `lanes & -lanes` |
| 表达"批" | 隐式:同区间即同批 | 显式:`a \| b`,可跨不相邻位 |
| 多更新并行 | 顺序执行,一个赢者 | 可交错,可纠缠(entangle) |
| 中断恢复 | 只能续跑 | 可暂停 / 可丢弃重来 |

## 环境

- Node.js ≥ 14(本地用 22.22.2 实测),零第三方依赖
- TS 版仅作静态说明,本机无 tsc 未编译(与仓库其它 `ts/` 目录同策略)

## 运行方式

```bash
cd 02-Web开发/01-前端框架/React/Concurrent/js
node concurrent.js     # 37 项断言,全部通过时 exit 0
```

## 关键代码

```js
// 调度核心:取最高优先级 → 转 Scheduler 优先级
function getNextLanes(root) {
  if (root.pendingLanes === NoLane) return NoLane;
  if (root.expiredLanes !== NoLane) return root.expiredLanes;   // 过期插队,防饿死
  const nonIdle = root.pendingLanes & NonIdleLanes;
  return getHighestPriorityLane(nonIdle !== NoLane ? nonIdle : root.pendingLanes);
}

// 可中断工作循环:单元之间检查预算,让出后浏览器绘一帧
for (let i = 0; i < units.length; i++) {
  if (sched.shouldYield()) { sched.endSlice(); sched.paint(); sched.beginSlice(); }
  sched.now += units[i].costMs;
}
```

## 性能边界

- **Lane 位运算**:O(1) 单指令,但 `TotalLanes` 上限 31 条,新的优先级类别需要复用/收回 lane
- **时间切片**:单节点自身耗时就可能超过 5ms(巨型组件),切不动 —— 需配合 `useDeferredValue` 或虚拟列表
- **并发渲染墙钟更慢**:让出 + 绘制 + 重渲染的额外成本换响应性;demo 实测 60ms → 236ms
- **丢弃重来的成本**:transition 被打断 N 次就重渲染 N 次,连续输入会放大开销(React 对并发 transition 做批处理)
- **Suspense 重试**:每次挂起 → 重试都是一次完整 render,`readCount` 精确等于渲染尝试次数

## 注意事项与常见坑

- `1 << 31` 溢出成负数,`lanes & -lanes` 对负数会失效 —— 所以 `TotalLanes = 31`
- `lanes & -lanes` 取的是**最低置位**;写成 `lanes & -lanes` 之外的高位取法会拿到最低优先级
- 位掩码字面量极易数错位数:`0b…1111…` 少写一个 1 就变成 14 条 transition lane(本 demo 首轮自检就是这么挂的)
- 过期时间戳是**首次观察时刻 + timeout**,不是绝对时刻;写错会让"防饿死"提前或永不触发
- transition 不能用于受控输入;`await` 之后的 setState 必须再包一层 `startTransition` 才会被视为 transition
- 同步 render 与并发 render 的总工作量必须相等 —— 不等则说明切片逻辑漏算或重算了工作单元(本 demo 断言了不重不漏)
- 不要用浮点精确比较时间片耗时(0.05 × 1200 = 59.99999999999873),要留容差

## 参考资料

实际读过:

- [React v18.0 官方发布博客](https://react.dev/blog/2022/03/29/react-v18) —— 并发渲染"可中断"、transition 定义、Streaming SSR 与选择性水合、自动批处理
- [react.dev — useTransition](https://react.dev/reference/react/useTransition) —— transition 会被其他更新打断、过时渲染被丢弃、`isPending` 切换时机、transitions 与 Suspense 的等待语义
- [react.dev — hydrateRoot](https://react.dev/reference/react-dom/client/hydrateRoot) —— 水合期 Suspense 边界的行为与常见不匹配原因
- [Part 3 — Concurrent Rendering & Lanes (React 18)](https://dev.to/nehamalviaaa/part-3-concurrent-rendering-lanes-react-18-2n0e) —— lanes 取代 expirationTime 的动机、纠缠(entanglement)
- [React Fiber Architecture and Concurrent Mode Under the Hood](https://www.codingpancake.com/2026/08/react-fiber-architecture-and-concurrent.html) —— lane 位值表、`lanes & -lanes`、优先级升级(5000ms 过期)
- [React Lane 模型:优先级与批处理的解耦革命](https://juejin.cn/post/7495711064873795638) —— 两段优先级转换(`lanesToEventPriority` / `eventPriorityToSchedulerPriority`)的源码片段

> Lane 位值与 `getTimeout` 启发式来自上述社区对 `ReactFiberLane.js` 的精读转录,**非官方文档**,常量仅供演示,可能与最新 main 有差异。
