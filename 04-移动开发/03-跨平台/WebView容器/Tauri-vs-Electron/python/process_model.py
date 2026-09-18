"""Tauri 与 Electron 的架构差异模型(纯标准库)。

权威依据(2026-09-18 实读):
  * v2.tauri.app/concept/architecture/ —— Tauri 的分层:crate 组成、WRY/TAO 的位置、
    "app 通过 message passing 控制系统"、"They are very small because they use the OS's
    webview. They do not ship a runtime since the final binary is compiled from Rust."
  * github.com/tauri-apps/tauri 的 README —— WRY 在各平台落到哪个系统 WebView
    (WKWebView / WebView2 / WebKitGTK / Android System WebView)、
    "Native WebView Protocol (tauri doesn't create a localhost http(s) server ...)"
  * github.com/tauri-apps/wry 的 Platform Considerations —— Linux 需要 WebKitGTK 4.1、
    Windows 用 WebView2(Edge Chromium)、macOS 原生 WebKit、移动端走 cargo-mobile2
  * Electron 官方文档(process-model / sandbox / ipc-renderer / tutorial-preload)——
    main 与 renderer 职责不可互换、nodeIntegration 与 sandbox 的取舍、
    contextIsolation + contextBridge、沙箱 preload 的 Node 子集、
    ipcRenderer.invoke/send 与 **Structured Clone Algorithm** 的限制

模型只做"数得清"的比较:进程构成、一次调用的跨界跳数、负载可接受性、引擎数量。
不比较任何未在文档中给出的性能数值。
"""

# ---------------------------------------------------------------------------
# 一、进程与运行时构成
# ---------------------------------------------------------------------------

TAURI = {
    "core_process": "Rust 二进制(编译产物,不随包携带运行时)",
    "window_layer": "TAO(窗口/菜单/托盘,源自 winit 的分支)",
    "webview_layer": "WRY(跨平台 WebView 抽象层,决定用哪个系统引擎)",
    "bundled_engine": False,        # 不打包浏览器引擎
    "ships_runtime": False,         # 官方原话:不携带 runtime,最终二进制由 Rust 编译而来
    "serves_over_localhost": False,  # 官方原话:不创建 localhost http(s) 服务器
    "ipc_mechanism": "WebView 与宿主之间的 message passing(JS API ↔ Rust API)",
    "engines": {
        "macOS": "WKWebView",
        "iOS": "WKWebView",
        "Windows": "WebView2(Edge Chromium)",
        "Linux": "WebKitGTK",
        "Android": "Android System WebView",
    },
}

ELECTRON = {
    "core_process": "main 进程(Node 环境,应用生命周期与原生能力)",
    "window_layer": "Chromium 的窗口实现",
    "webview_layer": "自带的 Chromium(每个窗口一个 renderer 进程)",
    "bundled_engine": True,
    "ships_runtime": True,
    "serves_over_localhost": True,   # 常见做法:file:// 或本地开发服务器
    "ipc_mechanism": "ipcRenderer / ipcMain(经 preload + contextBridge 暴露)",
    "satellite_processes": ["audio service", "GPU service", "network service"],
    "engines": {"any": "bundled Chromium"},
}

# 一次"页面 → 宿主"调用的跨界跳数(按各自主文档描述的消息路径数)
TAURI_CALL_HOPS = ["WebView JS", "JS API(api 包,走 webview 的 message passing)", "Rust core"]
ELECTRON_CALL_HOPS = ["WebView JS", "preload(contextBridge 暴露的函数)", "ipcRenderer",
                      "IPC 序列化(Structured Clone)", "ipcMain handler"]

# ipcRenderer 文档明确列出"发送会抛异常"的 JS 值
ELECTRON_REJECTED = ("Function", "Promise", "Symbol", "WeakMap", "WeakSet")
# 文档另外点名的非标准类型(主进程无法解码)
ELECTRON_REJECTED_DOM = ("ImageBitmap", "File", "DOMMatrix")
# 这些能过 Structured Clone,但文档提醒"prototype chains will not be included"
ELECTRON_LOSES_PROTOTYPE = ("class instance", "Date 子类实例", "带自定义原型的对象")


def engine_count(arch):
    return len(set(arch["engines"].values()))


def engine_fragmentation(arch):
    """同一份前端代码需要面对多少个不同的引擎实现。"""
    return len(arch["engines"])


def call_hops(arch):
    return len(TAURI_CALL_HOPS if arch is TAURI else ELECTRON_CALL_HOPS)


def accepts(js_value_kind):
    """按 ipcRenderer 文档判定该类型的参数能否过 IPC。"""
    if js_value_kind in ELECTRON_REJECTED or js_value_kind in ELECTRON_REJECTED_DOM:
        return False
    return True


def privileges_preload_needs(sandboxed):
    """沙箱开启时 preload 只能拿到 Node 的一个子集(文档给出的白名单)。"""
    full = {"fs", "path", "child_process", "electron", "Buffer", "process"}
    sandboxed_subset = {"electron", "events", "timers", "url",
                        "node:events", "node:timers", "node:url",
                        "Buffer", "process", "clearImmediate", "setImmediate"}
    return sandboxed_subset if sandboxed else full


def content_serving_ports(arch):
    """为 WebView 提供内容而需要监听的本机端口数。"""
    return 1 if arch["serves_over_localhost"] else 0
