// Main.kt — 演示入口:展示 common 代码使用 expect 声明 → 编译期绑定到具体 platform
//
// 用法(项目根 build.gradle.kts):
//   kotlin {
//       androidTarget()
//       iosX64()  ; iosArm64()  ; iosSimulatorArm64()
//       sourceSets {
//           val commonMain by getting
//           val androidMain by getting
//           val iosX64Main by getting
//           val iosArm64Main by getting
//           val iosSimulatorArm64Main by getting
//           val iosMain by creating { dependsOn(commonMain) }
//           iosX64Main.dependsOn(iosMain)
//           iosArm64Main.dependsOn(iosMain)
//           iosSimulatorArm64Main.dependsOn(iosMain)
//       }
//   }
//
// iosMain 是 intermediate source set — 被 iosX64Main / iosArm64Main / iosSimulatorArm64Main 共享
// (权威资料见 kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html)

package com.example.kmp

fun main() {
    println("=== Kotlin Multiplatform expect/actual Demo ===\n")

    // ----- Demo 1: randomUUID() -----
    println("[Demo 1: expect/actual function]")
    repeat(3) {
        println("  randomUUID() = ${randomUUID()}")
    }
    println("  -> on Android uses java.util.UUID, on iOS uses platform.Foundation.NSUUID")
    println()

    // ----- Demo 2: platformTag property -----
    println("[Demo 2: expect/actual property]")
    println("  platformTag = $platformTag  (Android=1, iOS=2)")
    println()

    // ----- Demo 3: AtomicRef class -----
    println("[Demo 3: expect/actual class — typealias on Android, full impl on iOS]")
    val counter = SharedCounter()
    repeat(5) { println("  counter.incrementAndGet() = ${counter.incrementAndGet()}") }
    println()

    // ----- Demo 4: LogLevel + writeLogMessage -----
    println("[Demo 4: expect/actual enum + function]")
    logDebug("debug message — visible only in debug build")
    logWarn("warning: approaching rate limit")
    logError("error: failed to fetch")
    println()

    // ----- Demo 5: greet() — common code using all expect declarations -----
    println("[Demo 5: common code using expect declarations]")
    println("  greet(\"Alice\") = ${greet("Alice")}")
    println("  -> notice the [$platformTag] tag differs by platform")
    println()

    // ----- Summary -----
    println("[Summary] (per kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html):")
    println("  - expect/actual: language-level mechanism for cross-platform API access")
    println("  - Each platform source set (androidMain, iosMain, ...) provides actuals")
    println("  - Compiler merges expect + actual at compile time per platform")
    println("  - For complex logic, prefer 'interface in common + actual factories' over expect/actual")
    println("  - Use expect/actual only when truly platform-specific (UUID, NSLog, atomic, etc.)")
}