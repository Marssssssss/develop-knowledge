# Frida Java 层 hook 原理

> 通过替换 ART 方法在运行时的 `implementation`，在**不修改 APK** 的前提下观测/改写 Java 方法调用。配套 `hook_agent.js`（真实 Frida API 风格的 agent 脚本）与 `frida_java_hook.py`（ART 方法分发的可执行模型，跑通 12 项断言）。

## 简介

Frida 在 Android 上 hook Java 方法不走字节码补丁，而是借 **frida-java-bridge**（Frida 17 起从 GumJS 拆出、需 `npm install frida-java-bridge`，REPL/frida-trace 仍内置）操作 ART 的方法结构：`Java.use('android.app.Activity')` 拿到类的 JS 包装，把 `onResume.implementation` 替换成 JS 函数后，**所有经过 ART 分发对该方法的调用都会先进入 JS**；在替换函数里用 `this.onResume()` 还能调回原始实现。宿主侧（Python）与 agent 侧通过 `send()`/`recv()` 双向消息通信。

## 原理详解

1. **`Java.available` 门控**：当前进程必须已加载 Java VM（Dalvik/ART）才能碰其余 Java API——frida-server attach 到纯 native 进程时它为 false。
2. **`Java.perform(fn)` 的「等类加载器」语义**：确保当前线程 attach 到 VM，并且**若 App 的 class loader 尚不可用则推迟执行 fn**（对应 `Java.performNow` = 不等类加载器的版本）。Agent 常见 bug 就是直接在顶层调 `Java.use` 而没包 `perform`。
3. **`Java.use(className)` 包装 + `implementation` 替换**：官方示例——
   ```js
   Java.perform(() => {
     const Activity = Java.use('android.app.Activity');
     Activity.onResume.implementation = function () {
       send('onResume() got called! Let\'s call the original implementation');
       this.onResume();   // 调原始实现
     };
   });
   ```
   替换函数内 `this` 绑定当前实例；`this.onResume()` 走的是**原始** ArtMethod（Frida 保存了原入口），不会递归进替换函数。也支持直接 `throw Exception.$new('Oh noes!')`。
4. **重载 `.overload(...)`**：同名方法按参数类型区分，hook 必须显式选重载，否则多重载方法会报歧义；没 hook 的重载不受影响。
5. **`$new()` / `$dispose()`**：类包装可调构造器造实例；`Java.cast(handle, klass)` 把裸指针转成包装；`Java.retain(obj)` 把实例包装复制到替换函数外留存（避免句柄随替换结束失效）。
6. **枚举**：`Java.enumerateLoadedClasses`（当前已加载类）、`Java.enumerateMethods('*youtube*!on*')`（glob 匹配 + `s` 修饰符带签名）。
7. **消息通道**：agent `send(payload[, bytes])` 异步发宿主；二进制用 ArrayBuffer 第二参传输。高频率时官方建议**批量合并**发送。

## 对比

| 方式 | 侵入性 | 需重打包 | 适配混淆 |
| --- | --- | --- | --- |
| Frida `implementation` 替换（本 demo） | 运行时内存改方法入口 | 否（root + frida-server） | 需先定位类名（可枚举） |
| smali 补丁重打包 | 改 APK 字节码 | 是（签名失效需重签） | 同上 |
| Xposed/LSPosed 模块 | 框架级全局 hook | 否（需框架） | 同上 |

## 环境

- `python`（≥3.8，仅标准库）：跑模拟与断言
- 真机：root 设备 + `frida-server` push 到 `/data/local/tmp`（官方 Android 教程口径），桌面 `pip install frida-tools`

## 运行方式

```bash
python frida_java_hook.py     # 12 项断言全绿
frida -U -l hook_agent.js -f com.example.app   # 真机用法(参考)
```

## 关键代码

- `JavaVM.perform`：类加载器未就绪时把回调**入队**，`set_class_loader_ready()` 后冲刷——对应官方「Will defer calling fn if the app's class loader is not available yet」。
- `JavaMethod.dispatch`：`impl` 已被替换且调用来自替换函数内部时走 `_orig`——对应 `this.onResume()` 不递归。
- `KlassWrapper.overload(*types)`：按参数类型串选重载句柄。

## 性能边界

- Java 层 hook 单次开销 ≈ ART↔JS 一次跨界 + 参数/返回值编组；热路径官方建议 CModule（见 Interceptor demo）。模拟中未建性能模型，只保证语义正确。

## 注意事项与常见坑

- **忘包 `Java.perform`** → class loader 未就绪时 `Java.use` 失败；REPL 里 Frida 自动包，独立 agent 必须手包。
- **多重载方法直接 `method.implementation = fn`** 会因歧义报错，必须 `.overload('java.lang.String', 'java.util.List')` 精确选。
- **替换函数里保存 `this`**：跨调用留存要 `Java.retain`，直接引用随替换帧失效。
- 模拟与真实 ART 的差异：真实替换是改 ArtMethod 的 entrypoint（frida-java-bridge 另有 `tempFileNaming` 反检测细节，见 SSL Pinning demo）；本 demo 只建**分发语义**模型，未复现内存布局——口径已在此声明。

## 参考资料（实际读过）

- [Frida 官方文档 — Android](https://frida.re/docs/android/)
- [Frida JavaScript API — Java 命名空间](https://frida.re/docs/javascript-api/)
- [frida-java-bridge（Frida 17 起的独立包）](https://github.com/frida/frida-java-bridge)
