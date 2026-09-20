# 反调试与越狱（Root）检测对抗

## 简介

动态分析 App 时遇到的第一堵墙通常不是混淆，而是**反调试 / Root / 越狱检测**：App 一发现调试器附着、设备已 root、或者被注入了 Frida，就直接退出或故意跑错逻辑。这一节不是"教你藏"，而是把**检测与绕过这一对矛和盾建模**——理解它才能判断"该 patch 哪里、该 hook 几层、什么时候该放弃 hook 去改环境"。

本目录按 OWASP MASTG 的官方判据实现：

- `anti_debug.py` — 三层 API（Java / native / syscall）的检查注册表、组合策略、`Bypass`（patch / hook / 改环境）
- `selfcheck_anti.py` — 83 项断言
- `anti_debug.go` / `anti_selfcheck.go` — 同构 Go 实现（本机无 Go 工具链，走静态校验）

## 原理详解

### 1. 检测分布在三个 API 层

MASTG-TEST-0046 给出的有效性判据里，最关键的一条是：反调试机制应当**分布在多个 API 层**上 —— Java 层、native 库函数层、汇编/系统调用层。本目录的注册表就按这三层组织：

| 层 | 检查 | 事实来源 |
| --- | --- | --- |
| Java | `Debug.isDebuggerConnected()` | 调试器是否已附着 |
| Java | `ApplicationInfo.FLAG_DEBUGGABLE` | 清单里的可调试位 |
| Java | su 二进制 / root 包名 | 设备是否被 root |
| Java | 第三方应用商店（Sileo、Zebra…） | 设备是否越狱 |
| native | `/proc` 里的 TracerPid | 是否被跟踪 |
| native | `maps` 里的 frida-agent / gadget | 是否被注入 |
| native | 27047 端口 | Frida 是否在监听 |
| native | 越狱特征文件 | 设备是否越狱 |
| syscall | `ptrace(PTRACE_TRACEME)` 自我附加 | 已被附加则再附加失败 |
| syscall | `fork()` 返回值 | iOS 沙箱内 fork 会失败 |

### 2. 三种绕过手段（MASTG-TEST-0046）

1. **把反调试逻辑 patch 成 NOP** —— 直接改二进制；逻辑复杂时要下的补丁也复杂。
2. **用 Frida / Xposed 在 Java 层与 native 层 hook** —— 篡改 `isDebuggable`、`isDebuggerConnected` 这类函数的返回值。
3. **改环境** —— MASTG 原话：Android 是开放环境，"如果别的办法都不行，你可以修改操作系统，推翻开发者设计这些 trick 时所作的假设"。

本目录把 1 和 2 统一建模为 `Bypass.patch()`（该检查恒返回 False），把 3 建模为 `Bypass.override()`（直接改底层事实）。

### 3. 为什么"hook 一层"必然失败

这是本目录最想说明的一点：检测是**分层**的，而 hook 通常只在**一层**生效。

```
只 hook Java 层   → 剩余 [native, syscall] → 仍被检出
再 hook native 层 → 剩余 [syscall]        → 仍被检出
三层全 hook       → 剩余 []               → 绕过成功
```

`Bypass.remainingLayers()` 直接回答"还差哪几层"。这也是为什么实战里大家宁愿改环境：把 TracerPid / frida-agent / su 这些**根因**抹掉，所有派生检查一起熄灭，一个 hook 都不用下。

### 4. 分散 ≠ 独立：阈值策略的陷阱

MASTG 要求"多种检测手段**分散**在代码各处，而不是全在一个方法里"。但"分散"不等于"独立"：

- `native_tracer_pid` 与 `syscall_ptrace_traceme` **共享同一个根因**（已被跟踪），`tracer_pid != 0` 会**同时**点亮两条；
- 于是"看起来有两条信号"，实际改一个根因就全灭；
- 更糟的是，若环境里还有第二个根因（比如调试器真的附着了），只改第一个根因**灭不掉**第二条。

所以阈值/组合策略要建立在**相互独立的事实**上（如"调试器附着"+"maps 里有 frida"+"fork 成功"），而不是同一事实的多种探测方式。

### 5. Hook 检测要盯哪些 API

MASTG-TEST-0354 列出了被 hook 后危害最大的敏感 API（本目录原样收录 12 个）：`SecItemCopyMatching` / `SecItemAdd` / `SecItemUpdate`（钥匙串）、`SecKeyCreateSignature` / `SecKeyCreateDecryptedData` / `CCCrypt`（密钥与明文）、`LAContext.evaluatePolicy` / `evaluateAccessControl`（认证）、`URLSession` 的 `dataTask` / `uploadTask` / `downloadTask` 与 `URLSessionTask.resume`（网络数据）。

MASTG 同时提醒：这份清单只是**指示性**的，每个 App 的防御响应都可能不同；而且检测机制本身**不在**本测试的评估范围内 —— 攻防手段持续演进，有足够时间和资源的攻击者总能绕过。

### 6. 越狱检测的形态

MASTG-TEST-0240（静态）/ 0241（运行时）的描述：检测第三方应用商店（Sileo、Zebra 等）的存在，或者"表明设备已越狱的特定文件/目录"是否存在。两者都明确写了**静态分析/自动化绕过工具的局限**：更 sophisticated 的检测要靠人工逆向与去混淆才能定位。

## 环境

- Python 3.8+（只用标准库）
- Go 1.21+（本机无工具链，未实跑；已过括号配平 / 参数个数 / 交叉引用校验）

## 运行方式

```bash
python selfcheck_anti.py     # 83 项断言，全部通过
# Go（有工具链时）
go run .
```

## 关键代码

```python
# 绕过是否成功，取决于"还剩几层没被覆盖"
def remaining_layers(self):
    live = [n for n in self.detector.names if n not in self.patched]
    return sorted({CHECK_LAYER[n] for n in live})

# 改环境 = 直接改底层事实，比 hook 更彻底
b.override("tracer_pid", 0)
b.override("debugger_attached", False)
```

## 性能与边界

- 本目录是**行为模型**，不真的调用 `ptrace` / 读 `/proc`，因此可以在没有真机的情况下验证"分层覆盖"与"独立信号"这两条判据。
- 检查的实现细节（字段名、端口、文件路径）随平台演进而变；模型把它们收在 `check()` 里，换平台只需改这一处。
- 组合策略只实现 `any` / `all` / `threshold` 三种；真实 RASP 会有打分、延时响应、服务端联动等更复杂的策略。

## 注意事项与常见坑

1. **hook 一层不够** —— 检测分布在 Java / native / syscall 三层，先看 `remainingLayers()`。
2. **别把"分散"当成"独立"** —— 同一根因派生出的多条检查不是独立信号，改一个根因就全灭；反过来，多个根因时只改一个也灭不掉。
3. **hook 检测的目标清单不是全集** —— MASTG 明确说只是指示性的。
4. **Frida 检测不止 27047** —— 端口只是最容易被改的一处；`maps` 里的 agent/gadget 更可靠。
5. **patch NOP 不等于删掉检测** —— 逻辑复杂时补丁会很复杂，且一旦有完整性校验就会触发另一条防御。
6. **改环境最彻底但成本最高** —— MASTG 把它列为"别的办法都不行"时的选择。
7. **别指望静态分析找全检测点** —— MASTG-TEST-0240 明确写了静态分析的局限，需要人工逆向与去混淆。

## 参考资料（实际读过）

- [MASTG-TEST-0046: Testing Anti-Debugging Detection (Android)](https://mas.owasp.org/MASTG/tests/android/MASVS-RESILIENCE/MASTG-TEST-0046/) —— 三种绕过手段（patch NOP / Frida-Xposed hook / 改环境）与有效性判据（jdb 与 ptrace 调试器附加失败或使 App 终止、检测手段分散、分布在多个 API 层）
- [MASTG-TEST-0354: Runtime Use of Hook Detection Techniques (iOS)](https://mas.owasp.org/MASTG/tests/ios/MASVS-RESILIENCE/MASTG-TEST-0354/) —— 12 个敏感 API 清单与"清单只是指示性、检测机制本身不在评估范围"的声明
- [MASTG-TEST-0240: Jailbreak Detection in Code (iOS)](https://mas.owasp.org/MASTG/tests/ios/MASVS-RESILIENCE/MASTG-TEST-0240/) —— 静态形态：检查第三方商店（Sileo、Zebra）或特征文件/目录；静态分析的局限
- [MASTG-TEST-0241: Runtime Use of Jailbreak Detection Techniques (iOS)](https://mas.owasp.org/MASTG/tests/ios/MASVS-RESILIENCE/MASTG-TEST-0241/) —— 运行时形态与自动化绕过工具的局限
- [MASTG-TEST-0401 / 0402: References to / Runtime Use of Debugging Detection APIs](https://mas.owasp.org/MASTG/tests/android/MASVS-RESILIENCE/MASTG-TEST-0402/) —— v1 的 0046 已废弃，官方指向这两个 v2 测试
