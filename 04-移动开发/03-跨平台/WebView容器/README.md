# WebView 容器

「外壳程序 + Web 前端」这一类跨平台方案的统称:业务写在 HTML/CSS/JS 里,原生侧只提供
一个承载 Web 内容的容器,以及容器与前端之间的桥。选型的分水岭在于**渲染引擎从哪来**——
用系统自带的 WebView,还是把整个浏览器内核打进安装包。

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [Tauri-vs-Electron/](./Tauri-vs-Electron/) | 系统 WebView vs 自带 Chromium 的架构与权限模型对比 |

## 已完成 demo

| ID | 路径 | 知识点 | 语言 |
| --- | --- | --- | --- |
| 341 | `Tauri-vs-Electron/` | 系统 WebView(WKWebView / WebView2 / WebKitGTK / Android System WebView,5 平台 4 引擎)vs 自带 Chromium;不携带运行时 vs 携带运行时;一次调用 3 段 vs 5 段;Structured Clone 负载限制;capability 声明式权限 vs preload + `contextBridge` 手工裁剪 | TypeScript / JavaScript / Python |

## 待研究

- [ ] Tauri 2.x 移动端(Android/iOS)支持细节与 JNI / UniFFI 桥
- [ ] Electron 沙箱(renderer sandbox)与 `nodeIntegration` 的互斥关系实测
- [ ] WebView 容器下的前端热更新与签名校验策略
- [ ] Capacitor / Cordova 插件模型(与 Tauri capability 的对比)
