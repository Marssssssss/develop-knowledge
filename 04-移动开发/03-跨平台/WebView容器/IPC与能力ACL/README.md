# Tauri 的 IPC 协议与 capability/ACL 判定

> 目录：`04-移动开发/03-跨平台/WebView容器/IPC与能力ACL/`
> 语言：Python（`python/main.py` + `python/selfcheck_acl.py`，**40 断言实跑全绿**）/ Go（`go/acl.go` + `go/main.go` 人工审查 + bracket_check + go_sanity + go_crossref）

## 一、简介

Tauri 与 Electron 最大的架构差别是**权限模型**：Electron 靠 preload 脚本手工裁剪 `ipcRenderer`，Tauri 2 则要求前端的每个 `invoke` 都过一遍 **ACL（capability → permission → command scope）**。

本 demo 复刻这条判定链的三段：

1. **命令键归一化** —— `plugin:<name>|<command>` 是怎么拼出来的；
2. **`resolve_access`** —— deny 优先、origin × (window | webview) 的判定顺序；
3. **URL 模式** —— 远程来源的 `ExecutionContext::Remote` 怎么匹配。

## 二、原理

### 2.1 命令键的三种写法

`resolved.rs` 在把权限展开成命令表时对 key 分三类处理：

| 插件 key | 结果 | 例子 |
| --- | --- | --- |
| `APP_ACL_KEY`（`__app-acl__`） | **不加前缀** | `do_thing` |
| `core:<name>` | 剥掉 `core:` 再拼 | `plugin:event|emit` |
| 其它插件 | 直接拼 | `plugin:fs|read_file` |

所以内核插件和用户插件在**命令名空间里是同构的**，只有 app 自己的命令是裸名。

### 2.2 全局 scope 与命令 scope 的分流

解析权限时：

```rust
if commands.allow.is_empty() && commands.deny.is_empty() {
    // global scope：这条权限只是给插件声明作用域，不授权任何命令
    global_scope.entry(key).or_default().push(scope);
} else {
    // command scope：有 scope 就分配一个 scope_id
    let scope_id = if scope.allow.is_some() || scope.deny.is_some() { ... };
}
```

也就是说：**一条 permission 只有写了 `commands.allow/deny` 才会授权命令**；只写 `scope` 的那类权限（比如限制路径）不会凭空放开命令。

### 2.3 `resolve_access`：deny 只看 origin

```rust
if self.denied_commands.get(command)
    .map(|resolved| resolved.iter().any(|cmd| origin.matches(&cmd.context)))
    .is_some() { None }                       // ① deny 命中即拒绝
else {
    self.allowed_commands.get(command).and_then(|resolved| {
        let resolved_cmds = resolved.iter().filter(|cmd| {
            origin.matches(&cmd.context)      // ② 来源必须匹配
                && (cmd.webviews.iter().any(|w| w.matches(webview))
                    || cmd.windows.iter().any(|w| w.matches(window)))  // ③ 窗口或 webview 任一匹配
        }).cloned().collect::<Vec<_>>();
        if resolved_cmds.is_empty() { None } else { Some(resolved_cmds) }
    })
}
```

三个反直觉点：

- **deny 的判据只有 origin**，不看 window/webview —— 一旦来源被 deny，窗口再精确也白搭；
- **window 与 webview 是「或」**，命中其一即可；
- **origin 与 (window|webview) 是「与」**，二者都要成立。

### 2.4 Origin × ExecutionContext

```rust
match (self, context) {
    (Self::Local, ExecutionContext::Local) => true,
    (Self::Remote { url }, ExecutionContext::Remote { url: url_pattern }) => url_pattern.test(url),
    _ => false,
}
```

**本地来源不会命中「只给远程 URL」的能力**，反之亦然 —— 这是一对容易写反的判定。

远程 URL 用的是 WHATWG `urlpattern` crate（`RemoteUrlPattern`）。`acl/mod.rs` 的三个单测把规则钉死了：

| 模式 | 命中 | 不命中 |
| --- | --- | --- |
| `http://*` | `http://tauri.app/path`、`http://localhost/path?q=1` | 换 scheme |
| `http://*.tauri.app` | `http://api.tauri.app/path` | **`http://tauri.app` 本身**、localhost |
| `http://localhost/*` | `http://localhost/path` | |
| `*://localhost` | http / https / `custom://` | |

### 2.5 IPC 请求头

前端 `invoke` 最终变成一次自定义协议请求，`parse_invoke_request` 从头部取值：

| 头 | 含义 |
| --- | --- |
| `Tauri-Callback` | 成功回调的 id，必须是**数字字符串** |
| `Tauri-Error` | 失败回调的 id，同样必须是数字字符串 |
| `Tauri-Invoke-Key` | 调用键 |
| `Tauri-Response` | 响应标记：`ok` / `error` |

源码单测用的正是 `callback = 12378123`、`error = 6243`。头值不是数字会直接报错（`Tauri callback header value must be a numeric string`）。

## 三、对比

| 维度 | Electron | Tauri 2 |
| --- | --- | --- |
| 权限声明 | 靠 preload 手工裁剪 | capability → permission → command 三级 TOML |
| 维度 | 通常只有「能否调用」 | **origin（本地/远程 URL 模式）+ window + webview** 三维 |
| deny 语义 | 无内建 | deny 只看 origin，命中即整体拒绝 |
| 命令名空间 | 字符串通道名 | `plugin:<name>|<cmd>` 归一化 |
| 远程内容 | 由开发者控制 | 需要显式给 `Remote { url }` 上下文 |

## 四、环境

- Python 3.13（标准库）
- Go 1.21+（无本机工具链，Go 版只做人工审查与静态检查）

## 五、运行

```bash
cd python && python main.py              # 命令键 / URL 模式 / resolve_access 对照
cd python && python selfcheck_acl.py     # 40 项断言
```

## 六、关键代码

| 文件 | 对应源码 |
| --- | --- |
| `python/main.py:command_key` | `crates/tauri-utils/src/acl/resolved.rs:171-182` |
| `python/main.py:RemoteUrlPattern` | `crates/tauri-utils/src/acl/mod.rs:280-320`（含 `:427-465` 单测） |
| `python/main.py:Origin.matches` | `crates/tauri/src/ipc/authority.rs:57-67` |
| `python/main.py:RuntimeAuthority.resolve_access` | `crates/tauri/src/ipc/authority.rs:439-470` |
| `python/main.py:parse_invoke_request` | `crates/tauri/src/ipc/protocol.rs:434-545` |

## 七、性能边界

- ACL 在**构建期解析**（`Resolved` 是一张 `BTreeMap<命令, Vec<ResolvedCommand>>`），运行期只做一次查表 + 少量过滤。
- `resolve_access` 的代价随「同一命令被多少 capability 声明过」线性增长；deny 表也要扫一遍。
- URL 匹配用的是编译好的 `urlpattern`，不是每次都解析字符串。

## 八、坑

1. **`deny` 不区分 window/webview**：只想「禁止某个窗口」是做不到的，deny 的粒度是 origin。
2. **`*.tauri.app` 不匹配裸域名** `tauri.app`（子域通配符要求至少一级子域）。
3. **窗口与 webview 是或关系**，只写 `windows` 但传入的是 webview 标签也能过（反之亦然）。
4. **本地来源吃不到只给远程 URL 的能力**，把远程能力当「通用能力」用会静默失效。
5. **只有写了 `commands.allow/deny` 的权限才授权命令**，纯 `scope` 权限只贡献作用域。
6. 回调头必须是**数字字符串**，传 UUID 会在解析阶段直接失败。
7. 命令键里的 `core:` 前缀会被剥掉 —— 手抄命令名时要写 `plugin:event|emit` 而不是 `core:event|emit`。

## 九、参考资料（实际读过）

- `tauri-apps/tauri@dev` — `crates/tauri/src/ipc/authority.rs`、`crates/tauri/src/ipc/protocol.rs`、`crates/tauri/src/ipc/command.rs`、`crates/tauri/src/ipc/channel.rs`、`crates/tauri-utils/src/acl/resolved.rs`、`crates/tauri-utils/src/acl/capability.rs`、`crates/tauri-utils/src/acl/identifier.rs`、`crates/tauri-utils/src/acl/mod.rs`
  （经 `cdn.jsdelivr.net/gh/tauri-apps/tauri@dev/...` 抓取）
- WHATWG `urlpattern` crate（`RemoteUrlPattern` 的底层实现）：`tauri-utils/src/acl/mod.rs` 里 `FromStr` 直接调用 `UrlPatternInit::parse_constructor_string`
- 窗口标签的精确匹配语义（`WindowPattern::matches`）未取到源码，本 demo 按精确相等建模，断言只覆盖已读到的 deny/origin/window-webview 结构
