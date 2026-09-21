# JNI 互调机制（Java ↔ C/C++）

JNI 是 JVM/ART 暴露给 native 代码的**唯一稳定 ABI**。本 demo 把三件最容易出错、却又完全可判定的事做成可执行模型：
符号名 mangling 与链接解析、引用的生命周期、`JNI_OnLoad` 的版本协商。

依据：Oracle《Java Native Interface Specification》Chapter 2「Design Overview」，以及
OpenJDK `src/java.base/share/native/include/jni.h`。

## 一、原理详解

### 1.1 JNIEnv 是「指针的指针的指针」

规范原文：*"An interface pointer is a pointer to a pointer. This pointer points to an array of
pointers, each of which points to an interface function. Every interface function is at a
predefined offset inside the array."* 即 `JNIEnv` 在非 C++ 语境下是
`const struct JNINativeInterface_ *`，C++ 里 `JNIEnv_` 只是把间接层包成 inline 成员函数——
**底层机制完全相同**。

由此推出两条硬约束：

- *"The JNI interface pointer is only valid in the current thread."* → **不能把 JNIEnv 跨线程传**。
  要跨线程必须先 `JavaVM->AttachCurrentThread` 拿到本线程的 env。
- 同一 Java 线程多次调用同一 native 方法拿到的是**同一个**指针；不同线程可能不同。

### 1.2 符号名 mangling

规范列出拼接顺序：

```
Java_ + mangled(fully-qualified class name) + '_' + mangled(method name)
     [ + '__' + mangled(argument signature) ]        // 仅重载方法需要
```

转义表（规范 Table: Unicode Character Translation）：

| 序列 | 含义 |
| --- | --- |
| `_0XXXX` | Unicode 码点 XXXX，**用小写十六进制** |
| `_1` | 字符 `_` |
| `_2` | 签名中的 `;` |
| `_3` | 签名中的 `[` |
| `/` → `_` | 全限定类名的包分隔符 |

规范给的 canonical 例子：

```java
package pkg;
class Cls { native double f(int i, String s); }
```

对应 C 函数名是 `Java_pkg_Cls_f__ILjava_lang_String_2`。

**两个常被弄错的细节：**

1. 长名的 `__` 后面只有**参数签名**，既不含括号，也**不含返回类型**——所以 `(D)I` 与 `(D)Z`
   两个重载在符号层面是同一个名字，JNI 层无法区分（这也是「返回类型不参与重载解析」在 native 侧的体现）。
2. **短名优先**：*"The VM looks first for the short name ... It then looks for the long name."*
   而且只有在**与同一个 native 库里的另一个 native 方法**重名时才必须用长名——同名的 Java 方法
   （非 native）根本不在库里，不构成冲突。

### 1.3 引用：局部 / 全局 / 弱全局

`jni.h` 的枚举：

```c
typedef enum _jobjectType {
     JNIInvalidRefType    = 0,
     JNILocalRefType      = 1,
     JNIGlobalRefType     = 2,
     JNIWeakGlobalRefType = 3
} jobjectRefType;
```

语义（规范 §Global and Local References）：

- **局部引用**：*"valid for the duration of a native method call, and are automatically freed
  after the native method returns"*。传给 native 方法的对象、JNI 函数返回的对象，**全是局部引用**。
- **全局引用**：*"remain valid until they are explicitly freed"*，靠 `NewGlobalRef` / `DeleteGlobalRef`。
  它是**强引用**，会阻止 GC。
- **弱全局引用**：不阻止 GC；GC 后解引用得到 `NULL`（配合 `IsSameObject(ref, NULL)` 判活）。
- 局部引用**只在创建它的线程有效**：*"The native code must not pass local references from one
  thread to another."*

为什么局部引用不能靠扫描 native 栈来实现？规范给了答案：*"The native code may store local
references into global or heap data structures."* → 所以 VM 必须为每次
Java→native 的控制转移维护一张 **registry**，native 方法返回时整表删除。

### 1.4 显式释放与局部引用帧

多数情况靠 VM 自动回收即可，但两种情形要手动 `DeleteLocalRef`：

- 拿住一个大对象后还要做很久计算；
- 循环里创建大量局部引用（每个引用都要占表项）。

批量管理用 `PushLocalFrame(capacity)` / `PopLocalFrame(result)`：pop 会释放该帧内所有局部引用，
**唯独 result 被提升到外层帧**——这是从循环里返回一个对象的唯一正确写法。
`EnsureLocalCapacity(n)` 只是「预告」，返回 `JNI_OK` / `JNI_ENOMEM`。

### 1.5 RegisterNatives 与 JNI_OnLoad

```c
typedef struct { char *name; char *signature; void *fnPtr; } JNINativeMethod;
JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM *vm, void *reserved);
```

`RegisterNatives` 让符号名彻底摆脱 mangling 规则，**也是静态链接函数唯一的注册方式**
（规范：*"The RegisterNatives() function is particularly useful with statically linked functions."*）。

`JNI_OnLoad` 的返回值是版本协商：返回 VM 不支持的版本 → 加载失败。jni.h 的取值：

```c
#define JNI_VERSION_1_1 0x00010001 ... #define JNI_VERSION_1_8 0x00010008
#define JNI_VERSION_9  0x00090000  #define JNI_VERSION_10 0x000a0000 ...
```

错误码同样出自 jni.h：`JNI_OK=0 / JNI_ERR=-1 / JNI_EDETACHED=-2 / JNI_EVERSION=-3 /
JNI_ENOMEM=-4 / JNI_EEXIST=-5 / JNI_EINVAL=-6`。

## 二、对比：三种互调方式

| 方式 | 符号来源 | 首次调用开销 | 典型场景 |
| --- | --- | --- | --- |
| 静态命名 `Java_xxx` | mangling 规则 | 需按名字动态查找 | 简单 demo、少量方法 |
| `RegisterNatives` | JNINativeMethod 表 | 加载期一次性登记 | 正式库、静态链接 |
| JNA / JNR | 运行时反射 | 最大 | 无需写 C 胶水层 |

## 三、环境要求

- Python 3.8+（模型无依赖）
- 真机验证需 NDK r21+ 与 `external fun` 声明

## 四、运行方式

```bash
cd 04-移动开发/02-Android/NDK与JNI/JNI互调机制
python selfcheck_jni.py      # 期望输出 PASS 50
```

## 五、关键代码

- `main.py`：`mangle` / `short_name` / `long_name` / `resolve_symbol` / `JNIEnv` / `jni_on_load` / `register_natives`
- `selfcheck_jni.py`：50 条断言
- `JniBridge.kt`：Kotlin 侧声明与 `JNI_OnLoad` 的对偶
- `jni_bridge.c`：C 侧参考实现（本机无 NDK，仅作人工审查）

## 六、性能边界与注意事项

- **每次 JNI 调用要跨 ABI 边界**，开销远高于普通方法调用；批量数组访问务必用
  `GetPrimitiveArrayCritical` / `ReleasePrimitiveArrayCritical` 或 `Get<Type>ArrayElements` 的
  `JNI_COMMIT` / `JNI_ABORT` 模式，避免逐元素 `GetObjectArrayElement`。
- `GetStringCritical` 期间**不能调用其他 JNI 函数**，也不能阻塞——规范把它列为「critical section」。
- **异常未清理时不要继续调 JNI**：只有少数函数（如 `ExceptionOccurred` / `ExceptionClear` /
  `ReleaseXXX`）在异常挂起时是安全的，其余行为未定义。
- 局部引用表默认容量有限（HotSpot 16，ART 512）；循环里不释放会 `JNI_ENOMEM` → 崩溃。
- **常见坑**：把 `jclass` 缓存成静态变量却忘了 `NewGlobalRef`（方法返回后即失效）；
  在子线程直接用父线程的 `JNIEnv`；`FindClass` 在 native 线程里失败（ClassLoader 找不到——
  要在 `JNI_OnLoad` 里缓存）。

## 七、参考资料

实际读取的原文：

- Oracle JNI 规范 Chapter 2（Design Overview）:
  `https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/design.html`
- Oracle JNI 规范 Chapter 3（JNI Types and Data Structures）:
  `https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/types.html`
- Oracle JNI 规范 Chapter 4（JNI Functions）:
  `https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/functions.html`
- Oracle JNI 规范 Chapter 5（The Invocation API）:
  `https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/invocation.html`
- OpenJDK 主干 `src/java.base/share/native/include/jni.h`（版本常量、jobjectRefType、JNINativeMethod、函数表）

> 口径说明：Android ART 的 `jni.h` 与 OpenJDK 版本常量一致（`JNI_VERSION_1_6` 是 Android 上最常返回的
> 版本）。本 demo 的数值全部取自上述原文，未采用第三方博客的转述。
