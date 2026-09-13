# React

## 已完成 demo

- [x] [Fiber/](./Fiber/) — FiberNode + 双缓冲 + render/commit 两阶段(workLoop 自实现,可中断语义;JS + TS)
- [x] [Hooks/](./Hooks/) — Hook 链表 + dispatcher + setState 环链表 + useEffect 依赖浅对比(JS + TS)
- [x] [VirtualDOM-Diff/](./VirtualDOM-Diff/) — 双端指针 + key 哈希 + LIS(Patience Sorting)乱序最小移动(JS + TS)

## 待研究

- [ ] Concurrent Mode 优先级调度(Lane 模型 / Scheduler)
- [ ] Server Components 架构(RSC payload + bundling)
- [ ] Suspense + Offscreen reconciler
- [ ] useTransition / useDeferredValue