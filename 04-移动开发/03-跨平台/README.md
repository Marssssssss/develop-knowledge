# 跨平台

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [React-Native/](./React-Native/) | React Native(JS 业务 + 平台 native UI) |
| [Flutter/](./Flutter/) | Flutter(Dart 自绘 UI,跨平台一致) |
| [Kotlin-Multiplatform/](./Kotlin-Multiplatform/) | Kotlin Multiplatform(common 共享 + expect/actual 平台差异) |
| [Compose-Multiplatform/](./Compose-Multiplatform/) | Compose Multiplatform(共享 UI 的 Kotlin 方案:SlotTable 与控制流 group) |
| [WebView容器/](./WebView容器/) | WebView 容器方案(Tauri / Electron 等「壳 + Web 前端」架构对比) |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 030 | `React-Native/Bridge-vs-JSI/` | RN New Architecture(Bridge 异步 JSON 队列 → JSI 同步 C++ 接口 + Fabric 渲染管线 + TurboModules 懒加载 + Codegen 类型安全) | TypeScript / JavaScript |
| 031 | `Flutter/三棵树/` | Widget 不可变配置 + Element 持久化 + RenderObject 布局绘制 + Box Constraint 模型 + build→layout→paint→composite 管线 | Dart |
| 032 | `Kotlin-Multiplatform/expect-actual/` | KMP expect/actual 声明合并机制(函数 + 属性 + 类 typealias + 枚举 + intermediate source set) | Kotlin |
| 337 | `Flutter/平台通道/` | Flutter 平台通道:StandardMessageCodec 二进制格式(类型字节 + expanding 长度 + 8 字节对齐)+ MethodCodec 应答信封(首字节 0 = 成功)+ EventChannel 流式 + BackgroundIsolateBinaryMessenger | Dart / Python |
| 338 | `Flutter/dart-ffi/` | dart:ffi 的 ABI 相关整数类型:`Long` / `Size` / `UintPtr` 宽度随 ABI 变化、LLP64 上 `long` 仍 4 字节、结构体布局按 C ABI 对齐 | Dart / C / Python |
| 339 | `Kotlin-Multiplatform/内存模型/` | Kotlin/Native 内存模型:共享堆 + 追踪式 GC(不分代)、旧「冻结」模型完全移除、全局属性惰性初始化、`AtomicReference` 环不再泄漏、与 Swift/ObjC ARC 集成 | Kotlin / Python |
| 340 | `Compose-Multiplatform/重组与SlotTable/` | SlotTable 结构:`groups` 整数数组 + `slots` gap buffer、`dataAnchor` 写模式下是锚点而非下标、`groupSize`/`skipToGroupEnd` 跳过原语、四类 group | Kotlin / Python |
| 341 | `WebView容器/Tauri-vs-Electron/` | Tauri vs Electron 架构:系统 WebView(5 平台 4 引擎)vs 自带 Chromium、不携带运行时 vs 携带、一次调用 3 段 vs 5 段、capability 声明式权限 vs preload 手工裁剪 | TypeScript / JavaScript / Python |
| 561 | `React-Native/Yoga布局算法/` | Yoga 布局引擎:`roundValueToPixelGrid`(0.5 一律进位、宽度 = 两条绝对边各自取整再相减、文本节点只向上取整避免截字)、`canUseCachedMeasurement` 四条规则、`calculateFlexLine` 的 gap 与因子抬 1(shrink 合计恒为负故永不抬起)、两遍弹性分配与 errata 默认值 24 | Python(60 断言实跑) / Go(人工审查 + 三项静态检查) |
| 562 | `Flutter/事件循环与Timer/` | Dart 事件循环:微任务是**单链表**且排空前不让出事件循环、优先级回调插到「上一个优先级项」之后、Timer 二叉堆(初值 7 / 扩容 2n+1 / 同时刻按 `_id` FIFO)、零延迟计时器独立 FIFO 且一条消息一个、周期计时器 `missedTicks` 补偿 | Python(35 断言实跑) / Go(人工审查 + 三项静态检查) |
| 563 | `React-Native/Hermes值与字节码/` | Hermes NaN-boxing:高 16 位标签(区间 0xfff9..0xffff)、7 个 Tag + 14 个 ETag(bit47 被占,数据位实为 47 位)、指针上限 48 位、负 NaN 也是 NaN;HBC 魔数 `0x1F1903C103BC1FC6` = 古希腊语 Ἑρμῆ、文件头 128 字节且 32 对齐 | Python(71 断言实跑) / Go(人工审查 + 三项静态检查) |
| 564 | `Compose-Multiplatform/快照系统/` | Compose 快照:`SnapshotIdSet`(双 Long 窗口 + 下界有序数组、不可变且无变化返回同一实例)、`valid()`/`readable()` 可见性、`innerApplyLocked` 的 previous/current/applied 三记录判据、默认 `mergeRecords` 返回 null 导致任何碰撞都失败 | Python(56 断言实跑) / Go(人工审查 + 三项静态检查) |
| 565 | `WebView容器/IPC与能力ACL/` | Tauri 2 权限:命令键归一化、`resolve_access` 的 deny 优先且只看 origin、origin 与 (window|webview) 是与或组合、远程 URL 走 WHATWG urlpattern(`*.tauri.app` 不匹配裸域名) | Python(40 断言实跑) / Go(人工审查 + 三项静态检查) |

## 2026-09-22 新增来源

- `facebook/yoga@main`:`yoga/algorithm/{CalculateLayout,PixelGrid,Cache,FlexLine}.cpp`、`SizingMode.h`、`numeric/Comparison.h`、`enums/Errata.h`、`YGEnums.h`、`config/Config.h`、`website/docs/styling/flex-basis-grow-shrink.mdx`
- `dart-lang/sdk@main`:`sdk/lib/async/schedule_microtask.dart`、`sdk/lib/_internal/vm/lib/{timer_impl,isolate_patch}.dart`、`sdk/lib/isolate/isolate.dart`
- `facebook/hermes@main`:`include/hermes/VM/HermesValue.h`、`include/hermes/BCGen/HBC/BytecodeFileFormat.h`、`include/hermes/Support/SHA1.h`
- `JetBrains/compose-multiplatform-core@master`:`compose/runtime/runtime/.../snapshots/{Snapshot,SnapshotIdSet}.kt`
- `tauri-apps/tauri@dev`:`crates/tauri/src/ipc/{authority,protocol,command,channel}.rs`、`crates/tauri-utils/src/acl/{resolved,capability,identifier,mod}.rs`

## 待研究

- [x] Compose Multiplatform(共享 UI 的 Kotlin 方案)→ demo 340
- [x] Tauri / Electron(桌面 + 移动端 WebView 跨平台)→ demo 341
- [x] Yoga 布局引擎的取整与两遍分配 → demo 561
- [x] Dart 事件循环与 Timer / 微任务 → demo 562
- [x] Hermes 值表示与 HBC 文件格式 → demo 563
- [x] Compose 快照系统 → demo 564
- [x] Tauri 2 的 IPC 与 capability ACL → demo 565
- [ ] Tauri 2.x 移动端(Android/iOS)支持细节与 JNI/UniFFI 桥
- [ ] Electron 主进程 → 渲染进程消息的序列化开销实测
- [ ] uni-app x(国产跨平台 Vue 方案)
- [ ] SwiftUI for Android / Kotlin Multiplatform Mobile vs Flutter 对比
- [ ] React Native Fabric 的 Shadow Tree 与挂载阶段
- [ ] Flutter Impeller 渲染器内部(相比 Skia 的管线差异)