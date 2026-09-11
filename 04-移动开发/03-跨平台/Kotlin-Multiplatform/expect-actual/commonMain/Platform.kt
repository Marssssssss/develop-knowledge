// commonMain/Platform.kt — 跨平台 common 代码:只有 expect 声明,无实现
//
// 权威资料(kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html):
//   "With this mechanism, the common source set of a multiplatform module defines
//    an expected declaration, and every platform source set must provide the
//    actual declaration that corresponds to the expected declaration."
//
// expect/actual 4 个核心规则:
//   1. expect 用 expect 关键字,actual 用 actual 关键字
//   2. expect 和 actual 必须同名且在同一个包(同名 fully qualified name)
//   3. expect 声明不能有函数体/初始化器(实现由 actual 提供)
//   4. 编译器会校验每个 expect 在所有目标 source set 都有对应 actual
//
// 这些声明会在不同平台编译时被合并成单个带具体实现的声明
// - android: 走 androidMain/Platform.android.kt
// - ios:     走 iosMain/Platform.ios.kt

package com.example.kmp

// ---------- Demo 1: expect/actual 函数 ----------
// 跨平台生成 UUID:Android 走 java.util.UUID,iOS 走 platform.Foundation.NSUUID
expect fun randomUUID(): String

// ---------- Demo 2: expect/actual 属性 ----------
// 跨平台常量:Android = 1,iOS = 2
expect val platformTag: Int

// ---------- Demo 3: expect/actual 类 ----------
// 跨平台原子引用:Android = java.util.concurrent.atomic.AtomicReference
//                  iOS     = 平台不支持 atomic(实际开发会引入 kotlinx.atomicfu)
expect class AtomicRef<T>(value: T) {
    fun get(): T
    fun set(value: T)
    fun getAndSet(value: T): T
    fun compareAndSet(expect: T, update: T): Boolean
}

// ---------- Demo 4: expect/actual 枚举 + 函数 ----------
// 跨平台日志级别
enum class LogLevel { DEBUG, WARN, ERROR }

// expect 函数:common 中无实现,由各平台 actual 提供
// 这里演示一个稍微复杂的签名 — 带 enum 参数
internal expect fun writeLogMessage(message: String, logLevel: LogLevel)

// 由 expect writeLogMessage 衍生的 common API
fun logDebug(message: String) = writeLogMessage(message, LogLevel.DEBUG)
fun logWarn(message: String) = writeLogMessage(message, LogLevel.WARN)
fun logError(message: String) = writeLogMessage(message, LogLevel.ERROR)

// ---------- Demo 5: 普通 common 代码,直接用 expect 声明 ----------
// 客户端代码不感知底层平台差异 — 这是 KMP 的核心价值
fun greet(to: String): String {
    val firstWord = if ((0..1).random() == 0) "Hi!" else "Hello!"
    return "$firstWord [$platformTag] guess-what:${to.reversed()} uuid=${randomUUID()}"
}

// 在 common 中持有 AtomicRef,演示类 typealias 的实际使用
class SharedCounter {
    private val ref = AtomicRef(0)
    fun incrementAndGet(): Int {
        while (true) {
            val cur = ref.get()
            val next = cur + 1
            if (ref.compareAndSet(cur, next)) return next
        }
    }
    fun get(): Int = ref.get()
}