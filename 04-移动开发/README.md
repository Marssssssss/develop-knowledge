# 04 移动开发

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-iOS/](./01-iOS/) | Swift / Objective-C / SwiftUI / UIKit(ARC、GCD、RunLoop、SwiftUI) |
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

## 待研究

- [ ] iOS 启动时间优化(dyld / ObjC runtime 初始化)
- [ ] Android OkHttp / Retrofit 原理
- [ ] Flutter Skia 渲染管线
- [ ] React Native 新架构(Fabric / TurboModules)
- [ ] SwiftUI 与 UIKit 桥接(UIHostingController / UIViewRepresentable)
- [ ] iOS Instruments(Leaks / Allocations / Time Profiler / Network)实战