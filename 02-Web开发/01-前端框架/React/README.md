# React

## 已完成 demo

- [x] [Fiber/](./Fiber/) — FiberNode + 双缓冲 + render/commit 两阶段(workLoop 自实现,可中断语义;JS + TS)
- [x] [Hooks/](./Hooks/) — Hook 链表 + dispatcher + setState 环链表 + useEffect 依赖浅对比(JS + TS)
- [x] [VirtualDOM-Diff/](./VirtualDOM-Diff/) — 双端指针 + key 哈希 + LIS(Patience Sorting)乱序最小移动(JS + TS)
- [x] [Concurrent/](./Concurrent/) — Lane 位掩码(31 lane)+ `lanes & -lanes` 取最高优先级 + 两级优先级换算 + 5ms 时间切片 + 饥饿防护(过期 lane 强制同步)+ transition 中断丢弃 + Suspense 占位(JS + TS)
- 相关跨框架主题:[SSR-Hydration/](../SSR-Hydration/) — 流式 SSR + 选择性水合(React 18 的两个官方运行时)

## 待研究

- [ ] Server Components 架构(RSC payload + bundling)
- [ ] React Compiler 的编译期自动记忆化
- [ ] Offscreen / Activity 组件与预渲染
