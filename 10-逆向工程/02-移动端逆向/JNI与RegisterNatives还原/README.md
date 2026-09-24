# JNI 符号解析与 RegisterNatives 动态注册还原

> Java 层的 `native` 方法最终要绑到 so 里的函数,有**两条绑定通道**:
> ①静态:按 JNI 规范的名字 mangling 解析导出符号;②动态:`JNI_OnLoad` 里调
> `RegisterNatives` 直接塞 `{name, sig, fnPtr}` 三元组。壳与混淆普遍用②——
> so 导出表里找不到任何 `Java_` 开头的符号,就该想到它。

## 1. 静态通道:名字拼接与转义表(JNI 规范 design.html)

```text
Java_ + 转义(FQCN 各段,斜杠作分隔) + _ + 转义方法名 [+ 重载: __ + 转义参数签名]
```

| 转义 | 含义 |
| --- | --- |
| `_1` | 字符 `_` 本身 |
| `_2` | 签名里的 `;`(如 `Ljava/lang/String;` → `Ljava_lang_String_2`) |
| `_3` | 签名里的 `[`(如 `[I` → `_3I`) |
| `_0XXXX` | 非 ASCII 字符,**小写**十六进制(规范强调 as opposed to _0ABCD) |

- **先转义段内、再用 `_` 连接**:类名自带下划线(`foo_bar`)必须先变 `foo_1bar`,
  否则与分隔符混淆;
- 签名取**参数部分**:不带括号、不带返回值,`f(double)` → `Java_Cls2_f__D`。

## 2. 长/短名:什么时候才需要参数后缀

VM 解析序:**先找短名,找不到才找长名**。规范原文的判定:

> 长名只在 native 方法与**另一个 native 方法**重载时必需;
> 与普通 Java 方法同名重载不需要(Java 方法不在 so 符号库里)。

规范 Cls1 例子:`int g(int)`(Java)+ `native int g(double)` → 短名 `Java_Cls1_g` 就能绑定。

## 3. 动态通道:RegisterNatives(functions.html)

```c
typedef struct { char *name; char *signature; void *fnPtr; } JNINativeMethod;
// JNIEnv 函数表第 215 项;成功返回 0,失败负值
```

- fnPtr 签名:`ReturnType (*fnPtr)(JNIEnv *env, jobject objectOrClass, ...)`;
- **第二参数**:实例方法收 `jobject`(对象),静态方法收 `jclass`(类对象);
- 抛 `NoSuchMethodError` 的两种情况:方法不存在,或**不是 native**;
- 逆向落点:hook `RegisterNatives`(libart 导出),第 3/4 个参数就是三元组数组与长度,
  **明文方法名 + 真实函数地址**一次到手——比静态通道信息还全。

## 4. UnregisterNatives:回到链接前

注销后类回到**未链接状态**,符号名解析通道重新生效。
规范注明"正常代码不应使用",它是热重载/对抗场景的专用后门:
壳可以先 Unregister 再 Register 假实现,防御方也可以反向利用。

## 自检

`python selfcheck_jnimap.py` —— 13 项断言:拼接与四种转义(含小写 hex)/
Cls1 与 Cls2 两种重载情形的解析序 / 短名优先 / 函数表索引 215 /
JNINativeMethod 24 字节结构体解析 / jobject vs jclass / NoSuchMethodError 条件 /
Unregister 回退。Go 侧 `jnimap.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [JNI 规范 — Resolving Native Method Names(design.html)](https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/design.html)
- [JNI 规范 — RegisterNatives / UnregisterNatives(functions.html)](https://docs.oracle.com/javase/8/docs/technotes/guides/jni/spec/functions.html)
