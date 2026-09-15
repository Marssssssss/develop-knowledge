# iOS

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [内存管理/](./内存管理/) | Swift ARC、AutoreleasePool 双向链表、weak / SideTable 实现 |
| [并发编程/](./并发编程/) | GCD DispatchQueue / DispatchSemaphore / DispatchGroup / barrier;Swift async/await + actor |
| [事件循环/](./事件循环/) | RunLoop modes / sources / timers / observers |
| [运行时/](./运行时/) | objc_msgSend 消息机制、KVO isa-swizzling、Category 与关联对象 |
| [UI框架/](./UI框架/) | Auto Layout(Cassowary 约束求解)、SwiftUI 状态管理与 Observation |
| [启动优化/](./启动优化/) | dyld pre-main 四阶段、+load / constructor 的代价、二进制重排与缺页 |
| [渲染/](./渲染/) | Core Animation 渲染管线(Commit / render server / GPU)、离屏与混合 |

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
| 227 | `UI框架/AutoLayout约束求解/` | Auto Layout 约束求解:线性等式 + 优先级 → Cassowary(required 1000 / CHCR 250·750 / 强度阶梯 / 表格单纯形 + Bland 规则) | Python / Swift / Objective-C |
| 228 | `UI框架/SwiftUI状态管理/` | SwiftUI 状态管理:`@State` 单一数据源 + `@Observable` 宏(ObservationRegistrar / access / mutation)+ 依赖图的粗粒度 vs 细粒度追踪 | Python / Swift / Objective-C |
| 229 | `并发编程/Swift-Concurrency/` | Swift 并发:await 串行 vs `async let` 并行、actor = exclusive executor、**可重入**导致 check/await/act 失效、结构化并发、协作式取消(不检查就不停) | Python / Swift / Objective-C |
| 230 | `启动优化/dyld-pre-main/` | dyld pre-main 四阶段(Load dylibs / Rebase-Bind / ObjC setup / Initializers)+ `+load` 顺序与次数 + Swift 全局变量懒初始化 | Python / Swift / Objective-C |
| 231 | `渲染/CoreAnimation管线/` | Core Animation 渲染循环:Commit(Layout/Display/Prepare)与 render server 的分工、脏标记合并、离屏渲染、`shouldRasterize` 盈亏平衡、混合与 overdraw | Python / Swift / Objective-C |

## 待研究

- [ ] UIKit vs SwiftUI 生命周期(viewDidLoad vs .onAppear 等)
- [ ] Swift 5.9+ 新特性(宏、参数包、if/switch 表达式)
- [ ] Swift 6 严格并发检查(Sendable / 区域隔离 / 数据竞争诊断)
- [ ] NotificationCenter 与 KVO 实现差异
- [ ] Combine 框架(Publisher / Subscriber / Operator)
- [ ] SwiftUI 布局系统(布局协议 Layout、ViewThatFits、尺寸协商)
- [ ] 图片解码与 ImageIO(下采样、渐进式解码、HEIF)
- [ ] Instruments 实战(Leaks / Allocations / Time Profiler / Core Animation / GPU Driver)
- [x] Auto Layout 原理(约束求解 Cassowary)→ demo 227
- [x] @State / @Binding / @Observable 等 SwiftUI 状态管理 → demo 228
- [x] Swift async/await + Task 与 RunLoop / GCD 协同 → demo 229
- [x] iOS 启动时间优化(dyld / ObjC runtime 初始化)→ demo 230
- [x] Core Animation 渲染管线 → demo 231
