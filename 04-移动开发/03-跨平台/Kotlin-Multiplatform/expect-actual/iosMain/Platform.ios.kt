// iosMain/Platform.ios.kt — iOS 平台 (Native) 的 actual 实现
//
// 权威资料(kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html):
//   "You can access iOS APIs from Kotlin/Native code."

package com.example.kmp

import platform.Foundation.NSUUID
import platform.Foundation.NSLog

// Demo 1: actual 函数 — iOS 用 platform.Foundation.NSUUID
actual fun randomUUID(): String = NSUUID().UUIDString()

// Demo 2: actual 属性 — iOS 标记为 2
actual val platformTag: Int = 2

// Demo 3: actual 类 — iOS 没有原生 atomic,这里用 var + @Synchronized 模拟
//   生产代码推荐用 kotlinx.atomicfu 的 AtomicReference(跨平台原子类型)
//   此处为了 demo 简洁,展示 typealias 不适用时如何写完整 actual 类
actual class AtomicRef<T>(initialValue: T) {
    private val lock = Object()
    private var value: T = initialValue

    actual fun get(): T = synchronized(lock) { value }
    actual fun set(value: T) { synchronized(lock) { this.value = value } }
    actual fun getAndSet(value: T): T = synchronized(lock) {
        val old = this.value
        this.value = value
        old
    }
    actual fun compareAndSet(expect: T, update: T): Boolean = synchronized(lock) {
        if (value == expect) { value = update; true } else false
    }
}

// Demo 4: actual 日志函数 — iOS 走 NSLog
internal actual fun writeLogMessage(message: String, logLevel: LogLevel) {
    val prefix = when (logLevel) {
        LogLevel.DEBUG -> "[DEBUG]"
        LogLevel.WARN  -> "[WARN!]"
        LogLevel.ERROR -> "[ERROR]"
    }
    NSLog("$prefix KmpDemo: %@", message)
}