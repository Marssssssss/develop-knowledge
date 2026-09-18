// Tauri v2 侧的 IPC 用法:WebView 里的 JS 通过 message passing 调 Rust core 的命令。
//
// 权威依据:v2.tauri.app/concept/architecture/ 与 tauri-apps 的 JS API(api 包)。
// 官方对该包的原话:"It uses the message passing of webviews to their hosts."
//
// 本机未安装 Tauri 工具链,不做编译,供人工审查。

import { invoke } from '@tauri-apps/api/core';
import { listen, emit } from '@tauri-apps/api/event';
import { getCurrentWindow } from '@tauri-apps/api/window';

// ---------------------------------------------------------------------------
// 1. 命令调用:WebView JS -> Rust core,一次请求/应答
// ---------------------------------------------------------------------------

interface BatteryInfo {
  level: number;
  charging: boolean;
}

/** Rust 侧 #[tauri::command] fn read_battery() -> Result<BatteryInfo, String> */
export async function readBattery(): Promise<BatteryInfo> {
  return await invoke<BatteryInfo>('read_battery');
}

/**
 * 参数名默认做 camelCase -> snake_case 转换(Rust 侧是 snake_case 形参)。
 * 返回值经 serde 序列化,失败时 Rust 的 Err(String) 会变成这里 catch 到的字符串。
 */
export async function saveNote(title: string, body: string): Promise<void> {
  try {
    await invoke('save_note', { title, body });
  } catch (err) {
    // Rust 侧返回 Err(...) 时,err 就是那个字符串,不是 Error 对象
    console.error('save_note 失败:', err);
    throw err;
  }
}

// ---------------------------------------------------------------------------
// 2. 事件:Rust core 主动推给 WebView(与"命令"方向相反)
// ---------------------------------------------------------------------------

/** Rust 侧用 app.emit("download-progress", payload) 推送 */
export async function watchDownload(): Promise<() => void> {
  const unlisten = await listen<{ done: number; total: number }>(
    'download-progress',
    (event) => {
      // event.payload 是 Rust 侧序列化过来的结构
      console.log(`${event.payload.done}/${event.payload.total}`);
    },
  );
  return unlisten; // 记得在组件卸载时调用,否则监听器泄漏
}

/** 反方向:JS 侧发事件给 Rust(以及给其它监听中的窗口) */
export async function notifyReady(): Promise<void> {
  await emit('ui-ready', { at: Date.now() });
}

// ---------------------------------------------------------------------------
// 3. 权限模型:能力(capability)决定这套 JS API 能不能调
// ---------------------------------------------------------------------------
//
// Tauri v2 的权限是**声明式**的:窗口/路径/命令是否被允许,写在能力配置里,
// 运行时由 core 校验 —— 而不是靠"前端不乱调"。
//
// 也就是说:
//   * 前端即使拿到了 invoke,没授权的命令依然会被 core 拒绝;
//   * 这与 Tauri 官方所说的"直接用 WRY/TAO 做系统调用、不提供 VM 或轻量内核包装"
//     是一致的安全取向 —— 危险动作全部落在 Rust core 这一侧做校验。
//
// 对比 Electron:renderer 侧的能力边界靠 preload + contextBridge **手工裁剪**,
// 官方专门警告过不要把整个 ipcRenderer 暴露出去("You never want to directly
// expose the entire ipcRenderer module via preload")。

// ---------------------------------------------------------------------------
// 4. 窗口操作走 window 命名空间
// ---------------------------------------------------------------------------

export function describeWindow(): string {
  const w = getCurrentWindow();
  return `窗口 ${w.label}`;
}

// ---------------------------------------------------------------------------
// 5. 为什么 Tauri 不启本地服务器
// ---------------------------------------------------------------------------
//
// 官方 README 明确写:"Native WebView Protocol (tauri doesn't create a localhost
// http(s) server to serve the WebView contents)"。
// 前端资源在构建期被 tauri-codegen「Embed, hash, and compress」,运行时通过原生
// WebView 协议直接喂给 WebView —— 因此没有监听端口可供本机其它进程探测。
// 代价是:前端资源必须能在编译期确定(WRY/WebView 的 URL scheme 各平台不同,
// 这也是 WRY 存在的意义 —— 它统一了"用哪个引擎、怎么交互")。
