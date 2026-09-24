"""FetchMetadata与资源隔离策略 自检。

官方向量与示例（实读 https://w3c.github.io/webappsec-fetch-metadata/ 后抄录）：
  §2   fetch() 的 destination 是空串；<img> 是 image；new Worker() 是 worker；
       顶层导航是 document；<iframe> 导航是 iframe。
       从 https://example.com 到 https://example.com/ 的顶层导航（用户点击站内链接）：
       Dest=document / Mode=navigate / Site=same-origin / User=?1
  §2.3 Sec-Fetch-Site 算法：初值 same-origin；用户显式触发的导航 → none；
       遍历 url list，同源 continue，否则先置 cross-site，不同站才 break，
       同站则改为 same-site
  §4.1 重定向走完整个 url list：任一跨站即 cross-site
  §4.2 Sec- 前缀 → forbidden response-header name → JS 不可伪造
"""

from fetchmeta import (Origin, Req, append_fetch_metadata,
                       is_forbidden_response_header_name, isolation_policy,
                       js_set_header, normalize_incoming, registrable_domain,
                       set_sec_fetch_site, set_sec_fetch_user)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


EX = Origin("https", "example.com")
SUB = Origin("https", "subdomain.example.com")
NET = Origin("https", "example.net")
UK = Origin("https", "a.b.co.uk")
UK2 = Origin("https", "c.b.co.uk")

# ---------- registrable domain（eTLD+1） ----------
eq("apex 的 RD", registrable_domain("example.com"), "example.com")
eq("子域的 RD 是 apex", registrable_domain("subdomain.example.com"), "example.com")
eq("多级后缀 co.uk", registrable_domain("a.b.co.uk"), "b.co.uk")
eq("单标签主机名", registrable_domain("localhost"), "localhost")

# ---------- same origin / same site ----------
ck("同源", EX.same_origin(Origin("https", "example.com")))
ck("显式 443 与默认 443 同源", EX.same_origin(Origin("https", "example.com", 443)))
ck("端口不同不同源", not EX.same_origin(Origin("https", "example.com", 8443)))
ck("scheme 不同不同源", not EX.same_origin(Origin("http", "example.com")))
ck("子域同站", EX.same_site(SUB))
ck("不同 registrable 不同站", not EX.same_site(NET))
ck("co.uk 下不同子域同站", UK.same_site(UK2))
ck("子域与 apex 不同源但同站",
   not EX.same_origin(SUB) and EX.same_site(SUB))

# ---------- §2.3 Sec-Fetch-Site 算法 ----------
eq("空 url list → same-origin", set_sec_fetch_site(Req(EX, [])), ("same-origin", 0))
eq("单一同源 url", set_sec_fetch_site(Req(EX, [EX])), ("same-origin", 1))
eq("单一同站跨源 url → same-site",
   set_sec_fetch_site(Req(EX, [SUB])), ("same-site", 1))
eq("单一跨站 url → cross-site",
   set_sec_fetch_site(Req(EX, [NET])), ("cross-site", 1))
eq("用户显式触发的导航 → none 且不遍历",
   set_sec_fetch_site(Req(EX, [EX], navigation=True, user_initiated=True)),
   ("none", 0))
eq("导航但非用户触发，仍走遍历",
   set_sec_fetch_site(Req(EX, [EX], navigation=True, user_initiated=False)),
   ("same-origin", 1))
eq("用户触发优先于跨站 url",
   set_sec_fetch_site(Req(EX, [NET], navigation=True, user_initiated=True)),
   ("none", 0))

# ---------- §4.1 重定向链 ----------
eq("链 (apex, 子域) → same-site",
   set_sec_fetch_site(Req(EX, [EX, SUB])), ("same-site", 2))
eq("链 (apex, 子域, 异站) → cross-site",
   set_sec_fetch_site(Req(EX, [EX, SUB, NET])), ("cross-site", 3))
# 规范原文：即便最后重定向回 example.com，链上出现过 example.net 就仍是 cross-site
eq("链末尾绕回 apex 仍是 cross-site",
   set_sec_fetch_site(Req(EX, [EX, SUB, NET, EX])), ("cross-site", 3))
ck("绕回后最后一个 url 根本没被检查",
   set_sec_fetch_site(Req(EX, [EX, SUB, NET, EX]))[1] == 3)
eq("链 (子域, 异站, 子域) 在异站处 break",
   set_sec_fetch_site(Req(EX, [SUB, NET, SUB])), ("cross-site", 2))
eq("全同源链 → same-origin",
   set_sec_fetch_site(Req(EX, [EX, EX, EX])), ("same-origin", 3))

# ---------- §2.4 Sec-Fetch-User ----------
eq("非导航请求不发 User", set_sec_fetch_user(Req(EX, user_activation=True)), None)
eq("导航但无激活不发 User",
   set_sec_fetch_user(Req(EX, navigation=True, user_activation=False)), None)
eq("导航且有激活发 ?1",
   set_sec_fetch_user(Req(EX, navigation=True, user_activation=True)), "?1")

# ---------- §3 potentially trustworthy 闸门 ----------
eq("非可信 URL 一个头都不发",
   append_fetch_metadata(Req(EX, [EX], trustworthy=False)), {})
# §2 规范示例：站内链接触发的顶层导航
eq("顶层导航四头齐全",
   append_fetch_metadata(Req(EX, [EX], dest="document", mode="navigate",
                                 navigation=True, user_activation=True)),
   {"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin", "Sec-Fetch-User": "?1"})
# §2 规范示例：跨站 <img>
img = append_fetch_metadata(Req(EX, [NET], dest="image", mode="no-cors"))
eq("跨站 img 的 Dest", img["Sec-Fetch-Dest"], "image")
eq("跨站 img 的 Mode", img["Sec-Fetch-Mode"], "no-cors")
eq("跨站 img 的 Site", img["Sec-Fetch-Site"], "cross-site")
ck("子资源请求不带 User", "Sec-Fetch-User" not in img)

# ---------- §2.2/§2.3 非法值忽略 ----------
eq("非法 Mode 被忽略",
   normalize_incoming({"Sec-Fetch-Mode": "weird", "Sec-Fetch-Site": "same-origin"}),
   {"site": "same-origin"})
eq("非法 Site 被忽略",
   normalize_incoming({"Sec-Fetch-Site": "nonsense", "Sec-Fetch-Mode": "cors"}),
   {"mode": "cors"})
eq("Dest 原样透传（规范未要求忽略）",
   normalize_incoming({"Sec-Fetch-Dest": "brand-new-thing"}),
   {"dest": "brand-new-thing"})
eq("mode 缺失则不产出 mode",
   normalize_incoming({"Sec-Fetch-Site": "same-origin"}), {"site": "same-origin"})

# ---------- §4.2 Sec- 前缀不可伪造 ----------
ck("Sec-Fetch-Site 不可被 JS 设置",
   is_forbidden_response_header_name("Sec-Fetch-Site"))
ck("前缀大小写不敏感", is_forbidden_response_header_name("sec-fetch-site"))
ck("SEC-FETCH-MODE 同样禁止", is_forbidden_response_header_name("SEC-FETCH-MODE"))
ck("Set-Cookie 也是 forbidden", is_forbidden_response_header_name("Set-Cookie"))
ck("普通头不禁止", not is_forbidden_response_header_name("X-Custom"))
h = {}
ck("JS 写 Sec-Fetch-Site 失败", not js_set_header(h, "Sec-Fetch-Site", "same-origin"))
eq("伪造未落进头部", h, {})
ck("JS 写普通头成功", js_set_header(h, "X-Custom", "1"))
eq("普通头落进头部", h, {"X-Custom": "1"})

# ---------- 资源隔离策略（工程惯例，非规范条文） ----------
eq("无元数据 fail-open", isolation_policy({}), "allow")
eq("顶层导航同源放行",
   isolation_policy({"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                     "Sec-Fetch-Site": "same-origin"}), "allow")
eq("顶层导航跨站拦截",
   isolation_policy({"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
                     "Sec-Fetch-Site": "cross-site"}), "block")
eq("子资源跨站拦截",
   isolation_policy({"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                     "Sec-Fetch-Site": "cross-site"}), "block")
eq("子资源同站放行",
   isolation_policy({"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
                     "Sec-Fetch-Site": "same-site"}), "allow")
eq("伪造的非法 site 值会被忽略而 fail-open",
   isolation_policy({"Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "junk"}), "allow")

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
