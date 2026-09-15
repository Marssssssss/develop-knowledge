# Vue

## 已实现
- [响应式实现](./reactive/) — Vue 3 Proxy 响应式最小实现(reactive / ref / effect / track / trigger + 嵌套代理 + cleanup)
- [编译器优化](./编译器优化/) — 静态提升 + PatchFlags 位掩码 + Block Tree / Tree Flattening + 事件缓存 + 静态串压缩(`createStaticVNode` 一次 innerHTML)

## 待研究
- [ ] ref / reactive 区别
- [ ] Composition API 设计哲学
- [ ] Vapor Mode(无虚拟 DOM 编译目标)与编译优化的边界
- [ ] 模板级局部水合(hydration 与编译器标记的配合)
