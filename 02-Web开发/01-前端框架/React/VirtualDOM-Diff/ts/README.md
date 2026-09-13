# 虚拟 DOM 与 Diff 算法 (TypeScript 版)

## 简介

与同目录 `js/diff.js` 等价的 TypeScript 实现,加类型约束。Demo 输出结构化 ops,而不是 console string。

## 类型化要点

```ts
export interface VNode {
  type: string;
  props: Record<string, unknown> & { key?: string | number | null };
  children: VNode[];
  key: string | number | null;
}

export interface TextVNode extends Omit<VNode, 'type'> {
  type: typeof TEXT_NODE;
  text: string;
}

export type DiffOp =
  | { kind: 'patch'; oldIndex: number; newIndex: number; tail?: boolean }
  | { kind: 'insert'; newIndex: number }
  | { kind: 'remove'; oldIndex: number }
  | { kind: 'move'; newIndex: number };
```

`DiffOp` 用 discriminated union,`kind` 字段是判别符,TYPES 自动按 kind 收窄字段:
- `patch`:`oldIndex, newIndex, tail?`
- `insert`:`newIndex`
- `remove`:`oldIndex`
- `move`:`newIndex`

## 与 JS 版的差别

| 维度 | JS | TS |
|---|---|---|
| vnode shape | 普通对象 | interface + `extends Omit<VNode, 'type'>` 文本节点 |
| ops 输出 | console string 拼接 | 类型化 `DiffOp[]`,可传给后续 renderer |
| isSameVNode | 双等式 | 严格 type check |
| LIS | 函数 | 严格 `Array<number>` 类型 |

## 关键设计

### 1. TextVNode extends Omit

```ts
export interface TextVNode extends Omit<VNode, 'type'> {
  type: typeof TEXT_NODE;
  text: string;
}
```

文本节点复用 VNode 大部分字段,把 type 改为 '\_\_TEXT\_\_' 标志符,加 `text: string`。

### 2. DiffOp discriminated union

```ts
export type DiffOp =
  | { kind: 'patch'; oldIndex: number; newIndex: number; tail?: boolean }
  | { kind: 'insert'; newIndex: number }
  ...
```

consumer 用 switch on `op.kind`,TS 自动收窄:

```ts
switch (op.kind) {
  case 'patch':  // op.oldIndex, op.newIndex 必可见
    renderer.patch(op.oldIndex, op.newIndex);
    break;
  case 'insert': // op.newIndex 必可见
    renderer.insert(op.newIndex);
    break;
}
```

## 环境准备

- Node.js 14+
- TypeScript 5+

## 运行方式

```bash
npx tsc --noEmit diff.ts
```

调用 `runDemos()` 看输出:

```bash
--- demo 1: 同序列 ---
  {"kind":"patch","oldIndex":0,"newIndex":0}
  {"kind":"patch","oldIndex":1,"newIndex":1}
  ...
--- demo 6: LIS 自测 ---
  LIS [3,1,0,2,4] → [0, 2, 4] (期望 [0,2,4])
  ...
```

## 性能与边界

- 与 JS 版一致,O(n log n) ≤ O(n²)
- TS 类型在编译期提供保护,不影响运行时性能

## 注意事项与常见坑

- ❌ discriminated union 必须给每个 case 显式处理;漏 case 编译报错
- ❌ `VNode.props` 是 `Record<string, unknown> & { key?: ... }` 比 VNode 更复杂,故意保留 key 的扩展性
- ✅ `as const` 给 union 字符串,确保 TS 收窄无误
- ✅ algorithm 自包含 `O(n log n)`,无外部依赖

## 参考资料

- [Vue 3 runtime-core/src/renderer.ts](https://github.com/vuejs/core/blob/main/packages/runtime-core/src/renderer.ts)
- [Vue 3 shared/src/getLIS.ts](https://github.com/vuejs/core/blob/main/packages/shared/src/getLIS.ts)
- [inferno GitHub](https://github.com/infernojs/inferno)
- [`js/README.md`](../README.md) — JS 版同步说明
