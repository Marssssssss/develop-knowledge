# Tauri vs Electron:系统 WebView 容器 vs 自带浏览器引擎

## 简介

"用 HTML 写桌面/移动应用"有两条路,差别不在语法而在**你把浏览器放在哪里**:
**Electron** 打包一整套 Chromium + Node 自己当宿主;**Tauri** 只用**操作系统自带的
WebView**,由 Rust core 当宿主、不携带运行时。这个差别会一路渗透到进程构成、IPC 路径、
包体积、安全模型与"同一份前端要适配几个引擎"。

关键概念:

- **系统 WebView** —— WKWebView / WebView2 / WebKitGTK / Android System WebView。
- **message passing** —— Tauri 官方对 WebView 与宿主之间通信方式的描述。
- **main / renderer / preload** —— Electron 的三段式进程与"中间人"分层。
- **Structured Clone Algorithm** —— Electron IPC 的参数序列化规则,决定什么能传;
  **capability(能力)** 则是 Tauri v2 的声明式权限,Electron 对应的是 preload 手工裁剪。

## 原理详解

### 1. Tauri 的分层:Rust core 持有系统能力,WebView 只负责画

官方架构页否定了两个误解:*"Tauri is not a lightweight kernel wrapper. Instead, it
directly uses WRY and TAO to do the heavy lifting in making system calls to the OS."*
以及 *"Tauri is not a VM or virtualized environment."*

| 层 | 作用 |
| --- | --- |
| `tauri` | 主 crate:编译期读 `tauri.conf.json`、运行时做脚本注入(polyfill/原型修订)、托管系统交互 API、管理更新 |
| `tauri-utils` / `tauri-codegen` | 解析配置、识别 platform triple、注入 CSP;codegen 还负责**嵌入、哈希、压缩**资源 |
| `tauri-runtime` / `-wry` | 与底层 WebView 库之间的胶水层;WRY 专属的直接系统交互(打印、显示器探测等) |
| `tauri-macros` / `tauri-build` | 过程宏与构建期接线 |

上游两个 crate:**TAO**(窗口/菜单/托盘,源自 winit 的分支)与 **WRY**(跨平台 WebView
抽象层)。WRY 的职责官方写得很清楚:*"Tauri uses WRY as the abstract layer responsible
to determine which webview is used (and how interactions are made)."*

### 2. 落到哪个引擎:一个平台一份

| 平台 | 引擎 |
| --- | --- |
| macOS / iOS | WKWebView |
| Windows | WebView2(Microsoft Edge Chromium) |
| Linux | WebKitGTK(WRY 要求 webkit2gtk-4.1;Tauri v1 用 4.0) |
| Android | Android System WebView |

即 **5 个平台、4 个不同引擎**(macOS 与 iOS 共用 WKWebView)。带来两件事:

1. 包体积小 —— 官方原话 *"They are very small because they use the OS's webview. They
   do not ship a runtime since the final binary is compiled from Rust."*
2. **引擎碎片化** —— 同一份前端要在 4 个引擎上表现一致,这是选 Tauri 最该先验证的成本项。

### 3. Electron 的分层:main 与 renderer 不可互换

官方原话:*"Electron's main and renderer process have distinct responsibilities and are
not interchangeable. This means it is not possible to access the Node.js APIs directly
from the renderer process, nor the HTML Document Object Model (DOM) from the main process."*

```
renderer(网页,只有 DOM)
   │  window.bridge.readBattery()
preload(在 renderer 里、网页加载前执行,被授予 Node 能力)
   │  ipcRenderer.invoke('read-battery')
   │  ← 参数经 Structured Clone Algorithm 序列化
main(ipcMain.handle,有 Node 与系统能力)
```

官方还有一条历史注脚:*"Renderer processes can be spawned with a full Node.js environment
for ease of development. Historically, this used to be the default, but this feature was
disabled for security reasons."*

### 4. 一次调用跨几段

| 架构 | 路径 | 段数 |
| --- | --- | --- |
| Tauri | WebView JS → JS API(经 webview 的 message passing)→ Rust core | **3** |
| Electron | WebView JS → preload → ipcRenderer → IPC 序列化 → ipcMain handler | **5** |

自检把两条路径的**差集**也断言了出来:Electron 恰好比 Tauri 多出 `preload` /
`ipcRenderer` / `IPC 序列化` / `ipcMain handler`。多出来的不是冗余,而是**分工** ——
Electron 需要显式中间人来裁剪能力边界。

### 5. IPC 能传什么:Structured Clone 的硬限制

`ipcRenderer` 文档明确:参数按 **Structured Clone Algorithm** 序列化(与
`window.postMessage` 一致),"so prototype chains will not be included"。

| 负载 | 能否过 IPC | 依据 |
| --- | --- | --- |
| 普通对象 / 数组 / Map / Set / TypedArray / 原始值 | ✅ | Structured Clone 支持 |
| **类实例** | ⚠️ 能过但**丢原型链** | "prototype chains will not be included" |
| Function / Promise / Symbol / WeakMap / WeakSet | ❌ 抛异常 | 文档点名的 5 类 |
| ImageBitmap / File / DOMMatrix | ❌ 抛异常 | main 进程没有 DOM,无法解码 |

结论:**IPC 只能传数据,传不了行为**;类实例过桥后方法消失,是个典型的静默陷阱。

### 6. 安全模型:声明式能力 vs 手工裁剪

- **Tauri v2** —— 权限写在能力(capability)配置里,运行时由 Rust core 校验。前端即便
  拿到 `invoke`,未授权的命令照样被拒,校验点在**宿主侧**。
- **Electron** —— 边界靠 preload + `contextBridge` 手工裁剪,官方连续给出三条警告:
  *"You never want to directly expose the entire ipcRenderer module via preload. This would
  give your renderer the ability to send arbitrary IPC messages to the main process, which
  becomes a powerful attack vector for malicious code."*;不要把 `event` 参数透传给网页;
  回调要包一层。

沙箱方面(Electron 20 起 renderer 默认开):Chromium 的沙箱覆盖 *"most processes other
than the main process… renderer processes, as well as utility processes such as the audio
service, the GPU service and the network service."* 沙箱下 preload 只能 require 一个子集
(`electron` 的 contextBridge/crashReporter/ipcRenderer/nativeImage/webFrame/webUtils、
`events`、`timers`、`url` 及对应 `node:` 形式),外加 `Buffer`/`process`/
`clearImmediate`/`setImmediate` 的 polyfill;因为 `require` 是受限 polyfill,**preload
不能靠 CommonJS 拆多文件**,要拆必须上打包器。另外官方明确:`nodeIntegration: true`
会**关掉沙箱** —— 二者只能取其一。

### 7. 内容怎么送到 WebView

- **Tauri** —— README 特意写了一条 *"Native WebView Protocol (tauri doesn't create a
  localhost http(s) server to serve the WebView contents)"*:资源在构建期被
  `tauri-codegen` 嵌入/哈希/压缩,运行时走原生 WebView 协议,**不监听任何本机端口**。
- **Electron** —— 常见做法是 `file://` 或本地开发服务器;若启了本地服务,本机其它进程
  就能探测到该端口,属于攻击面的一部分。

## 对比 / 选型

| 维度 | Tauri | Electron |
| --- | --- | --- |
| 浏览器引擎 | 系统 WebView(4 个引擎) | 自带 Chromium(1 个引擎) |
| 是否携带运行时 | ❌ 不携带,二进制由 Rust 编译 | ✅ 携带 Chromium + Node |
| 后端语言 | Rust | Node.js |
| 一次调用段数 | 3 | 5 |
| 权限模型 | 声明式 capability,宿主侧校验 | preload + contextBridge 手工裁剪 |
| 内容分发 | 原生 WebView 协议,无本机端口 | `file://` 或本地服务器(可能有端口) |
| 渲染一致性风险 / 适合 | 高(取决于系统 WebView 版本);适合体积敏感、要复用系统能力、能接受引擎差异 | 低(引擎随包固定);适合渲染一致性优先、工具链成熟度优先 |

## 环境准备

- 操作系统:任意(自检脚本纯标准库);Python 3.8+。运行真实应用需 Rust 工具链(Tauri)
  或 Node.js(Electron),本项目未安装,仅阅读代码

## 运行方式

```bash
cd python && python3 main.py          # 52 项断言
cd ts  && npm i @tauri-apps/api && npx tsc --noEmit   # Tauri 侧 TS
cd js  && npm i electron && npx electron .            # Electron 侧
```

## 关键代码片段

```ts
// Tauri:一条 invoke 直达 Rust core
import { invoke } from '@tauri-apps/api/core';
export async function readBattery(): Promise<BatteryInfo> {
  return await invoke<BatteryInfo>('read_battery');
}
```

```js
// Electron:必须经过 preload 的裁剪;main 侧注册要在 loadFile 之前
contextBridge.exposeInMainWorld('bridge', {
  readBattery: () => ipcRenderer.invoke('read-battery'),
});
ipcMain.handle('read-battery', async () => ({ level: 87, charging: false }));
```

## 性能与边界

- **段数不等于延迟**:本 demo 只断言消息路径的段数与可传负载,不对文档未给出的延迟数值
  下结论;真实延迟取决于引擎实现与负载大小。
- **体积**:Tauri 不携带运行时(官方表述),基线由系统 WebView 决定;Electron 需随包
  分发 Chromium + Node。
- **引擎边界**:Tauri 在 Linux 依赖 WebKitGTK 版本;移动端还需 `cargo-mobile2` 模板与
  一组 `WRY_ANDROID_*` 环境变量。
- **Electron 进程边界**:CPU 密集/易崩溃/不可信代码应放进 `UtilityProcess`(Node 环境,
  可通过 MessagePorts 与 renderer 直接通话),不要塞进 main 进程。

## 注意事项与常见坑

1. **把 Tauri 当"轻量内核包装"或"VM"**:官方直接否定了这两种理解。
2. **忽略系统 WebView 的版本差异**:同一段 CSS/JS 在 4 个引擎上行为不同,要分别验证。
3. **Electron 暴露整个 `ipcRenderer` / 开 `nodeIntegration`**:前者被官方点名为 attack vector,后者会顺带**关掉沙箱**。
4. **在 preload 里用 CommonJS 拆文件**:沙箱下 `require` 是受限 polyfill,拆不了。
6. **跨 IPC 传类实例或 DOM 对象**:前者能过桥但原型链被剥掉,后者直接抛异常(main 无 DOM 无法解码)。

## 参考资料(实际阅读过的权威来源)

- [Tauri · Architecture](https://v2.tauri.app/concept/architecture/)
  —— 分层与 crate 职责、TAO/WRY 定位、"not a kernel wrapper / not a VM"、体积与运行时说明
- [tauri-apps/tauri · README](https://github.com/github/tauri)
  —— WRY 在各平台落到哪个系统 WebView、平台版本要求、原生 WebView 协议(不启 localhost 服务)
- [tauri-apps/wry · Platform Considerations](https://github.com/tauri-apps/wry)
  —— Linux 需 WebKitGTK 4.1、Windows 用 WebView2、macOS 原生 WebKit、Android 变量要求
- [Electron · Process Model](https://www.electronjs.org/docs/latest/tutorial/process-model)
  —— main/renderer 职责不可互换、preload 与 contextIsolation、utility process
- [Electron · Process Sandboxing](https://www.electronjs.org/docs/latest/tutorial/sandbox)
  —— 沙箱覆盖范围、Electron 20 起默认开启、沙箱 preload 的 Node 子集、`nodeIntegration` 的代价
- [Electron · ipcRenderer](https://www.electronjs.org/docs/latest/api/ipc-renderer)
  —— invoke/send/postMessage 语义、Structured Clone 限制与被拒类型清单
- [Electron · Using Preload Scripts](https://www.electronjs.org/docs/latest/tutorial/tutorial-preload)
  —— contextBridge 用法与"不要暴露整个 ipcRenderer"的安全警告
