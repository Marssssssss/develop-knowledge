# iOS

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [内存管理/](./内存管理/) | Swift ARC、AutoreleasePool 双向链表、weak / SideTable 实现 |
| [并发编程/](./并发编程/) | GCD DispatchQueue / DispatchSemaphore / DispatchGroup / barrier |
| [事件循环/](./事件循环/) | RunLoop modes / sources / timers / observers |
| [运行时/](./运行时/) | objc_msgSend 消息机制、KVO isa-swizzling、Category 与关联对象 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 024 | `内存管理/ARC/` | Swift / Objective-C ARC 内存管理(strong / weak / unowned + 闭包捕获列表,WWDC 2021 #10216 + Swift Book) | Swift / Objective-C |
| 025 | `并发编程/GCD同步原语/` | GCD DispatchQueue / DispatchSemaphore / DispatchGroup / barrier(Apple Dispatch 文档 + GCD Reference) | Swift / Objective-C |
| 026 | `事件循环/RunLoop/` | RunLoop modes / sources / timers / observers(Apple Threading Programming Guide + CFRunLoop 官方文档) | Swift / Objective-C |
| 132 | `内存管理/AutoreleasePool/` | AutoreleasePoolPage 双向链表 + 哨兵嵌套池 + 分页(objc4 NSObject.mm) | Swift / Objective-C |
| 133 | `内存管理/Weak实现/` | SideTables / weak_table / weak_clear_no_lock 自动置 nil(objc4 objc-weak 体系) | Swift / Objective-C |
| 134 | `运行时/消息机制/` | objc_msgSend 缓存哈希 + 继承链慢查 + 三段转发(objc4 objc-msg-arm64.s + objc-runtime-new.mm) | Swift / Objective-C |
| 135 | `运行时/KVO/` | KVO isa-swizzling:派生 NSKVONotifying_ + setter 重写 + -class 伪装 | Swift / Objective-C |
| 136 | `运行时/Category与关联对象/` | attachCategories 前插合并 + 关联对象两层哈希(objc-references.mm) | Swift / Objective-C |

## 待研究

- [ ] UIKit vs SwiftUI 生命周期(viewDidLoad vs .onAppear 等)
- [ ] Swift 5.9+ 新特性(宏、参数包、if/switch 表达式)
- [ ] Swift async/await + Task 与 RunLoop / GCD 协同
- [ ] Auto Layout 原理(约束求解 Cassowary)
- [ ] Core Animation 渲染管线
- [ ] NotificationCenter 与 KVO 实现差异
- [ ] @State / @Binding / @Observable 等 SwiftUI 状态管理
- [ ] Combine 框架(Publisher / Subscriber / Operator)