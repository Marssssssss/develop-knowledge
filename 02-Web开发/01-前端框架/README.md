# 前端框架

## 子领域

- [React/](./React/) — Fiber 架构、Hooks 链表、虚拟 DOM Diff、Concurrent 并发渲染
- [Vue/](./Vue/) — 响应式原理、编译器优化(静态提升 / PatchFlags / Block Tree)
- [Signals/](./Signals/) — TC39 Signals 提案(Stage 1):push-pull 细粒度响应式
- [SSR-Hydration/](./SSR-Hydration/) — 流式 SSR(renderToPipeableStream)+ 选择性水合
- [构建工具/](./构建工具/) — Vite / Webpack / Turbopack / esbuild / Rolldown
- [状态管理/](./状态管理/) — Zustand / Redux / Pinia / Jotai / Recoil

## 已完成 demo

- [x] [Vue/reactive/](./Vue/reactive/) — Vue 3 Proxy 响应式最小实现 (TS + JS)
- [x] [React/Fiber/](./React/Fiber/) — Fiber 链表 + 双缓冲 + render/commit 两阶段(workLoop 自实现,JS + TS)
- [x] [React/Hooks/](./React/Hooks/) — Hook 链表 + dispatcher + setState 环链表 + useEffect 依赖浅对比(JS + TS)
- [x] [React/VirtualDOM-Diff/](./React/VirtualDOM-Diff/) — 双端指针 + key 哈希 + LIS 乱序最小移动(JS + TS)
- [x] [状态管理/Zustand/](./状态管理/Zustand/) — 极简 vanilla store + useStore selector + Object.is 精确订阅 + shallow + 中间件(JS + TS)
- [x] [构建工具/Vite/](./构建工具/Vite/) — ESM 冷启动 + 依赖预构建 + on-demand transform + HMR + import specifier 分类(JS + TS)
- [x] [React/Concurrent/](./React/Concurrent/) — Lane 位掩码模型(31 lane)+ 两级优先级换算 + 时间切片 5ms 预算 + 饥饿防护 + transition 中断丢弃 + Suspense(JS + TS,37 项断言)
- [x] [Vue/编译器优化/](./Vue/编译器优化/) — 静态提升 + PatchFlags 位掩码 + Block Tree / Tree Flattening + 事件缓存 + 静态串压缩(JS + TS,34 项断言)
- [x] [Signals/](./Signals/) — Computed 四状态机 + Watcher 三状态 + glitch-free 拓扑求值 + lossy 语义 + 同步 notify + AggregateError(JS + TS,46 项断言)
- [x] [SSR-Hydration/](./SSR-Hydration/) — shell-first 流式吐字节 + boundary 标记 + 选择性水合队列 + 用户输入抢跑 + mismatch 三态 + 两遍渲染(JS + TS,33 项断言)
- [x] [构建工具/Turbopack/](./构建工具/Turbopack/) — value cell + 读时依赖跟踪 + 内容相等短路 + 需求驱动懒打包 + 聚合图 + 文件系统缓存(JS + TS,36 项断言)

## 待研究

- [ ] Server Components 架构(RSC payload + bundling + Flight 协议)
- [ ] React Compiler(编译期自动记忆化)与 Vue Vapor Mode 的取舍
- [ ] Signals 进入 Web 平台后的框架收敛(Preact / Solid / Svelte runes 的现状)
- [ ] 前端构建的持久化缓存与远程缓存(remote cache / content-addressed store)
