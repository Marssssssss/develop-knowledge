# Zustand — 状态管理极简自实现

## 简介

Zustand 是一个轻量级 React 状态管理库:**无 Provider,无需 Context,无需 reducer,默认基于 React 18 `useSyncExternalStore` 做精确订阅**。核心 30 行 store + React 桥接 30 行 hook,合计 ~200 行实现完整功能。

关键概念:

- **vanilla store**:`createStore((set, get, api) => state)` 生成 `getState / setState / subscribe / getInitialState` 四件套
- **set 浅合并**:`set(partial)` 默认浅合并;`set(replace=true)` 全替换
- **useStore hook**:以 `useSyncExternalStore` 桥接到 React,订阅 store 变化并触发 selector 重算
- **中间件函数式**:`persist / devtools / immer / subscribeWithSelector` 都是高阶函数包装 store

## 原理详解

### 1. 极简 vanilla store 核心代码

```js
function createStore(initializer) {
  let state;
  const listeners = new Set();
  function setState(partial, replace = false) {
    const next = typeof partial === 'function' ? partial(state) : partial;
    if (next == null) return;
    state = replace ? next : (typeof next === 'object' ? { ...state, ...next } : next);
    for (const l of listeners) l();
  }
  function subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); }
  state = initializer((p) => setState(p), () => state, { getState, setState, subscribe });
  return { getState, setState, subscribe };
}
```

可以独立于 React 运行,浏览器 console 都能用。

### 2. useSyncExternalStore 桥接

React 18 新 hook,签名:

```ts
const value = useSyncExternalStore(
  subscribe: (callback) => Unsubscribe,
  getSnapshot: () => T,
  getServerSnapshot?: () => T
);
```

zustand 的 `useStore` 内部把 store.subscribe 转发给 React,store.getState 调用 selector 得到当前 slice。当 store 触发 listener,React 调度一次 render,**只调用 selector 重新计算**;若与旧值 `Object.is` 不同,组件才 rerender。

### 3. selector 精确订阅 vs 全量订阅

```js
// 普通订阅(每次任何变化都触发)
store.subscribe(() => render());

// selector 订阅(只有 selector 输出变化才触发)
store.subscribe((state) => state.bears, (bears, prev) => {
  if (prev !== bears) render();
});
```

后者由 `subscribeWithSelector` 中间件提供。React 组件层 useStore 默认就是 selector 版,这就是 zustand 比 `useContext` 性能高的根因。

### 4. 中间件函数式组合

```js
const useStore = create(
  subscribeWithSelector(
    persist(
      devtools((set) => ({ ... })),
      { name: 'app' }
    )
  )
);
```

- `persist`:localStorage / sessionStorage 持久化
- `devtools`:Redux DevTools 集成
- `subscribeWithSelector`:为手动场景加 selector
- `immer`:draft 风格 mutate
- `combine`:`createStore` + 类型推导

### 5. shallow / useShallow

对象/数组选择器用 `useShallow` 防每次 render:

```js
import { useShallow } from 'zustand/react/shallow';
const { nuts, honey } = useStore(useShallow((s) => ({ nuts: s.nuts, honey: s.honey })));
```

内部基于浅对比 `Object.keys.length` + 每项 `Object.is`。

## 对比 / 选型

| 维度 | Redux | Zustand | Recoil/Jotai |
|---|---|---|---|
| Provider | 必须 | 不要 | 必须 |
| 状态粒度 | single store | N 个独立 store | atom 颗粒 |
| 触发粒度 | 全组件 mapStateToProps | selector 精确 | atom-level |
| 中间件 | 大量 | 大量,函数式 | limited |
| 学习曲线 | 中(actions/reducers/types) | 低(API 一目了然) | 中 |
| TS 友好 | 优秀 | 优秀 | 优秀 |

## 环境准备

- Node.js 14+(ES2017+)

## 运行方式

```bash
node zustand.js
```

期望输出:

```
--- demo 1: minimal store ---
initial: { bears: 0, increase: ..., reset: ... }
after +2: { bears: 2, increase: ..., reset: ... }
after reset: { bears: 0, increase: ..., reset: ... }
--- demo 2: subscribe (without selector) ---
  total bears changed: 1
  total bears changed: 2
--- demo 3: subscribe with selector ---
  paw changed: true → false
--- demo 4: manual useStore hook (vanilla React) ---
  initial slice: 0
...
```

## 关键代码片段

`zustand.js` 中:

- L11-37:`createStore` vanilla core(set shallow merge + replace)
- L40-58:`subscribeWithSelector` 中间件(比较 selector 输出值,值变才回调)
- L62-71:`shallow` 全等判断
- L74-90:`useSyncExternalStore` 的最简模拟 + selector
- L120-140:demo 4-6 演示不同使用场景

## 性能与边界

- 全量订阅 O(N listeners × 触发) 每个 listener 都被调
- selector 订阅 O(N listeners) 但只有 selector 输出变化才回调
- 浅对比 shallow O(K) K=对象 key 数,数组用长度对比

## 注意事项与常见坑

- ❌ 不要在 selector 里返回新对象字面量:`useStore((s) => ({ a: s.a }))` 每次触发新引用,渲染永远跑。用 `useShallow`。
- ❌ `set` partial 为 `null` 或 `undefined` 被忽略(设计如此)
- ❌ 状态必须**不可变更新**:`set((s) => ({ bears: s.bears + 1 }))` 而非 `s.bears++`
- ✅ selector 内调用其他 selector 不触发额外订阅,只读当前 snapshot
- ✅ React 外也能用:`createStore` 出来的 vanilla store 可独立订阅

## 参考资料

- [github.com/pmndrs/zustand](https://github.com/pmndrs/zustand) — 官方仓库与 README(本 demo API 直接对齐)
- [React useSyncExternalStore 官方](https://react.dev/reference/react/useSyncExternalStore) — 桥接 hook 的内部机理
- [How to use zustand without React](https://github.com/pmndrs/zustand/blob/main/docs/guides/how-to-use-zustand-without-react.md) — vanilla store 用法
- [`ts/README.md`](../README.md) — TypeScript 版同步说明
