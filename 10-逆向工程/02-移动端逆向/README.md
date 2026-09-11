# 移动端逆向

> 移动 App（Android / iOS）的静态与动态分析。动态侧核心工具为 Frida：动态代码插桩工具包，向 Windows / macOS / Linux / iOS / Android 等平台的原生 App 注入 JavaScript 片段——其核心用 C 编写，将 QuickJS 注入目标进程，JS 可访问内存、hook 函数甚至主动调用进程内的原生函数；宿主与注入脚本之间通过双向通信通道交互，上层提供 Python（及 Node/Go/Swift 等）绑定。官方用例包括对加密网络协议做 API tracing、构建"增强版 Wireshark"等。

## 核心研究主题

- **Android 静态**：APK 结构、jadx / apktool 反编译、smali、so 层（JNI）分析
- **iOS**：砸壳（dump）、class-dump、越狱环境
- **动态插桩**：Frida hook Java 层与 Native 层、API tracing、函数调用观测
- **加密协议分析**：用 Frida 在加密前/解密后抓取明文数据
- **对抗**：反调试 / root 检测及其绕过、SSL Pinning

## 待研究

- [ ] Frida hook Android Java 方法（最小示例）
- [ ] Frida Interceptor.attach hook Native 函数
- [ ] APK 签名校验机制（v1/v2/v3）
- [ ] SSL Pinning 绕过原理
- [ ] Frida 与 Objection 的关系与用法

## 参考资料（已读）

- [Frida 官方文档 — A world-class dynamic instrumentation toolkit](https://frida.re/docs/home/)
