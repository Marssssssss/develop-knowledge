# React Hooks — 链表实现原理

## 简介

Hooks 是 React 16.8 引入的「在函数组件里使用 state / 副作用」的方案。其内部其实是用一条**单链表 + 全局 dispatcher** 在每次 render 时追踪 hooks 的顺序与状态。

关键概念:

- **Hook 节点**:`{ memoizedState, queue, next }`,挂在 `Fiber.memoizedState.next` 上
- **dispatcher**:全局变量,`mount` 时挂 mount 函数集合,`update` 时切换到 update 版本
- **setState 排队**:state 更新是 `update → next → next → ...` 的环链表,render 时消费
- **useRef**:返回同一个对象 `{ current }`,不参与 setState 流程
- **useEffect**:依赖数组浅对比,commit 后异步执行 `create()`

## 原理详解

### 1. 链表节点结构

```js
function createHook() {
  return {
    memoizedState: undefined, // 该 hook 的"值"
    queue: { pending: null },  // 等待执行的 update 队列(单向环链表)
    next: null,               // 链接到下一个 hook
  };
}
```

`Fiber.memoizedState` 单链:第一条 hook 永远挂在 `fiber.memoizedState`,`hook.next` 链下去。

### 2. dispatcher 全局变量 + mount/update 双形态

```js
let isMount = true;
let currentHook = { next: null };  // 当前 fiber 的 hook 链表头

// mount 阶段:dispatcher = mountDispatch
// update 阶段:dispatcher = updateDispatch(同 useState 实现但有差异)
```

每个 hook 在 mount 时挂上 `pending update`,update 时遍历 `queue.pending` 环链表,依次执行 reducer / setter 函数。

### 3. useState 完整流程

```js
function useState(initial) {
  let hook = currentHook.next ?? (currentHook.next = createHook());
  if (isMount) hook.memoizedState = initial;

  const setState = (action) => dispatchAction(hook, action);

  // update 阶段:消费 pending queue
  if (hook.queue.pending) {
    let p = hook.queue.pending.next;
    do { p = p.next; /* 计算 */ } while (p !== hook.queue.pending);
    hook.queue.pending = null;
  }

  currentHook = hook;
  return [hook.memoizedState, setState];
}
```

`dispatchAction` 把 update 接成**环链表**(1 元素时 self-loop,多个时 `pending.next` 始终指向最早,`pending` 指向最新),render 时从头到尾消费:

```js
update.next = update;                  // 第一个 update:自己指向自己
update.next = q.pending.next;          // 后续:插入到 head 之前
q.pending.next = update;
q.pending = update;
```

### 4. useReducer / useRef / useMemo / useEffect

| Hook | memoizedState 内容 | render 时行为 |
|---|---|---|
| useReducer | `{ state, dispatch }` | 与 useState 共享机制 |
| useRef    | `{ current }` 对象 | 不参与 update queue |
| useMemo   | `{ value, deps }` | `deps` 浅对比,变化重算 |
| useEffect | `{ deps, create }` | commit 后异步执行 `create()`,返回 `cleanup` 在下次 deps 变 / 卸载调用 |

### 5. 关键约束 — 调用顺序不能变

```jsx
function Comp({ cond }) {
  const [a] = useState();  // hook 1
  if (cond) return null;
  const [b] = useState();  // hook 2 (但上次没调用)
  return <p>{a}{b}</p>;
}
```

每次 render hooks 必须**按相同顺序调用同数量**,否则链表长度错位、值错位。这就是 `rules-of-hooks` 规则来源。

## 对比 / 选型

| 维度 | Class component state | Hooks |
|---|---|---|
| 复用单位 | HOC / Render Props | 自定义 hook |
| 顺序依赖 | 无 | 强 — 必须按相同顺序调用 |
| 实现开销 | 每实例一份 state | 每 render 走一遍链表 |
| React DevTools | `setState` 链路直观 | `value + dispatcher` 链路 |

## 环境准备

- Node.js 14+ (ES2017+ 语法)
- React 不需要(本 demo 完全自实现 dispatcher 与 Hooks)

## 运行方式

```bash
node hooks.js
# 期望输出:
# [render MOUNT] count = 0 , todo.value = 0 , ref === null? true , squared = 0
# [effect] count changed to 0
# [render UPDATE] ...
# ...
```

## 关键代码片段

`hooks.js` 中:

- L11-20:`createHook` 链表节点
- L26-50:`dispatchAction` + 环链表挂接
- L71-92:`useState` mount/update + 消费 pending queue
- L94-110:`useReducer` / `useRef` / `useMemo` 同 baseHook 实现
- L112-130:`useEffect`,依赖数组 `Object.is` 浅对比
- L150-180:`renderApp` 与 mount/update 切换

## 性能与边界

- 每次 render 都要遍历当前 fiber 的 hooks 链表,O(N) N=本 fiber 的 hooks 数
- `useState` 中传入 function 比对象字面量稍贵(每次调用,需比较旧值);React 在 18+ 用 `useId` / 自动 batching 优化
- `useEffect` 内返回 cleanup,commit 后异步执行,**不会阻塞渲染**
- `useMemo` 缓存判定用 `Object.is`,结构化比较需要 `useMemo + useCustomEquals` 或第三方

## 注意事项与常见坑

- ❌ hooks 不能放在 if / for / try 中(顺序依赖)
- ❌ 不要在 `useState` 的 `setX` 内直接读 current state(闭包陷阱):用 `setX(prev => ...)`
- ❌ `useRef.current` 突变不触发 render,要 UI 同步应用 `useState`
- ✅ `useEffect` 在 mount 时一定会跑一次,即使 deps 是 `[]`
- ✅ mount 时 memoizedState = initial,update 时不再覆盖

## 参考资料

- [react.dev/reference/react/useState](https://react.dev/reference/react/useState) — useState 官方 API
- [react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect) — useEffect 官方 API
- [react.dev/reference/rules/rules-of-hooks](https://react.dev/reference/rules/rules-of-hooks) — 调用顺序铁律
- [Hooks 链表真实源码](https://github.com/facebook/react/blob/main/packages/react-reconciler/src/ReactFiberHooks.js) — 完整实现参照
