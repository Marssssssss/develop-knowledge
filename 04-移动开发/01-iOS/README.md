# iOS

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [内存管理/](./内存管理/) | Swift ARC、weak / unowned、闭包捕获列表 |
| [并发编程/](./并发编程/) | GCD DispatchQueue / DispatchSemaphore / DispatchGroup / barrier |
| [事件循环/](./事件循环/) | RunLoop modes / sources / timers / observers |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 024 | `内存管理/ARC/` | Swift / Objective-C ARC 内存管理(strong / weak / unowned + 闭包捕获列表,WWDC 2021 #10216 + Swift Book) | Swift / Objective-C |
| 025 | `并发编程/GCD同步原语/` | GCD DispatchQueue / DispatchSemaphore / DispatchGroup / barrier(Apple Dispatch 文档 + GCD Reference) | Swift / Objective-C |
| 026 | `事件循环/RunLoop/` | RunLoop modes / sources / timers / observers(Apple Threading Programming Guide + CFRunLoop 官方文档) | Swift / Objective-C |

## 待研究

- [ ] UIKit vs SwiftUI 生命周期(viewDidLoad vs .onAppear 等)
- [ ] Swift 5.9+ 新特性(宏、参数包、if/switch 表达式)
- [ ] Swift async/await + Task 与 RunLoop / GCD 协同
- [ ] Auto Layout 原理(约束求解 Cassowary)
- [ ] Core Animation 渲染管线
- [ ] NotificationCenter 与 KVO 实现差异
- [ ] @State / @Binding / @Observable 等 SwiftUI 状态管理
- [ ] Combine 框架(Publisher / Subscriber / Operator)