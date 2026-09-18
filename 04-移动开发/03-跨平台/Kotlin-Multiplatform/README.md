# Kotlin Multiplatform

Kotlin 的跨平台方案:**共享逻辑**由 common source set 编译为各平台产物,平台差异通过
`expect` / `actual` 声明合并;UI 仍走各平台原生(要共享 UI 则用
[Compose-Multiplatform](../Compose-Multiplatform/))。运行时层面 Kotlin/Native 自带一套
内存模型,与 JVM 端差异显著。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [expect-actual/](./expect-actual/) | expect/actual 声明合并 + intermediate source set |
| [内存模型/](./内存模型/) | Kotlin/Native 共享堆与追踪式 GC、旧「冻结」模型的移除、与 ARC 的互操作 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 032 | `expect-actual/` | KMP expect/actual 声明合并机制(函数 + 属性 + 类 typealias + 枚举 + intermediate source set) | Kotlin |
| 339 | `内存模型/` | 共享堆 + 追踪式 GC(CMS,不分代)、旧「冻结」模型在 1.9.20 完全移除、全局属性惰性初始化、`AtomicReference` 引用环不再泄漏、stable refs 与 `autoreleasepool` | Kotlin / Python |

## 待研究

- [ ] Kotlin/Native 编译器后端(LLVM + cinterop 生成流程)
- [ ] KMP 的 `Hierarchical Multiplatform Project Structure` 与依赖传播
- [ ] Compose Multiplatform 与 KMP 的边界(哪些抽象可共享)
- [ ] KMP 在 iOS 侧的产物形态(静态 framework / XCFramework 打包)
