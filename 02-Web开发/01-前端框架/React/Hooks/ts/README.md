# React Hooks 链表实现 (TypeScript 版)

## 简介

与同目录 `js/hooks.js` 等价,加类型约束,完整字段定义。本目录把 hooks 抽到 `HooksContext` 类,模拟单个 fiber 内的 hooks 链表与调度循环。

## 类型化要点

```ts
export interface Hook {
  memoizedState: unknown;   // useState = 当前值; useEffect = { deps, create }; useRef = { current: T }
  queue: { pending: Update | null };  // setState 的 update 队列(单向环链表)
  next: Hook | null;        // 单链表指针
}

export interface Update<T> {
  action: T | ((prev: T) => T);
  next: Update<T> | null;   // 环链表指针(单元素 self-loop)
}
```

## 与 JS 版的差别

| 维度 | JS | TS |
|---|---|---|
| 边界 | 脚本直跑 | `HooksContext` 类 + `export` |
| update 类型 | 单对象 | `Update<T>` 泛型 |
| reducer | `useReducer((s,a)=>...)` | `useReducer<S, A>` 两个泛型 |
| 调度 | 全局函数 + module 变量 | 实例方法,便于测试 |
| Demo | `hooks.js demo()` | 暴露 `demo(c, fiber)` 给外部跑 |

## 关键设计

### 1. HooksContext 单实例

模拟单个 fiber 内 hooks 链表:

```ts
const c = new HooksContext();
const fiber = { memoizedState: null };  // 像 FiberNode
demo(c, fiber);
demo(c, fiber);  // update 阶段
```

`c.isMount = !fiber.memoizedState` 区分挂载/更新。

### 2. walkNext

```ts
walkNext(): Hook {
  let h = this.currentHook;
  if (h.next === null) h.next = createHook();
  this.currentHook = h.next;
  return h.next;
}
```

链表"懒初始化"——本 fiber 第一次调用某个 hook 时才创建节点,符合 React 内部逻辑。

### 3. useEffect 类型签名

```ts
useEffect(
  create: () => void | (() => void),
  deps: ReadonlyArray<unknown> | null
): void
```

deps 可以是 `null`(意味着每次都跑,等价于 mount-only 或者 unmount-only),TS 严格区分。

## 环境准备

- Node.js 14+
- TypeScript 5+

## 运行方式

```bash
npx tsc --noEmit hooks.ts
```

加 `tsconfig.json` 可设 `strict: true` 跑出更多错误以验证类型严谨度。

## 与实 React 的关键差异

| 维度 | 本 demo | 实 React |
|---|---|---|
| 跨 fiber | 单 fiber | 链表 + alternate 双缓冲 |
| 调度 | 简单 Promise.resolve | scheduler 包 + Lane 优先级 |
| 清理函数 | 立即执行 | commit 后异步 + unmount 同步 |
| StrictMode | 无 | 双调用 mount,验证幂等 |
| Suspense / Transition | 无 | 完整支持 |

## 性能与边界

- 每次 render 走一遍 hooks 链表 O(N),N=本 fiber 的 hooks 数;与 React 一致
- `useRef.current` 突变不会触发 render(本 demo 同步体现)
- `useEffect` 用 `Object.is` 浅对比,引用相等 ≠ 等值相等

## 注意事项与常见坑

- ❌ 多个 `useEffect` 必须稳定顺序,同 `useState`
- ❌ useReducer 的 reducer 是**纯函数**,不应当有副作用
- ✅ `flushQueue<T>` 泛型支持任意 T,consumer 写泛型推断
- ✅ `Object.is` 比 `===` 多 `NaN === NaN = true` 与 `±0 !== ∓0` 两个特例,React 早期用 `Object.is` 防 `NaN` 漏更新

## 参考资料

- [react.dev/reference/react/useState](https://react.dev/reference/react/useState)
- [react.dev/reference/react/useEffect](https://react.dev/reference/react/useEffect)
- [Hooks 链表源码](https://github.com/facebook/react/blob/main/packages/react-reconciler/src/ReactFiberHooks.js)
- [`js/README.md`](../README.md) — JS 版同步说明
