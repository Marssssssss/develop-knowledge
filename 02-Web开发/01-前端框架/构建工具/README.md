# 构建工具

## 已完成 demo

- [x] [Vite/](./Vite/) — ESM 冷启动原理 + 依赖预构建 + on-demand transform + HMR 流程 + import specifier 分类(JS + TS)
- [x] [Turbopack/](./Turbopack/) — turbo-tasks 增量计算引擎:value cell(Vc)+ 函数级记忆化 + 读时依赖跟踪 + 内容相等短路 + 需求驱动懒打包 + 聚合图 + 文件系统缓存(JS + TS)

## 待研究

- [ ] Webpack 5 Module Federation 联邦模块
- [ ] esbuild / SWC 性能对比 + esbuild plugin API
- [ ] Rolldown Migration + Oxc 集成(Vite 8+ 主线的 Rust 工具链)
- [ ] 远程缓存 / content-addressed store 与 CI 上的增量构建
