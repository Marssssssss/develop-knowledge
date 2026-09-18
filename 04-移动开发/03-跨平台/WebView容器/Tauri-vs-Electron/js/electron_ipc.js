// Electron 侧的三段式 IPC:main(特权) / preload(中间人) / renderer(网页)。
//
// 权威依据:electronjs.org 的 process-model、sandbox、ipc-renderer、tutorial-preload
// 四篇官方文档(2026-09-18 实读)。
//
// 官方对职责划分的原话:"Electron's main and renderer process have distinct
// responsibilities and are not interchangeable. This means it is not possible to
// access the Node.js APIs directly from the renderer process, nor the HTML Document
// Object Model (DOM) from the main process."
//
// 本机未安装 Electron,不做运行,供人工审查。

// ===========================================================================
// 第一段:main 进程(有 Node 能力,没有 DOM)
// ===========================================================================
const path = require('node:path');
const { app, BrowserWindow, ipcMain } = require('electron/main');

function createWindow() {
  const win = new BrowserWindow({
    width: 900,
    height: 600,
    webPreferences: {
      // preload 在 renderer 里、网页加载之前执行,是唯一"两边都沾"的位置
      preload: path.join(__dirname, 'preload.js'),
      // 下面两个开关的默认值就是安全配置,这里显式写出来便于对照:
      //   contextIsolation: true  —— preload 与网页不在同一个 world
      //   nodeIntegration: false  —— renderer 不拿 Node 环境
      // 注意官方原话:"Enabling Node.js integration for a renderer process by
      // setting nodeIntegration: true disables the sandbox for the process."
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  win.loadFile('index.html');
}

// 注册必须在 loadFile 之前,官方注释:"We do this before loading the HTML file so
// that the handler is guaranteed to be ready before you send out the invoke call."
ipcMain.handle('read-battery', async () => {
  // 这里才碰得到 Node / 系统能力
  return { level: 87, charging: false };
});

// 单向广播:主进程主动推给某个窗口
function pushProgress(win, done, total) {
  win.webContents.send('download-progress', { done, total });
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

// ===========================================================================
// 第二段:preload(在 renderer 里跑,但被授予更多权限)
// ===========================================================================
//
// preload.js 的内容:
//
//   const { contextBridge, ipcRenderer } = require('electron');
//
//   contextBridge.exposeInMainWorld('bridge', {
//     readBattery: () => ipcRenderer.invoke('read-battery'),
//     onProgress: (cb) => {
//       // 官方警告:不要把 event 参数透传给渲染进程
//       const wrapped = (_event, payload) => cb(payload);
//       ipcRenderer.on('download-progress', wrapped);
//       return () => ipcRenderer.off('download-progress', wrapped);
//     },
//   });
//
// 三条官方硬性要求:
//   1) 用 contextBridge 而不是直接挂 window。默认 contextIsolation 下
//      `window.myAPI = {...}` 在网页里是 undefined;
//   2) 不要把整个 ipcRenderer 暴露出去 —— "This would give your renderer the
//      ability to send arbitrary IPC messages to the main process, which becomes a
//      powerful attack vector for malicious code.";
//   3) 回调要包一层,不要把 event 参数暴露给网页 —— 那个对象带着危险 API。
//
// 沙箱打开时 preload 只能 require 一个子集:electron(contextBridge、crashReporter、
// ipcRenderer、nativeImage、webFrame、webUtils)、events、timers、url 以及对应的
// node: 形式;并 polyfill 了 Buffer / process / clearImmediate / setImmediate。
// 因为 require 是受限 polyfill,**不能**用 CommonJS 把 preload 拆成多个文件 ——
// 要拆就用打包器。

// ===========================================================================
// 第三段:renderer(网页,只有 DOM)
// ===========================================================================
//
//   const info = await window.bridge.readBattery();
//   const off = window.bridge.onProgress(({ done, total }) => render(done, total));
//
// 负载限制(ipcRenderer 文档口径):
//   * 参数用 **Structured Clone Algorithm** 序列化(与 window.postMessage 相同),
//     因此 "prototype chains will not be included" —— 类实例过去之后只剩数据;
//   * 发送 Function / Promise / Symbol / WeakMap / WeakSet 会抛异常;
//   * 发送 ImageBitmap / File / DOMMatrix 这类 DOM 对象也会抛异常,
//     因为 main 进程没有 DOM 支持、无法解码;
//   * 需要传 MessagePort 用 ipcRenderer.postMessage;
//   * 不需要应答用 ipcRenderer.send,需要应答用 ipcRenderer.invoke(返回 Promise)。

// ===========================================================================
// 附:进程构成与沙箱覆盖面(官方 sandbox 文档)
// ===========================================================================
//
// "In Chromium, sandboxing is applied to most processes other than the main process.
//  This includes renderer processes, as well as utility processes such as the audio
//  service, the GPU service and the network service."
//
// "Starting from Electron 20, the sandbox is enabled for renderer processes without
//  any further configuration."
//
// 需要跑 CPU 密集 / 易崩溃 / 不可信代码时,官方建议用 UtilityProcess API 起一个
// Node 环境的子进程(它还能通过 MessagePorts 与 renderer 直接通话),
// 而不是把这类活放在 main 进程里。
module.exports = { createWindow, pushProgress };
