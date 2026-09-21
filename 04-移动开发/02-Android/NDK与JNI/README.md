# NDK 与 JNI

Java/Kotlin 与 C/C++ 互调的领域。JNI 是唯一的稳定 ABI，也是 Android 上所有 native 库
（OpenGL、音视频、加解密、游戏引擎）的入口。

| 子目录 | 说明 |
| --- | --- |
| [JNI互调机制/](./JNI互调机制/) | 符号名 mangling 与链接解析、局部/全局/弱全局引用、`JNI_OnLoad` 版本协商 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 507 | `JNI互调机制/` | JNI 互调机制(短名/长名 mangling 与 `_1`/`_2`/`_3`/`_0XXXX` 转义、局部引用随方法返回失效、全局与弱全局引用、`PushLocalFrame`/`PopLocalFrame`、`RegisterNatives` 与 JNI 版本协商) | Python / Kotlin / C |

## 待研究

- [x] JNI 符号名 mangling 与引用生命周期 → demo 507
- [ ] `GetPrimitiveArrayCritical` 的 critical section 约束与 pin 开销
- [ ] JNI 异常挂起时哪些函数仍可安全调用（白名单）
- [ ] `FindClass` 在 native 线程失败的根因与 `JNI_OnLoad` 缓存 `jclass` 的写法
- [ ] NDK CMake / ndk-build 的 ABI 过滤与 `android.mk` 打包

## 参考资料

- Oracle JNI 规范（Design / Types / Functions / Invocation）:
  `https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/`
- OpenJDK 主干 `src/java.base/share/native/include/jni.h`
