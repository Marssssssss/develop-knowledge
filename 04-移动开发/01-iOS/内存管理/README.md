# iOS · 内存管理

研究 iOS / macOS 平台下的 Swift 与 Objective-C 内存管理机制,核心是 **ARC(Automatic Reference Counting)** 及其弱/无主引用变体。

## 子领域

| 子目录 | 知识点 |
| --- | --- |
| [ARC/](./ARC/) | Swift / Objective-C ARC(strong / weak / unowned + 闭包捕获列表) |

## 待研究

- [ ] 值类型(struct / enum)与引用类型的 ARC 边界
- [ ] ARC 优化(use-based lifetime vs observed lifetime,WWDC 2021 #10216)
- [ ] @autoreleasepool 在 Swift 5+ 的使用
- [ ] 桥接与 unowned(unsafe) 的悬挂指针风险
- [ ] Memory Graph Debugger 实战排错