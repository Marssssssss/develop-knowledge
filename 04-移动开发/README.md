# 04 移动开发

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [01-iOS/](./01-iOS/) | Swift / Objective-C / SwiftUI / UIKit(ARC、GCD、RunLoop、async-await+actor、Auto Layout、启动优化、Core Animation 渲染管线) |
| [02-Android/](./02-Android/) | Kotlin / Java / Jetpack Compose(Handler·协程与 Flow·WorkManager、Fragment/Service 生命周期、OkHttp 网络、Compose 重组) |
| [03-跨平台/](./03-跨平台/) | React Native / Flutter / Kotlin Multiplatform / Compose Multiplatform / WebView 容器 |
| [04-推送与消息/](./04-推送与消息/) | APNs / FCM / 厂商通道(HTTP/2 请求与响应语义、aps 载荷字典与本地化、离线排队与 TTL、静默推送与后台唤醒、设备令牌生命周期、FCM v1 平台覆写) |

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
| 282 | `02-Android/网络编程/OkHttp拦截器与连接池/` | OkHttp 拦截器链:应用拦截器(不关心重定向/重试)vs 网络拦截器(在 Connect 之后)分界、7 层内置链顺序与缓存短路、连接池 5 空闲/5 分钟复用、Address→Route→Connection 三级模型、Happy Eyeballs | Python / Kotlin / Java |
| 283 | `02-Android/并发编程/协程上下文与Flow/` | 协程上下文与 Flow:`CoroutineContext` 带键元素集 + 右侧覆盖合并、`Dispatchers.Default` 并行度 = CPU 核数、结构化并发与子失败取消父+兄弟、冷流每次 collect 重跑 vs 热流共享、`flowOn` 只换上游上下文 | Python / Kotlin |
| 284 | `02-Android/并发编程/WorkManager约束与重试/` | WorkManager:约束全部 AND 且默认 false、退避 30s 起步(下限 10s/上限 5h)与 `Result.retry()`、`doWork()` 每次实例一次且限 10 分钟、唯一任务 KEEP/REPLACE/APPEND、`Result.failure()` 阻断下游 | Python / Kotlin / Java |
| 285 | `02-Android/组件生命周期/Fragment生命周期与ViewModel/` | Fragment 状态机与 ViewModel 作用域:`mState` 9 态(含 `AWAITING_EXIT/ENTER_EFFECTS` 过渡态)、进返回栈只 `onDestroyView` 回来重跑 `onCreateView`、`getViewLifecycleOwner()` 视图销毁后抛错、`viewModelScope` 先于 `onCleared()` 取消 | Python / Kotlin |
| 286 | `02-Android/组件生命周期/Service三种形态/` | Service 三形态:started/bound/foreground 并存规则(已启动或有 BIND_AUTO_CREATE 连接即存活)、`onStartCommand` 返回值四档重建策略(`START_STICKY` 可能收到 null intent)、`stopSelf(startId)` 必须按序否则立即停止、`setForeground()` 已是 no-op | Python / Kotlin / Java |
| 337 | `03-跨平台/Flutter/平台通道/` | Flutter 平台通道:StandardMessageCodec 类型字节(0=null … 7=string)+ expanding 长度格式(0..253 单字节 / 254+uint16 / 255+uint32)+ double 8 字节对齐;MethodCodec 应答信封首字节 0 = 成功、**非零即错误**;EventChannel 流式与 BackgroundIsolateBinaryMessenger | Dart / Python |
| 338 | `03-跨平台/Flutter/dart-ffi/` | dart:ffi 的 ABI 相关整数类型:`Long` / `Size` / `UintPtr` 宽度随 ABI 变化、**LLP64**(windowsX64/Arm64)指针 8 字节但 `long` 仍 4 字节、定宽 vs 仅作标记 vs 可实例化三类类型、结构体 stride 按 C ABI 对齐 | Dart / C / Python |
| 339 | `03-跨平台/Kotlin-Multiplatform/内存模型/` | Kotlin/Native 内存模型:共享堆 + 追踪式 GC(CMS,不分代)、旧「冻结」模型在 1.9.20 完全移除、全局属性改为惰性初始化、`AtomicReference` 引用环不再泄漏、stable refs 与 `autoreleasepool` 的 ARC 集成规则 | Kotlin / Python |
| 340 | `03-跨平台/Compose-Multiplatform/重组与SlotTable/` | SlotTable 结构:`groups` 整数数组(每 group 占 `Group_Fields_Size` 个元素)+ `slots` 数组的 **gap buffer**、`dataAnchor` 写模式下是**锚点而非下标**、`groupSize`/`skipToGroupEnd` 跳过原语、四类 group(Restart/Replaceable/Movable/Node)、满表删除的父锚点回归 | Kotlin / Python |
| 341 | `03-跨平台/WebView容器/Tauri-vs-Electron/` | Tauri vs Electron:系统 WebView(5 平台 4 引擎)vs 自带 Chromium、不携带 vs 携带运行时、一次调用 3 段 vs 5 段、Structured Clone 负载限制、capability 声明式权限 vs preload+`contextBridge` 手工裁剪 | TypeScript / JavaScript / Python |
| 397 | `04-推送与消息/APNs请求与响应/` | APNs HTTP/2 请求头语义(apns-id 规范 UUID、expiration 0 与非 0、priority 10·5·1、collapse-id ≤64B)、4096·5120 载荷上限、**每 bundle ID 只存 1 条**的存储语义、10 个状态码 + 32 个 reason、四类重试动作、410 不算 error condition | Python / Swift |
| 398 | `04-推送与消息/载荷与aps字典/` | `aps` 内自定义键被静默忽略(必须是同级)、alert 字符串↔字典、`badge 0` 清除、critical alert 的 sound 字典、interruption-level 四档、relevance-score 0~1、本地化 `*-loc-args` 按 **出现顺序** 替换 `%@` | Python / Swift |
| 399 | `04-推送与消息/静默推送与后台唤醒/` | `content-available=1` + push-type=background + priority=5、三条"持有-延迟"副作用(新的顶掉旧的 / 被杀则丢弃 / 启动即投递)、2~3 条每小时节流、30 秒后台预算 | Python / Swift |
| 400 | `04-推送与消息/设备令牌生命周期/` | token 对「设备+应用」唯一且不可跨 App 复用、每次启动都注册、一用户多设备多 token、APNs 4 个令牌级 reason 与 FCM `UNREGISTERED` 的清理,`PayloadTooLarge`/`TooManyRequests` 不是令牌失效 | Python / Kotlin |
| 401 | `04-推送与消息/FCMv1消息模型/` | 目标 fid/token/topic/condition 恰好一个、**两套 priority**(high·normal 原样 vs min…max→`PRIORITY_*`)、TTL `"3s"`/`"3.500000000s"`、`remove_null_values` 保留 `False` 与 `0`、`content_available` 只认严格 `True` 且写数值 1 | Python / Kotlin |

## 待研究

- [x] iOS 启动时间优化(dyld / ObjC runtime 初始化)→ demo 230
- [x] Core Animation 渲染管线 / Auto Layout 原理 → demo 227、231
- [x] Android OkHttp / Retrofit 原理 → demo 282
- [x] Compose Multiplatform(共享 UI 的 Kotlin 方案)→ demo 340
- [x] Tauri / Electron 架构对比 → demo 341
- [x] 推送通道(APNs 请求响应 / aps 载荷 / 静默推送 / 令牌生命周期 / FCM v1)→ demo 397-401
- [ ] SwiftUI 与 UIKit 桥接(UIHostingController / UIViewRepresentable)
- [ ] iOS Instruments(Leaks / Allocations / Time Profiler / Network)实战
- [ ] React Native Hermes V1 字节码引擎
- [ ] Flutter Engine(Impeller 渲染器内部)
- [ ] Flutter Isolate 并发模型
- [ ] Android Retrofit 动态代理与 CallAdapter / Converter
- [ ] Android HTTP/2 多路复用与流控
- [ ] Tauri 2.x 移动端(Android/iOS)与 JNI / UniFFI 桥
