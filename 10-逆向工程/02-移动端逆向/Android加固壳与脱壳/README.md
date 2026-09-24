# Android 加固壳与脱壳(dex 挂载链的语义)

> 一切加固(整体壳/函数抽取/inmem)最终都要回答同一道题:**把解密后的 dex 塞进 ClassLoader 并让 ART 能加载**。
> 而脱壳,就是在这个挂载链的下游把 dex 捞回来。本 demo 用 AOSP libcore 源码的真实语义
> (BaseDexClassLoader / DexPathList / dex-format)搭出这条链,断言每个可利用的性质。

## 1. dex 头三段:落盘验收的硬口径(dex-format 官方)

| 字段 | 位置 | 值 |
| --- | --- | --- |
| magic | `ubyte[8]` | `dex\n035\0`(版本号可变) |
| checksum | `uint` @8 | **adler32(除 magic 与 checksum 自身的全部)** |
| signature | `ubyte[20]` @12 | **SHA-1(除 magic、checksum、signature 自身的全部)** |

生成顺序不可反:checksum 的覆盖范围**包含 signature 字段**,必须先定稿 signature 再算 checksum。
脱壳 dump 出的字节按这三段验收,数据区错一个字节两项同时失配。

## 2. 挂载链(DexPathList 源码语义)

```text
BaseDexClassLoader.findClass
  ├─ sharedLibraryLoaders      ← uses-library 声明,恒在 pathList **之前**查
  ├─ pathList(DexPathList)
  │    └─ for element in dexElements:  首个命中返回
  └─ sharedLibraryLoadersAfter ← OEM 配置,App 不可控,恒在 pathList **之后**
```

- **findClass 顺序遍历 dexElements,首个命中返回**——壳的 stub dex 在前不会挡住后挂的真身类,
  但**同名类先到先得**(防御:壳可放同名类顶替真身,见本目录"反调试"demo);
- **makeDexElements 的 IOException 不快速失败**:坏 dex 记进
  `dexElementsSuppressedExceptions`,等 findClass 落空才随 ClassNotFoundException 上报;
- **addDexPath = concat 追加到尾部**(源码注释引用 b/7726934):原顺序不变,新 dex 优先级最低;
- InMemoryDexClassLoader 走 initDexElements(ByteBuffer[]),不落盘——对应"不落地壳"。

## 3. 壳模型与脱壳点

```text
壳启动:stub dex(classes.dex) ← 只含 Loader/解密器
        attachBaseContext:解密 payload → addDexPath / InMemory 追加
脱壳点:遍历 dexElements → 取每个 DexFile 的内存(mCookie → dex 起址)→ 落盘 → §1 三段验收
```

- 内存 dump 的时机在**挂载之后**(elements 里出现了壳 APK 里不存在的 dex);
- InMemory 挂载的 dex 在文件系统上无对应物,是"必须走内存 dump"的那类;
- 函数抽取壳(NOP 掉方法体、调用时再补)dump 的 dex 方法体不全——三段验收通过
  也不代表可运行,需要额外做主动调用补全。

## 自检

`python selfcheck_unpack.py` —— 6 项断言:dex 头三段范围(改 1 字节双失配)/
findClass 顺序与首中返回 / suppressed 不快速失败语义 / addDexPath 尾部追加 /
三段 loader 查找顺序 / 壳 boot 后真身类可见 + dump 验收。
Go 侧 `unpack.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [BaseDexClassLoader.java / DexPathList.java — AOSP libcore main](https://android.googlesource.com/platform/libcore/+/refs/heads/main/dalvik/src/main/java/dalvik/system/BaseDexClassLoader.java)(googlesource 直抓)
- [Dalvik Executable format(头部/校验)— source.android.com](https://source.android.com/docs/core/runtime/dex-format)
- 本目录 [DEX文件格式解析/](../DEX文件格式解析/)(前置:dex 全结构)
