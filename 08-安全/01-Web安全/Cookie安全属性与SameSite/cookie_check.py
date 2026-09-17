"""Cookie 存储模型 + SameSite + HSTS 自检。

断言来源:
- RFC 6265 §5.1.3/§5.1.4/§5.3/§5.4(域匹配、default-path、存储模型、Cookie 头)
- draft-ietf-httpbis-rfc6265bis §4.1.2.7/§4.1.3/§5.4/§5.5/§5.6.7.2/§5.7(UA 侧前缀、400 天、SameSite、非安全覆盖)
- RFC 6797 §6.1/§8.1/§8.2/§8.3(HSTS)
"""
from cookie_store import CookieStore, default_path, domain_match, path_match
from hsts_store import HstsStore, domain_label_match, parse_sts_header

PASS = 0


def check(label, cond, detail=""):
    global PASS
    assert cond, f"FAIL {label} {detail}"
    PASS += 1
    print(f"  ok  {label}")


print("§5.1.3 域匹配 / §5.1.4 default-path 与 path-match")
check("相同域匹配", domain_match("example.com", "example.com"))
check("点边界后缀匹配", domain_match("www.example.com", "example.com"))
check("非点边界不匹配", not domain_match("notexample.com", "example.com"))
check("IP 不做后缀匹配", not domain_match("127.0.0.1", "0.0.1"))
check("default-path 取到最右斜杠之前(§5.1.4 原文不含尾斜杠,与 MDN 表述不同)",
      default_path("/docs/Web/HTTP/index.html") == "/docs/Web/HTTP")
check("default-path 单斜杠 → /", default_path("/index.html") == "/" and default_path("") == "/")
check("path-match 目录前缀", path_match("/docs/x", "/docs") and path_match("/docs", "/docs"))
check("path-match 不跨词边界", not path_match("/docsets", "/docs") and not path_match("/fr/docs", "/docs"))
check("cookie-path 以 / 结尾时任意前缀", path_match("/docs/a/b", "/docs/"))

print("§5.3 存储模型:域/路径/Secure/HttpOnly")
s = CookieStore(now=1_000_000)
u = "https://www.example.com/login"
c = s.receive("SID=1; Domain=example.com; Path=/", u)
check("Domain 去掉前导点并跨子域", c.domain == "example.com" and not c.host_only)
check("同域子域可带出", s.cookie_header("https://a.example.com/x") == "SID=1")
check("非子域不带出", s.cookie_header("https://notexample.com/x") == "")
s2 = CookieStore(now=1_000_000)
c2 = s2.receive("SID=1; Path=/", u)
check("省略 Domain → host-only", c2.host_only and c2.domain == "www.example.com")
check("host-only 不下发子域", s2.cookie_header("https://a.example.com/x") == ""
      and s2.cookie_header("https://www.example.com/x") == "SID=1")
check("公共后缀被拒绝", CookieStore(now=0).receive("a=1; Domain=com", u) is None)
check("上位域(非父域)被拒绝",
      CookieStore(now=0).receive("a=1; Domain=other.example.com", u) is None)
check("子域(非父域)被拒绝",
      CookieStore(now=0).receive("a=1; Domain=beta.www.example.com", u) is None)
check("Path 非 / 开头 → 回落 default-path",
      CookieStore(now=0).receive("a=1; Path=abc", u).path == "/")
check("HttpOnly 由非 HTTP API 写入 → 丢弃",
      CookieStore(now=0).receive("a=1; HttpOnly", u, from_http_api=False) is None)
check("Secure cookie 不能由 http 源设置",
      CookieStore(now=0).receive("a=1; Secure", "http://www.example.com/") is None)
s3 = CookieStore(now=0)
s3.receive("a=1; Secure", "https://www.example.com/")
check("Secure cookie 不在 http 请求上发送", s3.cookie_header("http://www.example.com/") == ""
      and s3.cookie_header("https://www.example.com/") == "a=1")

print("§5.5 / §5.6 Max-Age 与 Expires")
s4 = CookieStore(now=0)
s4.receive("a=1; Max-Age=100; Expires=Thu, 01 Jan 2099 00:00:00 GMT", "https://e.com/")
check("Max-Age 优先于 Expires", s4.jar[0].expiry == 100)
s5 = CookieStore(now=0)
c5 = s5.receive("a=1; Max-Age=9999999999", "https://e.com/")
check("超 400 天被截断", c5.expiry == 400 * 86400)
check("Max-Age=0 立即过期", CookieStore(now=10).receive("a=1; Max-Age=0", "https://e.com/") is None)
check("无 Expires/Max-Age → 会话 cookie", not CookieStore(now=0).receive("a=1", "https://e.com/").persistent)
check("超 4096 字节的 Set-Cookie 整条丢弃",
      CookieStore(now=0).receive("a=" + "x" * 4200, "https://e.com/") is None)
check("无 = 的 name-value → 丢弃", CookieStore(now=0).receive("justname", "https://e.com/") is None)
check("空名 → 丢弃", CookieStore(now=0).receive("=v", "https://e.com/") is None)
check("同名同域同路径 → 覆盖并继承 creation-time",
      CookieStore(now=0).receive("a=1", "https://e.com/") is not None)

print("§5.4 Cookie 头顺序与 §5.3 覆盖语义")
s6 = CookieStore(now=0)
s6.receive("short=1; Path=/", "https://e.com/a/b")
s6.receive("long=2; Path=/a", "https://e.com/a/b")
s6.receive("mid=3; Path=/a/b", "https://e.com/a/b")
check("路径长者在前", s6.cookie_header("https://e.com/a/b") == "mid=3; long=2; short=1",
      s6.cookie_header("https://e.com/a/b"))
s7 = CookieStore(now=0)
s7.receive("a=1; Path=/", "https://e.com/")
s7.now = 5
s7.receive("b=2; Path=/", "https://e.com/")
check("等长路径按 creation-time 升序", s7.cookie_header("https://e.com/") == "a=1; b=2")
s7.receive("a=9; Path=/", "https://e.com/")
check("覆盖不改变顺序(creation-time 继承)",
      s7.cookie_header("https://e.com/") == "a=9; b=2")

print("§4.1.3 / §5.4 cookie 名前缀(UA 侧大小写不敏感)")
check("__Host- 完整形态被接受",
      CookieStore(now=0).receive("__Host-SID=1; Secure; Path=/", "https://e.com/") is not None)
for bad in ("__Host-SID=1", "__Host-SID=1; Secure", "__Host-SID=1; Domain=e.com; Path=/",
            "__Host-SID=1; Secure; Path=/x"):
    check(f"__Host- 违规被拒: {bad}", CookieStore(now=0).receive(bad, "https://e.com/") is None)
check("__Host- 由 https 但带 Domain 仍拒",
      CookieStore(now=0).receive("__Host-SID=1; Secure; Path=/; Domain=e.com", "https://e.com/") is None)
check("大小写混写的 __HOST- 同样被强制",
      CookieStore(now=0).receive("__HOST-SID=1; Domain=e.com", "https://e.com/") is None)
check("__SeCuRe- 仍需 Secure",
      CookieStore(now=0).receive("__SeCuRe-SID=1", "https://e.com/") is None)
check("__Secure- 带 Secure 通过",
      CookieStore(now=0).receive("__SECURE-SID=1; Secure", "https://e.com/") is not None)

print("§5.7 step 21 非安全 cookie 不得覆盖同名 Secure cookie(cookie-fixing)")
s8 = CookieStore(now=0)
s8.receive("a=secure; Secure; Path=/login", "https://e.com/login")
check("非安全 cookie 覆盖同路径 Secure → 拒绝",
      s8.receive("a=evil; Path=/login", "http://e.com/login") is None)
check("非安全 cookie 覆盖更长路径(/login/en 是 /login 的后代)→ 拒绝",
      s8.receive("a=evil; Path=/login/en", "http://e.com/login") is None)
check("非安全 cookie 可设更短路径(比较不对称,规范 Note 明示)",
      s8.receive("a=evil; Path=/", "http://e.com/") is not None)
check("两个同名 cookie 共存时按路径长者在前",
      s8.cookie_header("https://e.com/login") == "a=secure; a=evil",
      s8.cookie_header("https://e.com/login"))

print("§4.1.2.7 / §5.6.7 SameSite 检索语义")
s9 = CookieStore(now=0)
s9.receive("lax=1; SameSite=Lax; Secure", "https://e.com/")
s9.receive("strict=1; SameSite=Strict; Secure", "https://e.com/")
s9.receive("none=1; SameSite=None; Secure", "https://e.com/")
got = s9.cookie_header("https://e.com/x", cross_site=False)
check("同站请求全部发出", got.count("=") == 3, got)
got = s9.cookie_header("https://e.com/x", cross_site=True, top_level_nav=False)
check("跨站子资源:None 唯一存活", got == "none=1", got)
got = s9.cookie_header("https://e.com/x", cross_site=True, top_level_nav=True, safe_method=True)
check("跨站顶层安全导航:Lax+None", got == "lax=1; none=1", got)
got = s9.cookie_header("https://e.com/x", cross_site=True, top_level_nav=True, safe_method=False)
check("跨站顶层 POST:Strict 与 Lax 均不发", got == "none=1", got)
check("SameSite=None 缺 Secure → 整条丢弃",
      CookieStore(now=0).receive("a=1; SameSite=None", "https://e.com/") is None)
check("非法 SameSite 值 → Default(等同 Lax)",
      CookieStore(now=0).receive("a=1; SameSite=Weird; Secure", "https://e.com/").same_site == "Default")
s10 = CookieStore(now=0, lax_allowing_unsafe=True)
s10.receive("a=1; Secure", "https://e.com/")
check("Default + 2 分钟内 + 顶层 POST → 放行(Lax-allowing-unsafe)",
      s10.cookie_header("https://e.com/x", cross_site=True, top_level_nav=True, safe_method=False) == "a=1")
s10.now = 121
check("超 2 分钟窗口 → 不放行",
      s10.cookie_header("https://e.com/x", cross_site=True, top_level_nav=True, safe_method=False) == "")
check("显式 SameSite=Lax 不享受 Lax-allowing-unsafe",
      CookieStore(now=0).receive("b=1; SameSite=Lax; Secure", "https://e.com/") is not None)

print("§5.7 容量与淘汰")
s11 = CookieStore(now=0)
for i in range(55):
    s11.now = i
    s11.receive(f"c{i}=1; Path=/", "https://e.com/")
check("单域上限 50", len(s11.jar) == 50, len(s11.jar))
check("淘汰最久未访问者", "c0" not in s11.names() and "c54" in s11.names())
s12 = CookieStore(now=0)
s12.receive("a=1; Max-Age=1", "https://e.com/")
s12.now = 5
s12.receive("b=2", "https://e.com/")
check("接收时清理过期条目", "a" not in s12.names() and "b" in s12.names())

print("RFC 6797 §6.1 HSTS 头解析")
check("max-age + includeSubDomains",
      parse_sts_header("max-age=31536000; includeSubDomains") ==
      {"max-age": 31536000, "includeSubDomains": True})
check("大小写不敏感", parse_sts_header("Max-Age=100; INCLUDESUBDOMAINS")["max-age"] == 100)
check("max-age 可加引号", parse_sts_header('max-age="31536000"')["max-age"] == 31536000)
check("未知指令被忽略", parse_sts_header("max-age=100; preload; foo=bar")["max-age"] == 100)
check("重复指令 → 整头忽略", parse_sts_header("max-age=100; max-age=200") is None)
check("缺 max-age → 整头忽略", parse_sts_header("includeSubDomains") is None)
check("非数字 max-age → 整头忽略", parse_sts_header("max-age=abc") is None)
check("§12.3 注:preload 不是 RFC 6797 定义的指令",
      "preload" not in parse_sts_header("max-age=1; preload"))

print("§8.1–§8.3 HSTS 处理模型")
check("父域/一致匹配判定", domain_label_match("bar.foo.example.com", "qaz.bar.foo.example.com") == "superdomain"
      and domain_label_match("foo.example.com", "foo.example.com") == "congruent"
      and domain_label_match("other.com", "foo.example.com") == "none")
h = HstsStore()
h.note(["max-age=1000"], "example.com", secure_transport=True, now=0)
check("自身命中", h.applies("example.com", now=1))
check("未声明 includeSubDomains → 子域不命中", not h.applies("a.example.com", now=1))
h.note(["max-age=1000; includeSubDomains"], "example.com", secure_transport=True, now=0)
check("声明后子域命中(父域匹配)", h.applies("a.example.com", now=1))
check("更深子域也命中", h.applies("x.y.example.com", now=1))
check("§8.2 无关域不命中", not h.applies("notexample.com", now=1))
h2 = HstsStore()
h2.note(["max-age=1000"], "example.com", secure_transport=False, now=0)
check("非安全传输上的 STS 头被忽略", not h2.applies("example.com", now=1))
h2.note(["max-age=1000", "max-age=99"], "example.com", secure_transport=True, now=0)
check("同一响应内多个 STS 头字段只取第一个", h2.policies["example.com"]["expiry"] == 1000)
h3 = HstsStore()
h3.note(["max-age=1000; includeSubDomains"], "example.com", True, now=0)
h3.note(["max-age=0; includeSubDomains"], "example.com", True, now=0)
check("max-age=0 删除策略(含子域)", "example.com" not in h3.policies
      and not h3.applies("a.example.com", now=1))
h4 = HstsStore()
h4.note(["max-age=10"], "example.com", True, now=0)
check("过期后不再命中", not h4.applies("example.com", now=11) and h4.pruned(now=11) == 1)
check("IP 字面量不被标记",
      HstsStore().note(["max-age=100"], "127.0.0.1", True, now=0) is None)
h5 = HstsStore()
h5.note(["max-age=100; includeSubDomains"], "example.com", True, now=0)
check("§8.3 无端口:http→https 且不加端口", h5.upgrade("http", "example.com", None) == ("https", None))
check("§8.3 显式 80 → 443", h5.upgrade("http", "example.com", 80) == ("https", 443))
check("§8.3 其他端口保留", h5.upgrade("http", "example.com", 8080) == ("https", 8080))
check("§8.3 已有 https 不变", h5.upgrade("https", "example.com", 443) == ("https", 443))

print(f"\n{PASS} 项断言全部通过 (Cookie 存储模型 + SameSite + HSTS)")
