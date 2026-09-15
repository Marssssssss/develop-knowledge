# 04 移动开发

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-iOS/](./01-iOS/) | Swift / Objective-C / SwiftUI / UIKit(ARC、GCD、RunLoop、async-await+actor、Auto Layout、启动优化、Core Animation 渲染管线) |
| [02-Android/](./02-Android/) | Kotlin / Java / Jetpack Compose |
| [03-跨平台/](./03-跨平台/) | React Native / Flutter / Kotlin Multiplatform |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 024 | `01-iOS/内存管理/ARC/` | Swift / Objective-C ARC 内存管理(strong / weak / unowned + 闭包捕获列表) | Swift / Objective-C |
| 025 | `01-iOS/并发编程/GCD同步原语/` | GCD DispatchQueue / DispatchSemaphore / DispatchGroup / barrier | Swift / Objective-C |
| 026 | `01-iOS/事件循环/RunLoop/` | RunLoop modes / sources / timers / observers(6 个 Activity) | Swift / Objective-C |
| 027 | `02-Android/并发编程/Handler消息机制/` | Android Handler / Looper / MessageQueue(post / sendMessage / HandlerThread / postDelayed / native epoll 阻塞唤醒 + 内存泄漏修复) | Kotlin / Java |
| 028 | `02-Android/组件生命周期/Activity启动模式/` | Activity launchMode 5 种(standard / singleTop / singleTask / singleInstance / singleInstancePerTask)+ Intent flags 运行时覆盖 | Kotlin / Java |
| 029 | `02-Android/UI框架/Compose重组/` | Jetpack Compose 重组机制(mutableStateOf / remember / derivedStateOf / lambda modifier / Backwards write 反模式) | Kotlin |
| 030 | `03-跨平台/React-Native/Bridge-vs-JSI/` | RN New Architecture(Bridge 异步 JSON 队列 → JSI 同步 C++ 接口 + Fabric 渲染管线 + TurboModules 懒加载 + Codegen 类型安全) | TypeScript / JavaScript |
| 031 | `03-跨平台/Flutter/三棵树/` | Widget 不可变配置 + Element 持久化 + RenderObject 布局绘制 + Box Constraint 模型 + build→layout→paint→composite 管线 | Dart |
| 032 | `03-跨平台/Kotlin-Multiplatform/expect-actual/` | KMP expect/actual 声明合并机制(函数 + 属性 + 类 typealias + 枚举 + intermediate source set) | Kotlin |
| 227 | `01-iOS/UI框架/AutoLayout约束求解/` | Auto Layout 约束求解:线性等式 + 优先级 → Cassowary(required 1000 / CHCR 250·750 / 强度阶梯 / 表格单纯形) | Python / Swift / Objective-C |
| 228 | `01-iOS/UI框架/SwiftUI状态管理/` | SwiftUI 状态管理:`@State` 单一数据源 + `@Observable` 宏(ObservationRegistrar)+ 依赖图粗细粒度追踪 | Python / Swift / Objective-C |
| 229 | `01-iOS/并发编程/Swift-Concurrency/` | Swift 并发:await 串行 vs `async let` 并行、actor = exclusive executor、可重入导致 check/await/act 失效、结构化并发、协作式取消 | Python / Swift / Objective-C |
| 230 | `01-iOS/启动优化/dyld-pre-main/` | dyld pre-main 四阶段 + `+load` 顺序与次数 + Swift 全局变量懒初始化(与 ObjC +load 的对比) | Python / Swift / Objective-C |
| 231 | `01-iOS/渲染/CoreAnimation管线/` | Core Animation 渲染循环:Commit(Layout/Display/Prepare)与 render server 分工、脏标记合并、离屏渲染、`shouldRasterize` 盈亏平衡、混合 overdraw | Python / Swift / Objective-C |

## 待研究

- [x] iOS 启动时间优化(dyld / ObjC runtime 初始化)→ demo 230
- [x] Core Animation 渲染管线 / Auto Layout 原理 → demo 227、231
- [ ] Android OkHttp / Retrofit 原理
- [ ] Compose Multiplatform(共享 UI 的 Kotlin 方案)
- [ ] SwiftUI 与 UIKit 桥接(UIHostingController / UIViewRepresentable)
- [ ] iOS Instruments(Leaks / Allocations / Time Profiler / Network)实战
- [ ] React Native Hermes V1 字节码引擎
- [ ] Flutter Engine(Impeller 渲染器内部)
- [ ] Flutter Isolate 并发模型
