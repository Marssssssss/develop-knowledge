"""Tauri 2 的 IPC 协议与 capability/ACL 判定模型。

对应源码（tauri-apps/tauri @dev）：
  crates/tauri/src/ipc/protocol.rs   —— 请求头解析、响应 ok/error 标记
  crates/tauri/src/ipc/authority.rs  —— RuntimeAuthority::resolve_access（deny 优先）
  crates/tauri-utils/src/acl/resolved.rs —— 命令键归一化、全局 scope 与命令 scope 分流
  crates/tauri-utils/src/acl/mod.rs  —— APP_ACL_KEY、ExecutionContext、RemoteUrlPattern（含单测）

URL 匹配规则取自 `acl/mod.rs` 里 `url_pattern_*` 三个单测的断言。
"""

APP_ACL_KEY = "__app-acl__"

TAURI_CALLBACK_HEADER_NAME = "Tauri-Callback"
TAURI_ERROR_HEADER_NAME = "Tauri-Error"
TAURI_INVOKE_KEY_HEADER_NAME = "Tauri-Invoke-Key"
TAURI_RESPONSE_HEADER_NAME = "Tauri-Response"
TAURI_RESPONSE_HEADER_OK = "ok"
TAURI_RESPONSE_HEADER_ERROR = "error"


# ------------------------------------------------------------ 命令键归一化


def command_key(plugin_key, command):
    """resolved.rs:171-182 —— 三类写法。"""
    if plugin_key == APP_ACL_KEY:
        return command
    if plugin_key.startswith("core:"):
        return "plugin:{}|{}".format(plugin_key[len("core:"):], command)
    return "plugin:{}|{}".format(plugin_key, command)


# ------------------------------------------------------------ URL 模式


class RemoteUrlPattern:
    """简化版 urlpattern：只实现 `acl/mod.rs` 单测覆盖到的三条规则。"""

    def __init__(self, pattern):
        self.pattern = pattern
        scheme, rest = pattern.split("://", 1)
        self.scheme = scheme
        if "/" in rest:
            host, path = rest.split("/", 1)
            self.path = "/" + path
        else:
            host, self.path = rest, None
        self.host = host

    def test(self, url):
        uscheme, rest = url.split("://", 1)
        if "/" in rest:
            uhost, upath = rest.split("/", 1)
            upath = "/" + upath
        else:
            uhost, upath = rest, "/"
        if self.scheme != "*" and self.scheme != uscheme:
            return False
        if not self._host_matches(uhost):
            return False
        if self.path is None:
            return True                      # 模式没写路径 -> 任意路径都过
        if self.path.endswith("/*"):
            return upath.startswith(self.path[:-1])
        return upath == self.path

    def _host_matches(self, uhost):
        if self.host == "*":
            return True
        if self.host.startswith("*."):
            suffix = self.host[1:]           # ".tauri.app"
            return uhost.endswith(suffix) and uhost != suffix[1:]
        return uhost == self.host

    def __repr__(self):
        return "UrlPattern({})".format(self.pattern)


# ------------------------------------------------------------ 来源与上下文


class Origin:
    def __init__(self, local=True, url=None):
        self.local = local
        self.url = url

    def matches(self, context):
        """authority.rs:58 —— Local 只对 Local；Remote 交给 url_pattern.test。"""
        if self.local:
            return context.local
        if context.local:
            return False
        return context.pattern.test(self.url)

    def __repr__(self):
        return "Origin(local)" if self.local else "Origin({})".format(self.url)


class ExecutionContext:
    def __init__(self, local=True, pattern=None):
        self.local = local
        self.pattern = RemoteUrlPattern(pattern) if pattern else None

    def __repr__(self):
        return "Context(Local)" if self.local else "Context({})".format(self.pattern.pattern)


class WindowPattern:
    """窗口/webview 标签匹配：本模型按精确匹配建模（源码走 WindowPattern::matches）。"""

    def __init__(self, label):
        self.label = label

    def matches(self, label):
        return self.label == label


# ------------------------------------------------------------ 解析结果


class ResolvedCommand:
    def __init__(self, command, capability, permission, context,
                 windows=None, webviews=None, scope_id=None):
        self.command = command
        self.capability = capability
        self.permission = permission
        self.context = context
        self.windows = windows or []
        self.webviews = webviews or []
        self.scope_id = scope_id

    def __repr__(self):
        return "ResolvedCommand({}/{})".format(self.capability, self.permission)


class RuntimeAuthority:
    def __init__(self):
        self.allowed_commands = {}
        self.denied_commands = {}

    def allow(self, rc, key=None):
        self.allowed_commands.setdefault(key or rc.command, []).append(rc)

    def deny(self, rc, key=None):
        self.denied_commands.setdefault(key or rc.command, []).append(rc)

    def resolve_access(self, command, window, webview, origin):
        """authority.rs:439 —— deny 先看 origin，命中就整体拒绝；再过滤 allow。"""
        denied = self.denied_commands.get(command)
        if denied is not None:
            if any(origin.matches(rc.context) for rc in denied):
                return None                  # deny 与 window/webview 无关，只看 origin
        resolved = self.allowed_commands.get(command) or []
        out = [rc for rc in resolved
               if origin.matches(rc.context)
               and (any(w.matches(webview) for w in rc.webviews)
                    or any(w.matches(window) for w in rc.windows))]
        return out or None

    def access_message(self, command, window, webview, origin):
        denied = self.denied_commands.get(command)
        if denied is not None and any(origin.matches(rc.context) for rc in denied):
            return "{} not allowed on origin [{}]".format(command, origin)
        resolved = self.allowed_commands.get(command) or []
        ctx_ok = [rc for rc in resolved if origin.matches(rc.context)]
        if not ctx_ok:
            return "{} not allowed. permission_error_detail".format(command)
        return "{} not allowed on window \"{}\", webview \"{}\"".format(
            command, window, webview)


# ------------------------------------------------------------ IPC 请求解析


class InvokeRequest:
    def __init__(self, cmd, callback, error, body=None, headers=None):
        self.cmd = cmd
        self.callback = callback
        self.error = error
        self.body = body
        self.headers = headers or {}


class ParseError(Exception):
    pass


def parse_invoke_request(headers, body=None):
    """protocol.rs:434 —— 两个回调头必须是「数字字符串」。"""
    def numeric(name):
        raw = headers.get(name)
        if raw is None:
            raise ParseError("{} header is missing".format(name))
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ParseError("{} header value must be a numeric string".format(name))

    cmd = headers.get("Tauri-Cmd") or headers.get("cmd")
    if cmd is None:
        raise ParseError("command is missing")
    callback = numeric(TAURI_CALLBACK_HEADER_NAME)
    error = numeric(TAURI_ERROR_HEADER_NAME)
    return InvokeRequest(cmd, callback, error, body, dict(headers))


def response_header(is_ok):
    return TAURI_RESPONSE_HEADER_OK if is_ok else TAURI_RESPONSE_HEADER_ERROR


def main():
    print("== 命令键归一化 ==")
    print("  app      :", command_key(APP_ACL_KEY, "do_thing"))
    print("  core     :", command_key("core:event", "emit"))
    print("  plugin   :", command_key("fs", "read_file"))

    print("== URL 模式（取自 acl/mod.rs 单测）==")
    p = RemoteUrlPattern("http://*.tauri.app")
    print("  http://*.tauri.app vs tauri.app    ->", p.test("http://tauri.app/path"))
    print("  http://*.tauri.app vs api.tauri.app->", p.test("http://api.tauri.app/path"))
    p2 = RemoteUrlPattern("*://localhost")
    print("  *://localhost vs custom://localhost ->", p2.test("custom://localhost/path"))

    print("== resolve_access ==")
    auth = RuntimeAuthority()
    auth.allow(ResolvedCommand("plugin:fs|read_file", "desktop", "fs:default",
                               ExecutionContext(local=True), windows=[WindowPattern("main")]))
    print("  本地 / 窗口 main      ->", auth.resolve_access(
        "plugin:fs|read_file", "main", "main", Origin(local=True)))
    print("  本地 / 窗口 settings  ->", auth.resolve_access(
        "plugin:fs|read_file", "settings", "settings", Origin(local=True)))
    print("  远程来源              ->", auth.resolve_access(
        "plugin:fs|read_file", "main", "main", Origin(local=False, url="https://tauri.app/")))


if __name__ == "__main__":
    main()
