"""Tauri / Electron 架构差异的自检(纯标准库,直接 python3 main.py 运行)。

断言覆盖四件事:是否自带引擎与运行时、一次调用的跨界跳数、
IPC 负载的可接受性(Structured Clone 的限制)、以及 WebView 引擎的数量。
"""

import sys

from process_model import (ELECTRON, ELECTRON_CALL_HOPS, ELECTRON_LOSES_PROTOTYPE,
                           ELECTRON_REJECTED, ELECTRON_REJECTED_DOM, TAURI,
                           TAURI_CALL_HOPS, accepts, call_hops,
                           content_serving_ports, engine_count,
                           engine_fragmentation, privileges_preload_needs)

PASS = 0
FAIL = 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s | %s" % (label, detail))


# ---------------- 1. 引擎与运行时:talent 是"用系统的",Electron 是"自带一套" ----------------
check("Tauri 不打包浏览器引擎", TAURI["bundled_engine"] is False)
check("Electron 打包浏览器引擎", ELECTRON["bundled_engine"] is True)
check("Tauri 不携带运行时(二进制由 Rust 编译而来)", TAURI["ships_runtime"] is False)
check("Electron 携带运行时(Chromium + Node)", ELECTRON["ships_runtime"] is True)
check("Tauri 明确不创建 localhost http(s) 服务器",
      TAURI["serves_over_localhost"] is False)
check("因此 Tauri 的 WebView 内容服务端口数为 0", content_serving_ports(TAURI) == 0)
check("Electron 常见做法会监听 1 个本机端口", content_serving_ports(ELECTRON) == 1)
check("两者内容服务端口数不同", content_serving_ports(TAURI) != content_serving_ports(ELECTRON))

# ---------------- 2. WebView 引擎数量:一致 vs 碎片化 ----------------
check("Tauri 覆盖 5 个平台(说明同一份前端要面对 5 份引擎实现)",
      engine_fragmentation(TAURI) == 5, str(engine_fragmentation(TAURI)))
check("Tauri 的引擎去重后仍是 4 个", engine_count(TAURI) == 4, str(engine_count(TAURI)))
check("Tauri 引擎清单精确匹配官方文档",
      TAURI["engines"] == {"macOS": "WKWebView", "iOS": "WKWebView",
                           "Windows": "WebView2(Edge Chromium)",
                           "Linux": "WebKitGTK",
                           "Android": "Android System WebView"},
      str(TAURI["engines"]))
check("macOS 与 iOS 共用一个引擎(WKWebView)", TAURI["engines"]["macOS"] == TAURI["engines"]["iOS"])
check("Electron 全平台只有一个引擎", engine_count(ELECTRON) == 1,
      str(engine_count(ELECTRON)))
check("去重后 Tauri 是 4 个引擎(macOS 与 iOS 共用 WKWebView)、Electron 是 1 个",
      engine_count(ELECTRON) * 4 == engine_count(TAURI), str(engine_count(TAURI)))

# ---------------- 3. 一次调用的跨界跳数 ----------------
check("Tauri 一次调用跨 3 段", call_hops(TAURI) == 3, str(call_hops(TAURI)))
check("Electron 一次调用跨 5 段", call_hops(ELECTRON) == 5, str(call_hops(ELECTRON)))
check("Electron 的跳数比 Tauri 多 2 段",
      call_hops(ELECTRON) - call_hops(TAURI) == 2)
check("Tauri 的最后一环是 Rust core",
      TAURI_CALL_HOPS[-1] == "Rust core", TAURI_CALL_HOPS[-1])
check("Electron 的最后一环是 ipcMain handler",
      ELECTRON_CALL_HOPS[-1] == "ipcMain handler", ELECTRON_CALL_HOPS[-1])
check("Electron 路径里含一次显式序列化",
      any("序列化" in h for h in ELECTRON_CALL_HOPS), str(ELECTRON_CALL_HOPS))
check("Tauri 路径里不含显式序列化环节",
      not any("序列化" in h for h in TAURI_CALL_HOPS), str(TAURI_CALL_HOPS))
check("Electron 的路径恰好比 Tauri 多出 preload / ipcRenderer / 序列化 / ipcMain",
      set(ELECTRON_CALL_HOPS) - set(TAURI_CALL_HOPS)
      == {"preload(contextBridge 暴露的函数)", "ipcRenderer",
          "IPC 序列化(Structured Clone)", "ipcMain handler"},
      str(set(ELECTRON_CALL_HOPS) - set(TAURI_CALL_HOPS)))
check("两条路径的起点都是 WebView 里的 JS",
      ELECTRON_CALL_HOPS[0].endswith("JS") and TAURI_CALL_HOPS[0].endswith("JS"))

# ---------------- 4. IPC 负载的可接受性(Structured Clone 限制) ----------------
for kind in ELECTRON_REJECTED:
    check("%s 不能过 IPC(文档明说会抛异常)" % kind, accepts(kind) is False)
for kind in ELECTRON_REJECTED_DOM:
    check("%s 不能过 IPC(主进程无法解码)" % kind, accepts(kind) is False)
check("被拒类型共 8 种", len(ELECTRON_REJECTED) + len(ELECTRON_REJECTED_DOM) == 8,
      str(len(ELECTRON_REJECTED) + len(ELECTRON_REJECTED_DOM)))
for kind in ("plain object", "Array", "Map", "Set", "Uint8Array", "string", "number", None):
    check("%s 可以过 IPC" % (kind if kind else "null"), accepts(kind) is True)
check("类实例能过但会丢掉原型链",
      accepts("class instance") is True and "class instance" in ELECTRON_LOSES_PROTOTYPE)
check("因此类实例的方法在另一侧不存在(只能传数据,不能传行为)",
      accepts("Function") is False and accepts("class instance") is True)

# ---------------- 5. 沙箱对 preload 能力的影响 ----------------
sandboxed = privileges_preload_needs(sandboxed=True)
unsandboxed = privileges_preload_needs(sandboxed=False)
check("沙箱 preload 仍能拿到 electron / Buffer / process",
      {"electron", "Buffer", "process"} <= sandboxed, str(sorted(sandboxed)))
check("沙箱 preload 拿不到完整 fs / child_process",
      not ({"fs", "child_process"} & sandboxed), str(sorted(sandboxed)))
check("非沙箱 preload 有完整 Node(含 fs/child_process)",
      {"fs", "path", "child_process"} <= unsandboxed, str(sorted(unsandboxed)))
check("沙箱开启时 require 是受限 polyfill 故 preload 不能拆成多个 CommonJS 文件",
      "node:events" in sandboxed and "node:path" not in sandboxed)
check("非沙箱独有、沙箱拿不到的正是 fs / path / child_process",
      unsandboxed - sandboxed == {"fs", "path", "child_process"},
      str(unsandboxed - sandboxed))

# ---------------- 6. 进程构成 ----------------
check("Electron 的 main 与 renderer 职责不可互换(文档原话)",
      "main 进程" in ELECTRON["core_process"] and "renderer" in ELECTRON["webview_layer"])
check("Electron 另有 3 类 utility 进程(audio/GPU/network)",
      len(ELECTRON["satellite_processes"]) == 3, str(ELECTRON["satellite_processes"]))
check("这 3 类进程都在 Chromium 沙箱覆盖范围内(main 除外)",
      set(ELECTRON["satellite_processes"]) == {"audio service", "GPU service",
                                               "network service"})
check("Tauri 的分层是 TAO(窗口) + WRY(WebView 抽象) + Rust core",
      "TAO" in TAURI["window_layer"] and "WRY" in TAURI["webview_layer"])
check("Tauri 自己不做窗口/WebView 的底层实现(直接用 WRY 与 TAO)",
      "WRY" in TAURI["webview_layer"] and "TAO" in TAURI["window_layer"])

print("=" * 62)
print("Tauri/Electron 架构自检:通过 %d 项,失败 %d 项" % (PASS, FAIL))
if FAILED:
    for line in FAILED:
        print("  [FAIL] " + line)
    sys.exit(1)
print("全部通过")
