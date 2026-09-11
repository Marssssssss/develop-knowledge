// androidMain/Platform.android.kt — Android 平台 (JVM) 的 actual 实现
//
// 权威资料(kotlinlang.org/docs/multiplatform/multiplatform-connect-to-apis.html):
//   "The Android implementation uses the APIs available on Android,
//    while the iOS implementation uses the APIs available on iOS."

package com.example.kmp

import java.util.UUID
import android.util.Log
import java.util.concurrent.atomic.AtomicReference

// Demo 1: actual 函数 — Android 用 java.util.UUID
actual fun randomUUID(): String = UUID.randomUUID().toString()

// Demo 2: actual 属性 — Android 标记为 1
actual val platformTag: Int = 1

// Demo 3: actual 类用 typealias 指向 JVM AtomicReference
//   (Kotlin 的 typealias actual 是合法语法,避免写完整的 wrapper 类)
actual typealias AtomicRef<T> = AtomicReference<T>

// Demo 4: actual 日志函数 — Android 走 android.util.Log
internal actual fun writeLogMessage(message: String, logLevel: LogLevel) {
    val tag = "KmpDemo"
    when (logLevel) {
        LogLevel.DEBUG -> Log.d(tag, message)
        LogLevel.WARN  -> Log.w(tag, message)
        LogLevel.ERROR -> Log.e(tag, message)
    }
}