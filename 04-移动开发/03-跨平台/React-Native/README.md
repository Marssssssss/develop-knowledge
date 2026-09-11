# React Native

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [Bridge-vs-JSI/](./Bridge-vs-JSI/) | 旧 bridge 异步 JSON 队列 vs 新架构 JSI 同步 C++ 接口 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 030 | `Bridge-vs-JSI/` | Bridge vs JSI + Fabric + TurboModules(New Architecture 核心) | TypeScript / JavaScript |

## 待研究

- [ ] Hermes JS 引擎字节码 + GC
- [ ] Codegen 完整工作流(TS spec → C++/Java/ObjC 胶水)
- [ ] React 18 并发渲染在 RN 中的应用(useTransition / Suspense)
- [ ] Native Modules 性能调优(measure 同步读取 / 动画 worklet)
- [ ] 包兼容性审计工具(newArchEnabled 后哪些 npm 包仍未适配)