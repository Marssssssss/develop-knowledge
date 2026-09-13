# Zustand 极简 store 自实现 (TypeScript 版)

## 简介

与同目录 `js/zustand.js` 等价的 TypeScript 实现,加类型约束。

## 类型化要点

```ts
export type StateCreator<T> = (
  set: (partial: Partial<T> | ((state: T) => Partial<T>), replace?: boolean) => void,
  get: () => T,
  api: StoreApi<T>,
) => T;

export interface StoreApi<T> {
  getState: () => T;
  setState: (partial: Partial<T> | ((state: T) => Partial<T>), replace?: boolean) => void;
  subscribe: (listener: () => void) => () => void;
}
```

`StateCreator` 把 `set / get / api` 三件套注入 initializer;`Partial<T>` 让 setState 只传变更字段而非整个 state。

## 与 JS 版的差别

| 维度 | JS | TS |
|---|---|---|
| 泛型 | 无 | `createStore<T>`,store 元素类型严格 |
| middleware | 函数包裹 | 类型安全的对象合并 `Object.assign` |
| useStore | 仅模拟 | 类型化 `(state: T) => S` 选择器签名 |
| shallow | object 检查 | `T extends object` 约束 |

## 关键设计

### 1. subscribeWithSelector 类型双签名

中间件实际要把 store 的 subscribe 替换成既支持原签名又支持新 selector 签名,类型上用 `as unknown as ...` 规避结构冲突:

```ts
return Object.assign(store, { subscribe: selSub } as unknown as {
  subscribe: typeof selSub;
});
```

实际 zustand 用了类型 union 实现更干净;本 demo 为简洁起见用类型断言。

### 2. shallow 与 useShallow 一致

```ts
export function shallow<T extends object>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true;
  // keys 数量 + 每项 Object.is 浅对比
}
```

`useShallow((state) => ({ a, b }))` 内部就调用 shallow。

## 环境准备

- Node.js 14+
- TypeScript 5+

## 运行方式

```bash
npx tsc --noEmit zustand.ts
```

调用 `demo()` 看输出:

```js
initial: 0
after +2: 2
after reset: 0
shallow(sl1, sl2): true
useBear slice: 0
```

## 性能与边界

- 同 JS 版,O(N) listener,O(K) shallow
- `Partial<T>` 不允许 set 整个 state(防误操作覆盖)
- `Object.assign` 中间件替换 subscribe 不破坏 store 不变量

## 注意事项与常见坑

- ❌ `Object.assign(store, ...)` 看似 hack,实际是 zustand middleware 模式(传入 store 改写后返回)
- ❌ `state as unknown as T` 类型断言:当 initializer 返回 state 但 type 推断为 wider 时需要,生产中 initializer 应直接返回 T 类型
- ✅ useStore 类型 `(state: T) => S` 让 selector 输出类型可推断
- ✅ shallow 对 frozen object 同样适用(Object.keys 仍然返回 keys)

## 参考资料

- [github.com/pmndrs/zustand](https://github.com/pmndrs/zustand) — 官方仓库
- [zustand/middleware](https://github.com/pmndrs/zustand/tree/main/src/middleware) — 几个 middleware 源码
- [`js/README.md`](../README.md) — JS 版同步说明
