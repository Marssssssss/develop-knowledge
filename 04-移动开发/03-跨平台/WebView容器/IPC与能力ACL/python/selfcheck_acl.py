"""Tauri IPC 与 capability/ACL 自检。

运行： python selfcheck_acl.py
"""

from main import (
    APP_ACL_KEY, TAURI_CALLBACK_HEADER_NAME, TAURI_ERROR_HEADER_NAME,
    TAURI_RESPONSE_HEADER_ERROR, TAURI_RESPONSE_HEADER_OK,
    command_key, RemoteUrlPattern, Origin, ExecutionContext, WindowPattern,
    ResolvedCommand, RuntimeAuthority, parse_invoke_request, ParseError,
    response_header,
)

PASS = 0


def ok(cond, label, actual=None):
    global PASS
    assert cond, "FAIL: {} -> {!r}".format(label, actual)
    PASS += 1


def local_ctx():
    return ExecutionContext(local=True)


def remote_ctx(pattern):
    return ExecutionContext(local=False, pattern=pattern)


# ================================================== A. 命令键归一化
ok(command_key(APP_ACL_KEY, "do_thing") == "do_thing",
   "A1 APP_ACL_KEY(__app-acl__) 的命令不加前缀", command_key(APP_ACL_KEY, "do_thing"))
ok(command_key("core:event", "emit") == "plugin:event|emit",
   "A2 core: 前缀被剥掉后仍写成 plugin:<name>|<cmd>",
   command_key("core:event", "emit"))
ok(command_key("fs", "read_file") == "plugin:fs|read_file",
   "A3 普通插件 -> plugin:<plugin>|<cmd>", command_key("fs", "read_file"))
ok(APP_ACL_KEY == "__app-acl__", "A4 APP_ACL_KEY 的字面值", APP_ACL_KEY)
ok(command_key("core:app", "version") == "plugin:app|version",
   "A5 core:app 也走同一条规则", command_key("core:app", "version"))

# ================================================== B. URL 模式（源码单测）
p = RemoteUrlPattern("http://*")
ok(p.test("http://tauri.app/path") is True, "B1 http://* 命中任意主机")
ok(p.test("http://localhost/path?q=1") is True, "B2 http://* 也命中 localhost 与查询串")
ok(p.test("https://tauri.app/path") is False, "B3 但 scheme 必须一致")

p2 = RemoteUrlPattern("http://*.tauri.app")
ok(p2.test("http://tauri.app/path") is False,
   "B4 *.tauri.app 不命中裸域名（子域通配符要求至少一级子域）")
ok(p2.test("http://api.tauri.app/path") is True, "B5 命中子域")
ok(p2.test("http://localhost/path") is False, "B6 不命中其它主机")

p3 = RemoteUrlPattern("http://localhost/*")
ok(p3.test("http://localhost/path") is True, "B7 路径通配符命中")
ok(p3.test("http://localhost/path?q=1") is True, "B8 查询串不影响")

p4 = RemoteUrlPattern("*://localhost")
ok(p4.test("http://localhost/path") is True, "B9 scheme 通配符")
ok(p4.test("https://localhost/path?q=1") is True, "B10 https 也过")
ok(p4.test("custom://localhost/path") is True, "B11 自定义 scheme 也过")

# ================================================== C. Origin × ExecutionContext
ok(Origin(local=True).matches(local_ctx()) is True, "C1 Local ↔ Local")
ok(Origin(local=True).matches(remote_ctx("http://*")) is False,
   "C2 本地来源不会命中只给远程 URL 的能力")
ok(Origin(local=False, url="https://tauri.app/x").matches(local_ctx()) is False,
   "C3 远程来源不会命中只给本地的能力")
ok(Origin(local=False, url="https://api.tauri.app/x").matches(
    remote_ctx("https://*.tauri.app")) is True, "C4 远程按 URL 模式匹配")

# ================================================== D. resolve_access
def auth_with(window="main", context=None, webviews=None):
    a = RuntimeAuthority()
    a.allow(ResolvedCommand("plugin:fs|read_file", "desktop", "fs:default",
                            context or local_ctx(),
                            windows=[WindowPattern(window)],
                            webviews=webviews or []))
    return a


a = auth_with()
ok(a.resolve_access("plugin:fs|read_file", "main", "main", Origin(local=True)) is not None,
   "D1 窗口标签匹配 -> 放行")
ok(a.resolve_access("plugin:fs|read_file", "settings", "settings",
                    Origin(local=True)) is None,
   "D2 窗口标签不匹配 -> 拒绝")
ok(a.resolve_access("plugin:other|cmd", "main", "main", Origin(local=True)) is None,
   "D3 命令不在允许表里 -> 拒绝")
ok(a.resolve_access("plugin:fs|read_file", "main", "main",
                    Origin(local=False, url="https://tauri.app/")) is None,
   "D4 来源不匹配 -> 拒绝（哪怕窗口对）")

aw = auth_with(window="nope", webviews=[WindowPattern("web-main")])
ok(aw.resolve_access("plugin:fs|read_file", "other", "web-main",
                     Origin(local=True)) is not None,
   "D5 窗口不匹配但 webview 匹配 -> 放行（两者是或关系）")

# deny 优先，且只看 origin
a = auth_with()
a.deny(ResolvedCommand("plugin:fs|read_file", "locked", "fs:deny-all", local_ctx()))
ok(a.resolve_access("plugin:fs|read_file", "main", "main", Origin(local=True)) is None,
   "D6 deny 命中 origin 时，窗口再对也拒绝（deny 优先于 allow）")
ok(a.resolve_access("plugin:fs|read_file", "main", "main",
                    Origin(local=False, url="https://tauri.app/")) is None,
   "D7 远程来源下 allow 本就不匹配，结果同样是拒绝")

a2 = auth_with()
a2.deny(ResolvedCommand("plugin:fs|read_file", "locked", "fs:deny-remote",
                        remote_ctx("https://*.tauri.app")))
ok(a2.resolve_access("plugin:fs|read_file", "main", "main", Origin(local=True)) is not None,
   "D8 deny 只作用于指定 origin：本地来源不受影响")
ok(a2.resolve_access("plugin:fs|read_file", "main", "main",
                     Origin(local=False, url="https://api.tauri.app/x")) is None,
   "D9 该远程来源被 deny")

# 多条 allow 可以叠加
a3 = auth_with(window="main")
a3.allow(ResolvedCommand("plugin:fs|read_file", "mobile", "fs:mobile", local_ctx(),
                         windows=[WindowPattern("mobile")]))
ok(len(a3.resolve_access("plugin:fs|read_file", "mobile", "mobile",
                         Origin(local=True))) == 1, "D10 命中的那条会被返回")
ok(len(a3.resolve_access("plugin:fs|read_file", "main", "main",
                         Origin(local=True))) == 1, "D11 另一条同样只返回自己")
ok(a3.resolve_access("plugin:fs|read_file", "other", "other",
                     Origin(local=True)) is None, "D12 两条都不匹配 -> None")

ok(a3.access_message("plugin:fs|read_file", "other", "other",
                     Origin(local=True)).startswith("plugin:fs|read_file not allowed on window"),
   "D13 有匹配 origin 的能力但窗口不对 -> 报错文案指向 window/webview")
ok(a3.access_message("plugin:fs|read_file", "main", "main",
                     Origin(local=False, url="https://tauri.app/")).endswith(
                         "permission_error_detail"),
   "D14 连 origin 都没匹配 -> 文案退化为 permission_error_detail",
   a3.access_message("plugin:fs|read_file", "main", "main",
                     Origin(local=False, url="https://tauri.app/")))

# ================================================== E. IPC 请求头
req = parse_invoke_request({
    "Tauri-Cmd": "plugin:fs|read_file",
    TAURI_CALLBACK_HEADER_NAME: "12378123",
    TAURI_ERROR_HEADER_NAME: "6243",
}, body={"path": "a.txt"})
ok(req.cmd == "plugin:fs|read_file", "E1 命令从请求里取出")
ok(req.callback == 12378123 and req.error == 6243,
   "E2 两个回调头都被解析成数字（源码用同样的数值做单测）",
   (req.callback, req.error))

try:
    parse_invoke_request({"Tauri-Cmd": "x", TAURI_CALLBACK_HEADER_NAME: "abc",
                          TAURI_ERROR_HEADER_NAME: "1"})
    ok(False, "E3 非数字回调头应报错")
except ParseError as ex:
    ok("numeric string" in str(ex), "E3 回调头必须是数字字符串", str(ex))

try:
    parse_invoke_request({TAURI_CALLBACK_HEADER_NAME: "1", TAURI_ERROR_HEADER_NAME: "2"})
    ok(False, "E4 缺命令应报错")
except ParseError as ex:
    ok("missing" in str(ex), "E4 缺命令/缺头都要报错", str(ex))

ok(response_header(True) == TAURI_RESPONSE_HEADER_OK == "ok", "E5 响应头 ok")
ok(response_header(False) == TAURI_RESPONSE_HEADER_ERROR == "error", "E6 响应头 error")

print("Tauri IPC 与 capability ACL 自检：{} 项断言全部通过".format(PASS))
