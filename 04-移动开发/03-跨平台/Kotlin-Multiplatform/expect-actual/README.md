# Kotlin Multiplatform expect/actual — 跨平台声明合并机制

## 简介

Kotlin Multiplatform (KMP) 用 **`expect`/`actual` 关键字** 让 common 代码声明"我需要这个 API",由各平台 source set 提供实现,编译器在编译期把对应实现合并进去。这是 KMP 区别于"common 接口 + 手动 DI"的关键语言机制。

**关键概念清单**:
- **expect declaration**: common 中只声明签名,无函数体
- **actual declaration**: 平台 source set 提供具体实现
- **source set 层级**: `commonMain` → `iosMain` (intermediate) → `iosX64Main` / `iosArm64Main` / `iosSimulatorArm64Main` (platform)
- **合并时机**: 编译器在生成目标平台代码时,把 expect + 匹配 actual 合并为单个带实现的声明

**历史背景**: 自 Kotlin 1.3 (2018) 引入;1.4 进入 Beta;1.5+ Stable。

## 原理详解

### 1. expect/actual 的 4 条铁律

> 权威资料 kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html:
> "The compiler ensures that every declaration marked with the expect keyword in the common source set has the corresponding declarations marked with the actual keyword in all targeted platform source sets."

1. **`expect` 关键字** 标记 common 声明, **`actual` 关键字** 标记平台实现
2. **同名 + 同包**: 完全限定名必须一致,否则编译器不识别为匹配
3. **`expect` 不能有函数体/初始化器**: 函数 `expect fun randomUUID(): String` 没有 `{}`,属性 `expect val num: Int` 不能 `= 42`
4. **编译器合并**: 编译每个目标平台时,匹配 expect + actual 成一个带平台实现的声明

### 2. 适用声明类型

expect/actual 可用于大多数 Kotlin 声明:

| 类别 | 是否支持 | 说明 |
| --- | --- | --- |
| 函数 | ✓ | `expect fun platform(): Platform` |
| 属性 | ✓ | `expect val platformTag: Int` |
| 类 | ✓ | `expect class AtomicRef<T>(value: T)` |
| 接口 | ✓ | `expect interface Platform` |
| 枚举 | ✓ | `expect enum class LogLevel { ... }` |
| object | ✓ | `expect object Clock` |
| 注解 | ✓ | `expect annotation class Test` |

### 3. 三种"平台 API 注入"路径

> 权威资料 kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html:
> "To inject the appropriate platform implementations when you need a common interface, you can choose one of the following options"

| 选项 | 适用 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **expect/actual 函数** | 简单平台 API | 语言级支持,IDE 跳转 | 不适合复杂逻辑 |
| **不同入口点** | 控制应用入口 | 无 expect 声明开销 | 调用方负责构造 |
| **DI 框架** (Koin) | 大型应用 | 解耦,可测试 | 引入框架依赖 |

本 demo 用第一种(最简单直接)。

### 4. Source Set 层级(intermediate source sets)

```
commonMain
    │
    ├── androidMain       (Android 平台)
    │
    └── iosMain           (intermediate,iOS 系列共享)
            │
            ├── iosX64Main             (Intel Mac iOS 模拟器)
            ├── iosArm64Main           (真机 ARM64)
            └── iosSimulatorArm64Main  (Apple Silicon 模拟器)
```

`iosMain` 是 **intermediate source set**——它被所有具体 iOS 平台共享,放 iOS 通用代码;具体平台(`iosArm64Main` 等)只放该平台特有的代码(例如 ABI 特定的内联函数)。通常 `actual` 只放在 intermediate,平台 source set 不放。

### 5. typealias actual

如果某个平台已有完全等价的类型,可以用 `typealias` 作为 actual,避免写 wrapper:

```kotlin
// common
expect class AtomicRef<T>(value: T) {
    fun get(): T
    fun set(value: T)
}

// android (JVM 已有 java.util.concurrent.atomic.AtomicReference)
actual typealias AtomicRef<T> = java.util.concurrent.atomic.AtomicReference<T>

// iOS (Kotlin/Native 无内置 atomic) — 写完整实现或用 kotlinx.atomicfu
actual class AtomicRef<T>(initialValue: T) {
    // ... synchronized lock 实现
}
```

## 对比 / 选型

| 跨平台方案 | 共享度 | 类型安全 | 运行时开销 | 学习曲线 |
| --- | --- | --- | --- | --- |
| **KMP expect/actual** | 共享业务逻辑 + 部分 UI | 编译期 | 零(编译期合并) | 低(语言原生) |
| **KMP + Compose Multiplatform** | 共享 UI(Android/iOS/Desktop/Web) | 编译期 | 零 | 中(需学 Compose) |
| **Flutter** | 100% UI + 业务 | 编译期 | Dart VM | 中(Dart 语言) |
| **React Native** | JS 业务 + 平台 native UI | 部分 Codegen | Bridge/JSI 调用 | 中(JS + native) |
| **平台原生开发** | 无共享 | 编译期 | 零 | 高(两套代码) |

**何时用 expect/actual**: 平台 API 必须用(UIDevice/Build.VERSION)且不想引入 DI 框架时。

**何时不用**: 业务逻辑可放在 common 实现,平台只是参数时(如数据库驱动) — 用 interface + factory 更灵活。

## 环境准备

- **Kotlin Multiplugin**: IntelliJ IDEA / Android Studio 装 KMP 插件
- **build.gradle.kts**: 配置 targets (android / iosX64 / iosArm64 / iosSimulatorArm64) + sourceSets
- **Xcode**: 跑 iOS 端需要 Xcode(消费 KMP 生成的 framework)

本 demo 文件按 source set 分目录,展示 KMP 项目结构。**不实际编译**(需 KMP toolchain)。

## 运行方式

实际运行需要 KMP 项目,这里给出项目骨架:

```kotlin
// build.gradle.kts (项目根)
plugins {
    kotlin("multiplatform") version "2.0.0"
}
kotlin {
    androidTarget()
    iosX64(); iosArm64(); iosSimulatorArm64()
    sourceSets {
        val commonMain by getting
        val androidMain by getting
        val iosMain by creating { dependsOn(commonMain) }
        iosX64Main.dependsOn(iosMain)
        iosArm64Main.dependsOn(iosMain)
        iosSimulatorArm64Main.dependsOn(iosMain)
    }
}
```

```bash
# Android 端
./gradlew :androidApp:installDebug

# iOS 端(在 iosApp 目录)
xcodebuild -workspace iosApp.xcworkspace -scheme iosApp
```

本目录下的源文件可直接复制到 KMP 项目的对应 source set。

## 关键代码片段

**common 中的 expect**(`commonMain/Platform.kt`):

```kotlin
expect fun randomUUID(): String             // Android = UUID.randomUUID(), iOS = NSUUID().UUIDString()
expect val platformTag: Int                 // Android = 1, iOS = 2
expect class AtomicRef<T>(value: T) {       // Android typealias, iOS full impl
    fun get(): T
    fun compareAndSet(expect: T, update: T): Boolean
}
```

**android 端 actual**(`androidMain/Platform.android.kt`):

```kotlin
actual fun randomUUID(): String = java.util.UUID.randomUUID().toString()
actual val platformTag: Int = 1
actual typealias AtomicRef<T> = java.util.concurrent.atomic.AtomicReference<T>
```

**iOS 端 actual**(`iosMain/Platform.ios.kt`):

```kotlin
actual fun randomUUID(): String = platform.Foundation.NSUUID().UUIDString()
actual val platformTag: Int = 2
actual class AtomicRef<T>(initialValue: T) {
    // Kotlin/Native 无内置 atomic → 用 synchronized 模拟
    // 生产推荐用 kotlinx.atomicfu 的 atomicfu.AtomicReference
}
```

**调用方**:`Main.kt` 不感知平台差异,直接 `randomUUID()` / `platformTag` / `AtomicRef(0)`。

## 性能与边界

| 维度 | 说明 |
| --- | --- |
| 编译期合并 | 零运行时开销(不像 DI 框架需要运行时查表) |
| 类型安全 | 编译期报错:expect 缺 actual / 签名不匹配 / 包名不一致 |
| 支持平台 | Android / iOS / JVM / JS / Native (macOS/Linux/Windows) / WebAssembly |
| 与 Kotlin/Native 互操作 | iOS 端可直调 `platform.UIKit.*` / `platform.Foundation.*` |
| 二进制大小影响 | 编译时合并无影响;DI 框架引入会带运行时 |

## 注意事项与常见坑

1. **expect 不能有函数体/初始化器**: `expect val x: Int = 42` 直接编译失败("Expected property cannot have an initializer")
2. **包名必须一致**: expect 在 `com.example.kmp`,actual 在 `com.example.platform` 不会被编译器关联
3. **不要过度使用 expect/actual**: 业务逻辑尽量放 common;只在确实需要平台 API 时用(否则不如 interface + factory)
4. **typealias actual 只对类有效**: 函数和属性不能 typealias,只能写完整 actual
5. **中间 source set**: 多个 iOS 平台(arm64/x64/simulator) 共享 `iosMain`,把通用 iOS 代码放那里
6. **DI 框架优先于 expect/actual**: 项目用 Koin/Hilt 时,继续保持 DI 一致性,不要混用
7. **Kotlin 1.5+**: expect/actual 已 Stable;1.3-1.4 是 Beta
8. **IDE 辅助**: IntelliJ IDEA 提供 "Create missed actuals..." 快速生成 platform stub

## 参考资料(实际阅读过的权威来源)

- [Kotlin Multiplatform — Use platform-specific APIs](https://kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html) — expect/actual 4 条规则 + UUID 示例 + Android/iOS 完整 actual 代码 + 三种注入方式对比 + Koin DI 范例
- [Kotlin Multiplatform — Create your first multiplatform app](https://kotlinlang.org/docs/multiplatform/multiplatform-create-first-app.html) — `Platform` interface 模式 + `num` 属性 demo + IntelliJ IDEA 运行配置

补充(辅助理解):
- [BinaryTape/Open-Docs — Kotlin Multiplatform expect/actual 规则中文版](https://github.com/BinaryTape/Open-Docs/blob/main/docs/kmp/multiplatform-expect-actual.md) — expect/actual 4 条规则的中文译本 + intermediate source set 解释