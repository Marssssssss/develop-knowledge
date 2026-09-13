# React Fiber 架构 — 最小实现

## 简介

Fiber 是 React 16 引入的**协调(reconciliation)引擎**,把"渲染"从原来的递归对象树遍历,改为**链表 + 可中断 + 双缓冲**的方案,使 React 能在主线程让出(yield)并在空闲时恢复,为后来的 Concurrent Renderer、Suspense、useTransition / useDeferredValue 铺路。

关键概念:

- **Fiber Node**:链表节点,字段 `child` / `sibling` / `return` 形成可中断遍历的工作单元
- **双缓冲**:内存中同时存在 `current` 树(已提交)和 `workInProgress` 树(正在构建),commit 后指针互换
- **render + commit 两阶段**:render 可中断、commit 同步不可中断
- **scheduler**:5ms 时间片 + 优先级(Lane 模型 React 17+ 取代 expirationTime)

## 原理详解

### 1. FiberNode 字段

```js
{
  type, key, props,
  stateNode,           // 关联的 DOM 节点或 class 实例
  child, sibling, return, // 链表指针(代替 N 叉对象树)
  alternate,           // 双缓冲:指向另一棵树对应节点
  memoizedState,       // Hooks 链表头节点
  effectTag,           // PLACEMENT / UPDATE / DELETION
  nextEffect,          // effect 单链表
}
```

链表 vs 原来的 `child[0]/child[1]/...`:

- 递归调用栈 ≈ 浏览器 V8 默认 10000 层上限,组件树深了直接栈溢出
- 链表遍历可以**在任意节点中断保存进度**,然后恢复
- 不需要为每个节点创建 V8 调用栈帧

### 2. 双缓冲

```
current tree (屏幕可见)    workInProgress tree (内存中构建)
       A ─── alternate ─── A'
      ╱ ╲                 ╱ ╲
     B   C   ←── 隔的 ─→ B'  C'
                            ↑
                     rootFiber = A'
```

`commitRoot` 后 `rootFiber = A'`、`A'.alternate = A`,整个树变成"屏幕可见"。

### 3. Render 阶段(`beginWork` / `completeWork`,可中断)

```text
beginWork(A) → 处理 A 的 props/state → 生成/复用 children
↓
beginWork(B)
  ├─ beginWork(D)
  │    ├─ D 没有 child → completeWork(D)
  │    └─ D 没有 sibling → 沿 return 回到 D.parent
  └─ beginWork(E)
       └─ completeWork(E)
↓ ... 深度优先 + child → sibling 切换
```

每次 `performUnitOfWork` 处理一个 Fiber 节点后,调用 `scheduler.shouldYield()`:

```js
function shouldYield() {
  // 真实 React:performance.now() - frameStartTime > 5ms
  return false;
}
```

返回 true 就把 `nextUnitOfWork` 暂存,主线程先去响应 input/动画/绘制。

### 4. Commit 阶段(同步、不可中断)

所有 effect 已在 render 阶段构建到 `fiber.return.firstEffect → ... → lastEffect` 单链表,commit 阶段顺序遍历执行:

```text
PLACEMENT → parentNode.appendChild(childNode)
UPDATE    → 写属性 / update styles
DELETION  → parentNode.removeChild(childNode)
```

### 5. 调度模型

| React 16 | React 17+ |
|---|---|
| `expirationTime`(单一时间戳) | **Lane 模型**(二进制位掩码,可同时存在多个优先级) |
| 6 级硬编码 | 31 个 Lane 自由组合(Transition / Sync / Default…) |
| 一个 root 一个 deadline | fiberRoot 树每条 lane 独立调度 |

### 6. 为什么 render 可中断而 commit 不可

- render 阶段都是"算 JSX / diff"——纯计算,不碰 DOM,中断/恢复安全
- commit 阶段直接改 DOM 并触发浏览器的 layout/paint——必须同步,否则视觉错乱

## 对比 / 选型

| 方案 | 用途 | 复杂度 | 可中断 |
|---|---|---|---|
| 旧栈协调 (React 15) | 简单应用 | 递归对象树 | ❌ |
| Fiber (React 16/17) | 大型 SPA | 链表 + workLoop | ✅ |
| Lane (React 18+) | 并发 / Suspense | 位掩码优先级 | ✅✅ |

## 环境准备

- 操作系统:跨平台
- 语言:Node.js 14+ (仅 ES2017+ 语法)
- 依赖:无(`fiber.js` 是纯 JS)

## 运行方式

```bash
node fiber.js
# 期望输出:
# [render OK] ops: PLACEMENT,PLACEMENT,PLACEMENT,PLACEMENT,PLACEMENT
# [commit OK] tree: div → h1 → p → section → span → span
```

## 关键代码片段

`fiber.js` 中:

- 第 50-72 行 `createFiber + createWorkInProgress`:Fiber 节点创建 + alternate 复用
- 第 110-145 行 `reconcileChildren`:同父兄弟链表复用与新建
- 第 162-180 行 `completeWork`:把 effect 追加到父级的单链表
- 第 213-242 行 `workLoop + commitRoot`:render 循环 + commit 同步阶段

## 性能与边界

- **workLoop**:每次 `performUnitOfWork` 一个节点,O(n) 遍历
- **reconcileChildren**:本 demo 是简化的"同 type 复用,否则 PLACEMENT"——实 React 有 key 表 + 单端/双端 diff
- **commit**:O(n),不可中断,大树提交可能占主线程 100ms+ — 这是 Suspense/并发切片要解决的问题
- **Lane 模型 31 个**位,materialize 在 `react-reconciler/src/ReactFiberLane.js`

## 注意事项与常见坑

- ❌ render 与 commit 不要混在一起,否则 DOM 状态错乱
- ❌ commit 阶段不能调用 `setState`,会导致 commit 时 parent 已变更、child 还在旧树
- ✅ 同父 Fiber 的 `effectTag` 只增不减,所有 effect 在 commit 阶段按 effect list 顺序应用
- ✅ `alternate` 是双向的,不只新→旧,旧也要指新
- ✅ commit 后 `current` 与 `wipRoot` 角色互换,`root.current = wipRoot`

## 参考资料

- [A Cartoon Intro to Fiber — Lin Clark 视频分享](https://www.youtube.com/watch?v=ZCuYPiUIONs) — 把 Fiber 拆解为"自己 / child / sibling / return"四角色,本 README 第一段心智模型来源
- [react.dev/learn/render-and-commit](https://react.dev/learn/render-and-commit) — React 官方 render+commit 三阶段权威说明
- [react.dev/learn/queueing-a-series-of-state-updates](https://react.dev/learn/queueing-a-series-of-state-updates) — batching / setState 与 render 关系
- [facebook/react/packages/react-reconciler/src/ReactFiber.js](https://github.com/facebook/react/blob/main/packages/react-reconciler/src/ReactFiber.js) — Fiber Node 字段定义
- [facebook/react/packages/react-reconciler/src/ReactFiberWorkLoop.js](https://github.com/facebook/react/blob/main/packages/react-reconciler/src/ReactFiberWorkLoop.js) — workLoop + commitRoot
- [react-reconciler/Lane 模型](https://github.com/facebook/react/blob/main/packages/react-reconciler/src/ReactFiberLane.js) — 31 Lane 位掩码
