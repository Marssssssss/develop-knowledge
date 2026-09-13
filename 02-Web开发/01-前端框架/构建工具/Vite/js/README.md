# Vite 冷启动原理 — 最小模拟

## 简介

Vite 用**浏览器原生 ES Modules** + **依赖预构建** + **按需转换**解决大型 SPA 启动慢、HMR 卡顿的问题。冷启动时间与项目规模**几乎无关**(传统 webpack 全图打包在大项目上 30s+ 才出页面)。

关键概念:

- **原生 ESM**:`<script type="module">` + `import` 由浏览器直接发起请求,跳过"先全量打包再发"
- **依赖预构建**:首次冷启动用 esbuild 把 CommonJS / UMD 依赖转 ESM,缓存到 `node_modules/.vite/deps/`,之后直接加载
- **按需 transform**:源文件不预打包,谁被 import 浏览器请求谁,Vite 仅 transform 该文件
- **HMR**:文件修改通过 WebSocket 推送,vite 客户端只替换对应模块不刷新页面

## 原理详解

### 1. dev server 拦截 import

```js
// 浏览器
import App from '/src/App.jsx';
// 浏览器向 vite dev server 发 GET /src/App.jsx
// vite dev 返回 App.jsx 的 transform 结果(JSX → JS)
```

不像 webpack,不再有"watch + full-bundle + livereload"循环。

### 2. 依赖预构建 — CommonJS / UMD 转 ESM

```text
第一次冷启动:
  vite 解析源码的 bare specifier(`import react from 'react'`)
  → 找到 node_modules/react 是 CommonJS / UMD
  → 调用 esbuild 转 ESM
  → 输出 node_modules/.vite/deps/react.js
  → 浏览器再次请求 react → 命中缓存,直接返回 ESM 版
```

这一步只跑一次,且 esbuild 是 Go 写的,比 webpack Babel / ts-loader 快 100x 量级。

### 3. 按需 transform

```js
// 在 vite 中,源代码不走打包
// 但浏览器需要 ESM 模块图,所以需要:
//  - JSX / TSX → JS
//  - 现代语法(可选链等)→ 浏览器目标版
//  - import 重写(把 'react' 重写到 '/node_modules/.vite/deps/react.js?v=abc')
// 这一切 on-demand,谁第一次用就 compile 谁
```

### 4. HMR — 文件修改后只替模块

```text
[editor save] /src/App.jsx
[vite watcher] 触发 transform /src/App.jsx → 新 ESM
[vite ws] push 'update' 事件到 browser
[browser] 找到 import.meta.hot.accept 回调,只替换 App.jsx 链路,其他模块不动
[browser] 无需刷新页面,无状态丢失
```

### 5. 与 webpack 对比(粗略量级)

| 维度 | Vite | Webpack |
|---|---|---|
| 冷启动 | 100ms(esbuild 100 个依赖) | 10-30s(全图打包 1000 文件) |
| HMR | 50ms(单文件 transform) | 500ms-2s(重打包+全链路注入) |
| 生产构建 | Rollup / Rolldown | Webpack 自己 |
| 服务器依赖 | Node.js dev server | Node.js dev server |
| 配置复杂度 | 中(vite.config.js) | 高(loader / plugin 各种配置) |

### 6. import 解析路径

| spec 类型 | 处理 |
|---|---|
| `./utils.js` | 浏览器原生,直接 GET |
| `/src/utils.js` | dev server 静态服 |
| `react` | Vite 通过 import-analysis 重写到 `/node_modules/.vite/deps/react.js?v=hash` |
| `lodash-es/debounce` | 子路径映射到预构建 deps |
| `node:fs` | Vite SSR 模块图 |
| `virtual:foo` | 插件 `resolveId` + `load` 自定义 |

## 对比 / 选型

| 场景 | Vite | Webpack | Turbopack (Next) |
|---|---|---|---|
| 大型 SPA dev 体验 | ★★★★★ | ★★ | ★★★★★ (Rust) |
| 生产 bundle 优化 | Rollup 成熟 | ★★★★ | ★★★★★ (SWC + bundlerless) |
| 插件生态 | 中(早期) | ★★★★★ | ★★★ |
| 跨平台 SSR | ★★★ | ★★★ | ★★★★★ (Next.js) |

## 环境准备

- Node.js 18+ (esbuild ≥ 0.18)

## 运行方式

```bash
node vite-sim.js
# 期望输出:
# --- demo 1: on-demand source transform ---
# [browser import] /index.html  →  html
# [browser import] /src/main.js  →  source-transformed (46B)
# [browser import] /src/App.jsx  →  source-transformed (52B)
# --- demo 2: CommonJS dep pre-bundling ---
# ...
```

## 关键代码片段

`vite-sim.js` 中:

- `class ViteDevServer`:depsCache + sourceCache 两个 Map 模拟 `node_modules/.vite/deps/` 缓存 + transform 内存缓存
- `async resolve(url)`:分流 /node_modules/.vite → 预构建 + 缓存;源文件 → 走 transform
- `pretendEsbuildBundle`:`// bundled by esbuild` 模拟 esbuild 输出
- demo 5 列表:Vite 对 6 类 import specifier 的处理路径

## 性能与边界

- **depsCache**:每个依赖首跑 esbuild(一次),复用所有后续请求
- **transform**:每次修改 → 单文件 transform,O(1) 时间片
- **HMR**:WebSocket 推送,O(1) 替换模块
- **生产构建**:走 Rollup → 体积更小;新版 Vite 正在迁 Rolldown(基于 Rust,基于 Oxc)

## 注意事项与常见坑

- ❌ `import { foo } from './utils'` 在 ESM 浏览器原生严格,**必须显式 .js** —— 老代码迁移容易踩
- ❌ CommonJS 依赖同步报错时,Vite 已经预构建好的 .vite/deps 是缓存的,删掉重预构建
- ❌ 生产环境也跑 dev server 是错的,Vite 生产构建走 `vite build` → Rollup
- ✅ dev 时 dev server 500 / 性能差,先 `rm -rf node_modules/.vite` 预构建失效

## 参考资料

- [vite.dev/guide/why.html](https://vite.dev/guide/why.html) — 实际访问,The Origins + Growing with the Ecosystem + A Unified Toolchain 三段
- [vite.dev/guide/dep-pre-bundling.html](https://vite.dev/guide/dep-pre-bundling) — 预构建依赖规则
- [MDN ES modules](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules) — 浏览器原生 ESM 装载原理
- [esbuild 官方](https://esbuild.github.io/) — Go 写的 JS bundler,Vite dev 用
- [`ts/README.md`](../README.md) — TypeScript 版同步说明
