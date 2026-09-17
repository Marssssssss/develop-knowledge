# Objection 与 Frida：分层关系与用法

> Objection 是「跑在 Frida 之上的 runtime mobile exploration toolkit」——把常用 Frida hook 预写成 agent 命令 + REPL。本 demo 模拟其分层架构、gadget/usb 双模式、job 模型与 patchapk 流程。Python 12 项断言实跑全绿。

## 简介

自底向上四层：**frida-core**（C 编写，向目标进程注入 QuickJS，宿主↔agent 双向消息通道）→ **语言绑定**（Python/Node/Go/Swift）→ **Frida API / frida-tools CLI**（`frida`、`frida-trace`）→ **Objection**（agent 是 TypeScript 写的命令集，编译注入后由 REPL 驱动）。安装即 `pip3 install objection`；支持 iOS + Android，**无需越狱/root 也能用**（gadget 模式）。

## 原理详解

1. **两种接入模式**（MASTG-TOOL-0029 口径）：
   - **frida-server（usb 模式）**：root 设备跑 frida-server，`objection -n "Telegram" start` 按 App 名 attach（或 `-s -n 包名` spawn；也可按 PID）。App 有高级 RASP/root 检测时可能不易。
   - **gadget 模式**：`objection patchapk` 重打包——解码（apktool）→ 注入 `libfrida-gadget.so` + loader → 回编 → **重签**（原 v1/v2 签名已失效，见 APK签名校验 demo）→ 装 patched APK，宿主连 `Gadget` 进程（`objection -g explore` 或 `-f` 前台进程）。**非 root 设备动态分析的通行路径**。
2. **REPL → agent 命令通道**：命令（如 `android sslpinning disable`）经 Frida 会话变成 agent 命令消息；agent 侧是命令注册表（TypeScript，`agent/src/` 下按域组织：`android/pinning.ts`、`android/root.ts`…）。
3. **job 模型**（源码口径）：每条批量动作建 `Job(identifier, name)`；`addImplementation` **只把成功挂上的 hook 入列**（类不存在 → `undefined` → 跳过）——这正是混淆 App 上「部分 hook 静默失效但其余生效」的机制。
4. **Android 专属能力**（官方 Features wiki）：列 Activity/Service/Receiver、watch class method（支持重载）、keystore 列表与**监视 keystore 使用以提取密码**、RootBeer 等 root 检测绕过、**deoptimize 强制代码走解释器以提高 hook 可靠性**、app 级代理设置、intent 监视。
5. **iOS 专属**：keychain dump/增删、NSUserDefaults/NSHTTPCookieStorage dump、生物识别绕过、CommonCrypto 实时监视、越狱检测绕过（JailMonkey）。
6. **跨平台**：文件系统交互（列目录/上传下载）、内存模块列表与 dump/patch、执行自定义 Frida 脚本、pattern hooking。

## 对比

| | frida-tools（CLI/自写 agent） | objection |
| --- | --- | --- |
| 定位 | 通用插桩（自写 JS） | 移动安全测试的命令集 |
| 学习成本 | 高（写 agent） | 低（REPL 命令） |
| 灵活性 | 完全自由 | 预置命令 + 自定义脚本逃生口 |
| SSL 绕过覆盖 | frida-multiple-unpinning 更广 | `pinning.ts` 五条路径（见 SSLPinning demo） |

## 环境

`python` ≥3.8（标准库）。真机：`pip3 install objection`；gadget 模式还需 apktool 与调试签名密钥。

## 运行方式

```bash
python objection_frida.py      # 12 项断言
# 真机:
#   objection -n "Telegram" start              # usb 模式(root + frida-server)
#   objection patchapk -s com.example.app      # gadget 模式重打包
#   objection -g explore                       # 连 Gadget 进程
```

## 关键代码

- `Device.attach`：usb 模式校验 frida-server 存在性；gadget 模式校验 patched APK——未 patch 即报错（MASTG：非 root + 高级 RASP 时 gadget 常是唯一路径）。
- `patchapk`：四步流程语义版，末步 `re-sign`——与 APK 签名 demo 的「重打包破坏 v2 摘要」结论互证。
- `Job.addImplementation`：`None` 不入列（only-if-hooked）。

## 性能边界

- REPL 命令 → agent 消息往返为毫秒级；重量级操作在 hook 本身（Java 层方法替换，µs 级/调用）。
- patchapk 的耗时大头在 apktool 解码/回编大型 APK。

## 注意事项与常见坑

- **gadget 模式忘了 patchapk** 直接连 `Gadget` → 连不上（不是挂了）。
- **patched APK 是重签名的**：签名指纹变了，服务器端「签名校验接口」、同签名 App 共享 uid 的场景都会受影响；且过不了 Play 完整性类校验。
- job 模型意味着**命令成功 ≠ 全部 hook 生效**：混淆 App 上要看 agent 回执里挂上了几条。
- `deoptimize`（强制解释执行）是为 hook 可靠性付出的运行时性能换购——热路径 App 上会明显变慢。
- REPL 命令是有参数形态的（`android hooking watch class method com.x.Y z`），模拟中仅分发表直连命令。

## 参考资料（实际读过）

- [objection — sensepost/objection README（官方）](https://github.com/sensepost/objection)
- [objection Wiki — Features（官方，1.12.0 能力清单）](https://github.com/sensepost/objection/wiki/Features)
- [MASTG-TOOL-0029: objection (Android) — OWASP MAS](https://mas.owasp.org/MASTG/tools/android/MASTG-TOOL-0029/)
- [objection agent 源码 `agent/src/android/pinning.ts`（job 模型原始出处）](https://github.com/sensepost/objection/blob/master/agent/src/android/pinning.ts)
