# SSL Pinning 绕过原理

> 证书绑定（Certificate Pinning）让 App 拒绝代理证书；绕过分**静态**（改 smali/资源重打包）与**动态**（Frida hook 校验函数）两路。动态侧完整复刻 objection `pinning.ts` 的 hook 语义。Python 13 项断言实跑全绿 + JS agent 参考。

## 简介

SSL Pinning 分三层：① 全局 `X509TrustManager`（信任链校验）；② 库级 pin（OkHttp `CertificatePinner` 按 **SPKI 哈希**比对）；③ 三方框架（Appcelerator `PinningTrustManager`、PhoneGap `SSLCertificateChecker`）。MASTG 结论：多数 App 用标准 API 几秒即可绕；**自定义/混淆实现的 pinning 必须手工定位 patch，耗时**。动态绕过无需对抗 APK 完整性校验、试错快——这是它相对静态路径的核心优势。

## 原理详解（objection `pinning.ts` 五条代表路径）

1. **空 TrustManager + `SSLContext.init` 改参**：`Java.registerClass` 实现 `X509TrustManager`（三个方法全空），hook `SSLContext.init.overload(...)` 把传入的 TrustManager 数组**换成空实现**再链回原 `init`——此后所有新建 SSLContext 都不校验信任链。
2. **OkHttp `CertificatePinner.check`（`check.overload('java.lang.String','java.util.List')`）与 `check$okhttp`（Kotlin 元符号名）**：替换成空函数体 = 不抛 `SSLPeerUnverifiedException`。
3. **Android 7+ `TrustManagerImpl.verifyChain`（conscrypt）**：跳过全部逻辑**直接返回入参 untrustedChain**（NCC Group 2017 网络安全配置绕过路线）。
4. **`checkTrustedRecursive`**：返回**空 `ArrayList`**（mediaservice 2018 通用绕过路线）。
5. **PhoneGap `SSLCertificateChecker.execute`**：`callBackContext.success('CONNECTION_SECURE')` 并返回 true。
- **only-if-class-exists**：每条 hook 包 `try/catch ClassNotFoundException`，类不存在返回 `undefined`，`job.addImplementation` 只挂成功的——**混淆/裁剪的 App 上部分路径自然失效，其余照常**。
- **反检测细节**：`Java.classFactory.tempFileNaming.prefix` 默认 `'frida'` 会出现在 `/proc/<pid>/maps`，脚本先改成 `'onetwothree'`。
- **frida-multiple-unpinning（CodeShare）覆盖更广**：动态检测 `SSLPeerUnverifiedException` 实例化，**自动 patch 抛出异常的方法**（MASTG-TOOL-0140）。

## 静态路径（MASTG-TECH-0012）

- `grep -ri "sha256\|sha1" ./smali` 找 pin 哈希 → 换成代理 CA 的哈希（或把域名改成不存在域名）。
- `find ./assets -iname "*.cer" -o -iname "*.crt"` / `find . -iname "*.jks" -o -iname "*.bks"` → 用 keytool 把代理证书导入 BKS truststore（需在反编译代码里找硬编码密码）。
- 改完用 apktool 重打包——**原签名失效必须重签**，且需先绕过 App 的完整性校验（这正是动态路径更便利的原因）。
- **找 hook 点方法论**（混淆 App）：找到库的身份串（license/版本字符串）→ 读未混淆版源码找候选方法（如 `CertificatePinner.Builder.add`）→ `grep -ri "java/lang/String;\[Ljava/lang/String;)L"` 按方法签名搜 smali → 逐个 hook 打参数，打出「域名 + 证书哈希」的那个就是目标。

## 对比

| 路径 | 需重打包 | 需过完整性校验 | 试错成本 | 覆盖自定义 pinning |
| --- | --- | --- | --- | --- |
| 动态（Frida/objection） | 否 | 否 | 低（秒级） | 需手工找 hook 点 |
| 静态（smali/资源替换） | 是（重签） | 是 | 高 | 可（时间换覆盖） |
| Xposed（TrustMeAlready 等） | 否 | 否 | 中 | 框架级 |

## 环境

`python` ≥3.8（标准库）。真机：root + frida-server（或 `objection -g` gadget 模式）。

## 运行方式

```bash
python ssl_pinning_bypass.py    # 13 项断言
# 真机: objection -> android sslpinning disable
# 或:  frida -U --codeshare akabe1/frida-multiple-unpinning -f com.example.app
```

## 关键代码

- `hook_ssl_context_empty_tm`：类级 hook `SSLContext.init`，构造新 App 时自动注入空 TM（与 objection 完全同构的生效时机）。
- 断言 2/3 的**分层挂载**：只挂 TrustManager 层时 pinner 仍拦截（组合防御），两层都挂才放行。
- `auto_patch_thrower`：frida-multiple-unpinning 的「异常实例化监视 + patch 抛出点」模型。

## 性能边界

- hook 均为 Java 层方法替换，单次调用开销在 µs 级（见 Interceptor demo 官方口径），对握手路径无感。
- 静态重打包的代价在流程（解码/改/回编/重签/装）而非计算。

## 注意事项与常见坑

- **`check$okhttp` 带 `$`**：Kotlin 内部方法名，Frida 里直接 `pinner.check$okhttp` 访问；Python 模拟改名 `check_okhttp`（`$` 非合法标识符——本轮实跑两次踩 Python 标识符坑的教训）。
- **`_Patched` 不能继承被替换 `__init__` 的异常类**：构造即递归。
- **类级 hook 会污染后续构造的所有实例**——演示「静态路径」前必须还原真实 TM（断言 6/7 的构造教训）。
- 空 TrustManager 绕过后 **pinning 层仍可能拦截**：OkHttp 的 `CertificatePinner` 在握手成功后独立比对 SPKI——两层都要处理。
- `tempFileNaming.prefix` 这类细节是「anti-frida 扫 /proc/maps」检测面的缩影，做对抗时要通读 agent 源码而不是只跑命令。

## 参考资料（实际读过）

- [MASTG-TECH-0012: Bypassing Certificate Pinning — OWASP MAS](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0012)
- [objection `agent/src/android/pinning.ts` — 官方 agent 源码（392 行全文）](https://github.com/sensepost/objection/blob/master/agent/src/android/pinning.ts)
- [MASTG-TOOL-0140: frida-multiple-unpinning](https://mas.owasp.org/MASTG/tools/android/MASTG-TOOL-0140)
- [MASTG-TOOL-0029: objection (Android)](https://mas.owasp.org/MASTG/tools/android/MASTG-TOOL-0029/)
