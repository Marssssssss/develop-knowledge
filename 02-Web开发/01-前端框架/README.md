# 前端框架

## 子领域

- [React/](./React/) — Fiber 架构、Hooks 链表、虚拟 DOM Diff、Concurrent 并发渲染
- [Vue/](./Vue/) — 响应式原理、编译器优化(静态提升 / PatchFlags / Block Tree)
- [Signals/](./Signals/) — TC39 Signals 提案(Stage 1):push-pull 细粒度响应式
- [SSR-Hydration/](./SSR-Hydration/) — 流式 SSR(renderToPipeableStream)+ 选择性水合
- [构建工具/](./构建工具/) — Vite / Webpack / Turbopack / esbuild / Rolldown
- [状态管理/](./状态管理/) — Zustand / Redux / Pinia / Jotai / Recoil
- [Svelte/](./Svelte/) — runes 编译式细粒度响应性($state / $derived / $effect)
- [WebComponents/](./WebComponents/) — Custom Elements 升级机制与 reaction 队列
- [浏览器渲染管线/](./浏览器渲染管线/) — 样式 / 布局 / 绘制 / 合成与强制同步布局
- [模块系统/](./模块系统/) — ESM 两阶段求值与实时绑定

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
- [x] [Svelte/编译式响应性/](./Svelte/编译式响应性/) — runes 三层订阅(Source/Derived/Effect)+ pull 式 derived + 相等短路 + teardown 时机 + 深代理不 mutate 原对象(Python + Go,29 项断言)
- [x] [React/RSC与Flight协议/](./React/RSC与Flight协议/) — Flight 行状态机(ROW_ID/TAG/LENGTH)+ 20 余种 `$` 引用前缀 + 流式 shell 与 `$L` 回填 + 字节长度重组(Python + Go,31 项断言)
- [x] [浏览器渲染管线/](./浏览器渲染管线/) — 改动分类(重排/重绘/只合成)+ 层提升与层归属 + 强制同步布局计数 + 图片尺寸迟到引发 reflow(Python + Go,26 项断言)
- [x] [WebComponents/](./WebComponents/) — 五种 element state + 升级幂等 + 构造期不可见 attributes/children + reaction 队列三后果 + connectedMoveCallback(Python + Go,27 项断言)
- [x] [模块系统/ESM实时绑定/](./模块系统/ESM实时绑定/) — Link/Evaluate 两阶段 + 函数提升与 TDZ + live binding 视图不可写 + 循环依赖 + 错误记录范围(Python + Go,28 项断言)

## 待研究

- [x] Server Components 架构(RSC payload + bundling + Flight 协议)—— 已由 `React/RSC与Flight协议/` 覆盖行协议与流式回填
- [x] Svelte runes 与 Signals 的关系 —— 已由 `Svelte/编译式响应性/` 覆盖 pull/push 差别
- [ ] React Compiler(编译期自动记忆化)与 Vue Vapor Mode 的取舍
- [ ] 前端构建的持久化缓存与远程缓存(remote cache / content-addressed store)
- [ ] 水合粒度演进:Resumability(Qwik)与 Island 架构(Astro)
- [ ] View Transitions API 与跨文档导航的渲染管线影响
- [ ] WebGPU 在前端渲染管线中的位置与合成器交互
