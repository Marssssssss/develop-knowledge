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

## 待研究

- [ ] iOS 启动时间优化(dyld / ObjC runtime 初始化)
- [ ] Android JNI / NDK
- [ ] Flutter Skia 渲染管线
- [ ] React Native 新架构(Fabric / TurboModules)
- [ ] SwiftUI 与 UIKit 桥接(UIHostingController / UIViewRepresentable)
- [ ] Android Compose 重组(Recomposition)机制
- [ ] iOS Instruments(Leaks / Allocations / Time Profiler / Network)实战