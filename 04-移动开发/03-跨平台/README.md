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

## 待研究

- [x] Compose Multiplatform(共享 UI 的 Kotlin 方案)→ demo 340
- [x] Tauri / Electron(桌面 + 移动端 WebView 跨平台)→ demo 341
- [ ] Tauri 2.x 移动端(Android/iOS)支持细节与 JNI/UniFFI 桥
- [ ] Electron 主进程 → 渲染进程消息的序列化开销实测
- [ ] uni-app x(国产跨平台 Vue 方案)
- [ ] SwiftUI for Android / Kotlin Multiplatform Mobile vs Flutter 对比