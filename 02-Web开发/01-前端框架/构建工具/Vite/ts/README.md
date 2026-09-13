# Vite 冷启动原理 (TypeScript 版)

## 简介

与同目录 `js/vite-sim.js` 等价的 TypeScript 实现,加类型约束,演示 Vite dev server 的核心数据流。

## 类型化要点

```ts
export type UrlKind = 'html' | 'dep-cached' | 'dep-built' | 'source-transformed';

export interface ResolveResult {
  kind: UrlKind;
  url: string;
  size?: number;
  out?: string;
  mtime?: number;
}
```

`kind` 区分 html / 已缓存依赖 / 刚预构建依赖 / 源代码 transform;`size`/`out`/`mtime` 可选字段对应不同 kind。

## 与 JS 版的差别

| 维度 | JS | TS |
|---|---|---|
| 状态 | 类公开字段 | `private depsCache/sourceCache/timers` |
| 函数签名 | 隐式 | `(url: string): Promise<ResolveResult>` |
| try/catch | 无 | 可以加 `Promise.catch`(本 demo 未做) |
| demo | 顶层表达式 | `runDemos()` 函数调用 |

## 关键设计

### 1. ViteDevServer 私有字段

```ts
private depsCache = new Map<string, string>();
private sourceCache = new Map<string, { out: string; mtime: number }>();
private timers: ViteStats = { cacheMiss: 0, transform: 0 };
```

`#field` 在 ES2022 后是真正的私有,本 demo 用 TS `private` 关键字可读性更好。

### 2. 返回类型的可选字段

```ts
return { kind: 'source-transformed', url, size: t.out.length, out: t.out, mtime: t.mtime };
```

TS 允许 `size? / out? / mtime?` 可选 consumer 在用 `result.out` 时可能为 undefined,需要在调用处判空。

## 环境准备

- Node.js 18+
- TypeScript 5+

## 运行方式

```bash
npx tsc --noEmit vite-sim.ts
```

调用 `runDemos()` 见输出。

## 性能与边界

- 同 JS 版,O(1) 缓存命中,O(单文件) on-demand transform
- TypeScript `private` 修饰仅编译期检查,运行时仍可访问
- `Map` 替代 `Object` 防原型污染

## 注意事项与常见坑

- ❌ TS 可选字段输出会要求 consumer 判空:`if (result.out) process(result.out)`
- ❌ `private` 修饰只是编译期,真实隔离可用 `#field` syntax(Target ES2022+)
- ✅ Vite dev cache 大小可达几 GB,生产环境记得 `rm -rf node_modules/.vite`

## 参考资料

- [vite.dev/guide/why.html](https://vite.dev/guide/why.html)
- [vite.dev/guide/dep-pre-bundling](https://vite.dev/guide/dep-pre-bundling)
- [`js/README.md`](../README.md) — JS 版同步说明
