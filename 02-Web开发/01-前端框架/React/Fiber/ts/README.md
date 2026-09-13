# React Fiber 架构 (TypeScript 版)

本目录包含与同目录 `js/fiber.js` 等价的 TypeScript 实现,加类型注解便于编译期检查:

- `Fiber` interface:完整字段定义(`child`/`sibling`/`return`/`alternate`/`effectTag`/`nextEffect` 等)
- `VNode` interface:type 字段允许字符串或 `FunctionComponent`
- 模块级 `nextUnitOfWork` / `wipRoot` / `currentRoot` 与 JS 版本保持一致

## 类型化要点

```ts
export type FiberTag = 'PLACEMENT' | 'UPDATE' | 'DELETION';

export interface Fiber {
  type: VNode['type'];           // string | FunctionComponent
  child: Fiber | null;
  sibling: Fiber | null;
  return: Fiber | null;          // 父节点
  alternate: Fiber | null;       // 双缓冲
  memoizedState: unknown;        // Hooks 链表头
  effectTag: FiberTag;
  nextEffect: Fiber | null;
}
```

## 与 JS 版的差别

| 维度 | JS | TS |
|---|---|---|
| 函数签名 | 无类型 | `h(type: VNode['type'], props?: ...)` |
| `tag` 字面量 | 字符串隐式 | `'PLACEMENT' \| 'UPDATE' \| 'DELETION'` 字面量联合 |
| stateNode 类型 | `any` | `HTMLElement \| null`(虽简化未真创建,但类型正确) |
| module 边界 | 普通 | `export` 允许跨文件复用 |
| 副作用 | console.log | console.log |

## 环境准备

- Node.js 14+
- TypeScript 5+

## 运行方式

仅类型检查(`tsc` 不连接运行时):

```bash
npx tsc --noEmit fiber.ts
```

或启用项目级:

```bash
npx tsc -p .       # 需 tsconfig.json
```

实际运行需把末尾 `// render(tree);` 注释去掉,但本目录没有真 DOM 环境,只输出 `[render OK] ops: ...` + `[commit OK] tree depth=N` 验证渲染树。

## 关键代码片段

`fiber.ts` 行号(以本文件 ≤ 200 行为准):

- L11-25:类型别名与 Fiber interface
- L29-36:`h()` 与 React.createElement 等价的最小版
- L40-52:`createFiber`,字段全 null 初始化
- L56-72:`createWorkInProgress`,复用 alternate
- L100-130:`reconcileChildren`,同 type 复用 / 否则 PLACEMENT
- L137-152:`completeWork`,父级 firstEffect/lastEffect 链表 append

## 性能与边界

- 同 JS 版,详见 `js/README.md`
- `effectTag` 联合类型让不存在的枚举(如 `MOVE`)编译期就报错
- type 兼容函数组件与 string,但 `key` 字段是 Fiber 复用判据,在 demo 中未使用

## 注意事项与常见坑

- ❌ 不真创建 DOM:本目录只演示数据流,实 React 才走 `document.createElement`
- ❌ `alternate` 在 TS 版必须显式 `null` 初始,不然 `if (current.alternate === null)` 报错
- ✅ TS 版可被上层 app 单独 import(`import { render } from './fiber'`)而 JS 版是脚本直接执行
- ✅ `nextEffect` 在 react reconciler 内是单向链表;commit 阶段按序遍历

## 参考资料

- [react.dev/learn/render-and-commit](https://react.dev/learn/render-and-commit) — render/commit 三阶段官方说明
- [facebook/react/packages/react-reconciler](https://github.com/facebook/react/tree/main/packages/react-reconciler) — Fiber 真实源码
- [Hooks / Fiber Lane 模型](https://github.com/facebook/react/blob/main/packages/react-reconciler/src/ReactFiberLane.js) — 31 Lane 优先级位
- [`js/README.md`](../README.md) — JS 版同步说明
