# 前端框架

## 子领域

- [React/](./React/) — Fiber 架构、Hooks 链表、虚拟 DOM Diff、Concurrent Mode
- [Vue/](./Vue/) — 响应式原理、Composition API、Diff 算法
- [构建工具/](./构建工具/) — Vite / Webpack / Turbopack / esbuild / Rolldown
- [状态管理/](./状态管理/) — Zustand / Redux / Pinia / Jotai / Recoil

## 已完成 demo

- [x] [Vue/reactive/](./Vue/reactive/) — Vue 3 Proxy 响应式最小实现 (TS + JS)
- [x] [React/Fiber/](./React/Fiber/) — Fiber 链表 + 双缓冲 + render/commit 两阶段(workLoop 自实现,JS + TS)
- [x] [React/Hooks/](./React/Hooks/) — Hook 链表 + dispatcher + setState 环链表 + useEffect 依赖浅对比(JS + TS)
- [x] [React/VirtualDOM-Diff/](./React/VirtualDOM-Diff/) — 双端指针 + key 哈希 + LIS 乱序最小移动(JS + TS)
- [x] [状态管理/Zustand/](./状态管理/Zustand/) — 极简 vanilla store + useStore selector + Object.is 精确订阅 + shallow + 中间件(JS + TS)
- [x] [构建工具/Vite/](./构建工具/Vite/) — ESM 冷启动 + 依赖预构建 + on-demand transform + HMR + import specifier 分类(JS + TS)

## 待研究

- [ ] React Concurrent Mode / Suspense / RSC
- [ ] Vue 3 编译器优化(模板编译时静态 hoist)
- [ ] Turbopack / Rolldown Rust 构建链