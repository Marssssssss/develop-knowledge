# 跨平台

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [React-Native/](./React-Native/) | React Native(JS 业务 + 平台 native UI) |
| [Flutter/](./Flutter/) | Flutter(Dart 自绘 UI,跨平台一致) |
| [Kotlin-Multiplatform/](./Kotlin-Multiplatform/) | Kotlin Multiplatform(common 共享 + expect/actual 平台差异) |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 030 | `React-Native/Bridge-vs-JSI/` | RN New Architecture(Bridge 异步 JSON 队列 → JSI 同步 C++ 接口 + Fabric 渲染管线 + TurboModules 懒加载 + Codegen 类型安全) | TypeScript / JavaScript |
| 031 | `Flutter/三棵树/` | Widget 不可变配置 + Element 持久化 + RenderObject 布局绘制 + Box Constraint 模型 + build→layout→paint→composite 管线 | Dart |
| 032 | `Kotlin-Multiplatform/expect-actual/` | KMP expect/actual 声明合并机制(函数 + 属性 + 类 typealias + 枚举 + intermediate source set) | Kotlin |

## 待研究

- [ ] Compose Multiplatform(共享 UI 的 Kotlin 方案)
- [ ] Tauri / Electron(桌面 + 移动端 WebView 跨平台)
- [ ] uni-app x(国产跨平台 Vue 方案)
- [ ] SwiftUI for Android / Kotlin Multiplatform Mobile vs Flutter 对比