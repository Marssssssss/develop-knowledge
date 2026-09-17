# 移动端逆向

> 移动 App（Android / iOS）的静态与动态分析。动态侧核心工具为 Frida：动态代码插桩工具包，向 Windows / macOS / Linux / iOS / Android 等平台的原生 App 注入 JavaScript 片段——其核心用 C 编写，将 QuickJS 注入目标进程，JS 可访问内存、hook 函数甚至主动调用进程内的原生函数；宿主与注入脚本之间通过双向通信通道交互，上层提供 Python（及 Node/Go/Swift 等）绑定。官方用例包括对加密网络协议做 API tracing、构建"增强版 Wireshark"等。

## 核心研究主题

- **Android 静态**：APK 结构、jadx / apktool 反编译、smali、so 层（JNI）分析
- **iOS**：砸壳（dump）、class-dump、越狱环境
- **动态插桩**：Frida hook Java 层与 Native 层、API tracing、函数调用观测
- **加密协议分析**：用 Frida 在加密前/解密后抓取明文数据
- **对抗**：反调试 / root 检测及其绕过、SSL Pinning

## 已完成 demo

| demo | 主题 |
| --- | --- |
| [Frida-Java层hook/](./Frida-Java层hook/) | Java.perform/use、implementation 替换、overload、$new、retain、消息通道 |
| [Frida-Interceptor-native-hook/](./Frida-Interceptor-native-hook/) | inline hook trampoline、onEnter/onLeave、retval.replace、replace vs attach、flush、CModule 热路径 |
| [APK签名校验/](./APK签名校验/) | v1 JAR 保护链、v2 Signing Block/分块摘要/EOCD 口径、防回滚、v3 PoR 密钥轮换 |
| [SSLPinning绕过/](./SSLPinning绕过/) | 三层 pinning、objection pinning.ts 五条 hook 路径、静态/动态绕过、frida-multiple-unpinning |
| [Objection与Frida/](./Objection与Frida/) | 分层架构、gadget vs usb 模式、REPL 命令分发、job 模型、patchapk 重签 |

## 待研究

- [x] Frida hook Android Java 方法（最小示例）→ 312 Frida-Java层hook
- [x] Frida Interceptor.attach hook Native 函数 → 313 Frida-Interceptor-native-hook
- [x] APK 签名校验机制（v1/v2/v3）→ 314 APK签名校验
- [x] SSL Pinning 绕过原理 → 315 SSLPinning绕过
- [x] Frida 与 Objection 的关系与用法 → 316 Objection与Frida
- [ ] iOS 砸壳与 class-dump（dyld 共享缓存、脱壳原理）
- [ ] Frida Stalker 指令级跟踪（与 Interceptor 的边界）
- [ ] smali 语义与 jadx/apktool 输出对照

## 参考资料（已读）

- [Frida 官方文档 — A world-class dynamic instrumentation toolkit](https://frida.re/docs/home/)
- [Frida 官方文档 — Android](https://frida.re/docs/android/)
- [Frida JavaScript API — Java / Interceptor / Module 命名空间](https://frida.re/docs/javascript-api/)
- [APK signature scheme v2 — Android Open Source Project（官方中国镜像）](https://source.android.google.cn/docs/security/features/apksigning/v2)
- [APK signature scheme v3 — Android Open Source Project（官方中国镜像）](https://source.android.google.cn/docs/security/features/apksigning/v3)
- [MASTG-TECH-0012: Bypassing Certificate Pinning — OWASP MAS](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0012)
- [objection agent 源码 pinning.ts — sensepost/objection](https://github.com/sensepost/objection/blob/master/agent/src/android/pinning.ts)
- [objection Wiki — Features](https://github.com/sensepost/objection/wiki/Features)
- [MASTG-TOOL-0029: objection (Android)](https://mas.owasp.org/MASTG/tools/android/MASTG-TOOL-0029/)
