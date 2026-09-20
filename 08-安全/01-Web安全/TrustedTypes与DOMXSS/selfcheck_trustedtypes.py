"""TrustedTypes与DOMXSS 自检。

依据 W3C Trusted Types 规范(https://www.w3.org/TR/trusted-types/ 全文实读)
的 §3.1 / §3.3 / §3.4 / §3.5 / §3.8 / §4.2.1.1 / §4.2.3 / §4.2.4 / §4.2.5。
"""

from trustedtypes import (Global, CSPPolicy, create_policy, create_trusted_type,
                          get_policy_value, get_trusted_type_data_for_attribute,
                          does_sink_type_require_trusted_types,
                          get_trusted_type_compliant_string,
                          require_tt_pre_navigation_check, set_attribute,
                          set_inner_html, set_script_src, set_script_text,
                          should_policy_creation_be_blocked, TrustedType,
                          TrustedTypeError, HTML_NS, SVG_NS, XLINK_NS)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s  %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


def raises(name, fn):
    try:
        fn()
    except TrustedTypeError:
        ck(name, True)
        return
    ck(name, False, "没有抛 TypeError")


def enforce_csp(**kw):
    return [CSPPolicy("enforce", **kw)]


def report_csp(**kw):
    return [CSPPolicy("report", **kw)]


RTT = {"require-trusted-types-for": "'script'"}

# ------------------------------------------------------- 不强制时字符串直通
g0 = Global()
eq("无 CSP 时 innerHTML 接受裸字符串", set_inner_html(g0, "<b>hi</b>"), "<b>hi</b>")
ck("无 CSP 时无任何违规报告", not g0.violations)

# ------------------------------------------------- §3.4 强制模式的核心行为
g1 = Global(enforce_csp(**RTT))
raises("强制模式下 innerHTML 拒绝裸字符串", lambda: set_inner_html(g1, "<b>x</b>"))
ck("且产生一条 trusted-types-sink 违规",
   any(v.resource == "trusted-types-sink" for v in g1.violations),
   str([v.sample for v in g1.violations]))

noop = create_policy(g1, "sanitizer", {"createHTML": lambda s, *a: s})
html = create_trusted_type(noop, "TrustedHTML", "<b>ok</b>")
eq("TrustedHTML 直接进入汇点", set_inner_html(g1, html), "<b>ok</b>")

full = create_policy(g1, "full", {"createHTML": lambda s, *a: s,
                                   "createScript": lambda s, *a: s,
                                   "createScriptURL": lambda s, *a: s})
script = create_trusted_type(full, "TrustedScript", "1+1")
raises("类型错配:TrustedScript 给期望 TrustedHTML 的汇点",
       lambda: set_inner_html(g1, script))
eq("类型正确的 TrustedScript 走 script 文本汇点",
   set_script_text(g1, create_trusted_type(full, "TrustedScript", "alert(1)")),
   "alert(1)")

# ------------------------------------------------- §4.2.4 sample 的格式与截断
long_src = "x" * 55
g1b = Global(enforce_csp(**RTT))
try:
    set_inner_html(g1b, long_src)
except TrustedTypeError:
    pass
v = [x for x in g1b.violations if x.resource == "trusted-types-sink"][0]
eq("sample = sink 名 + '|' + 前 40 字符", v.sample, "Element innerHTML|" + "x" * 40)
eq("违规的 effective directive 是 require-trusted-types-for",
   v.directive, "require-trusted-types-for")

# --------------------------------------- §3.5 default policy 的三条分支
def sanitize(s, *a):
    return s.replace("<script>", "").replace("</script>", "")


g2 = Global(enforce_csp(**RTT, **{"trusted-types": "default"}))
create_policy(g2, "default", {"createHTML": sanitize})
eq("default policy 净化后的值被采用",
   set_inner_html(g2, "<script>alert(1)</script><b>ok</b>"), "alert(1)<b>ok</b>")

g3 = Global(enforce_csp(**RTT, **{"trusted-types": "default"}))
create_policy(g3, "default", {"createHTML": lambda s, *a: None})
raises("default policy 返回 null 且强制模式 → 抛 TypeError",
       lambda: set_inner_html(g3, "<img src=x onerror=alert(1)>"))
ck("null 返回也照样报违规（先报告再决定）",
   any(v.resource == "trusted-types-sink" for v in g3.violations))

# 规范原话：report-only 下 default policy 的拒绝被报告但**被忽略**
g4 = Global(report_csp(**RTT, **{"trusted-types": "default"}))
create_policy(g4, "default", {"createHTML": lambda s, *a: None})
eq("report-only 下 default policy 拒绝 → 返回原始值",
   set_inner_html(g4, "<b>raw</b>"), "<b>raw</b>")
ck("report-only 下仍产生违规报告",
   any(v.disposition == "report" for v in g4.violations))

g5 = Global(report_csp(**RTT))
eq("report-only 且无 default policy → 原始值直通",
   set_inner_html(g5, "<b>raw</b>"), "<b>raw</b>")
ck("report-only 直通时也有报告", bool(g5.violations))

# -------------------------------------------- §4.2.5 policy 创建的 CSP 闸门
gA = Global(enforce_csp(**{"trusted-types": "one two"}))
eq("白名单内的名字允许创建",
   should_policy_creation_be_blocked(gA, "one", []), "Allowed")
eq("白名单外的名字被 Blocked",
   should_policy_creation_be_blocked(gA, "three", []), "Blocked")
ck("被 Blocked 时资源是 trusted-types-policy",
   any(v.resource == "trusted-types-policy" for v in gA.violations))

gB = Global(enforce_csp(**{"trusted-types": "one two"}))
create_policy(gB, "one", {})
raises("重名且无 'allow-duplicates' → TypeError", lambda: create_policy(gB, "one", {}))

gC = Global(enforce_csp(**{"trusted-types": "one two 'allow-duplicates'"}))
create_policy(gC, "one", {})
try:
    create_policy(gC, "one", {})
    ck("'allow-duplicates' 允许重名", True)
except TrustedTypeError:
    ck("'allow-duplicates' 允许重名", False, "被拒")

gD = Global(enforce_csp(**{"trusted-types": "*"}))
eq("'*' 允许任意新名字",
   should_policy_creation_be_blocked(gD, "anything", []), "Allowed")
eq("'*' 也不允许重名（没有 allow-duplicates）",
   should_policy_creation_be_blocked(gD, "anything", ["anything"]), "Blocked")

gE = Global(enforce_csp(**{"trusted-types": "'none'"}))
eq("'none' 单独出现 → 任何 policy 都不能建",
   should_policy_creation_be_blocked(gE, "any", []), "Blocked")

gF = Global(enforce_csp(**{"trusted-types": "one 'none'"}))
eq("'none' 与其它名字并存时被忽略（规范 note）",
   should_policy_creation_be_blocked(gF, "one", []), "Allowed")

gG = Global(enforce_csp(**{"trusted-types": ""}))
eq("空指令值 → 一个 policy 都不能建",
   should_policy_creation_be_blocked(gG, "one", []), "Blocked")

# -------------------------------------------------------- §3.1 default 唯一
gH = Global(enforce_csp(**{"trusted-types": "default"}))
create_policy(gH, "default", {"createHTML": sanitize})
raises("default policy 不能建第二次", lambda: create_policy(gH, "default", {}))

# ------------------------------------------- §3.3 回调缺失 / 异常向上传播
gI = Global()
pol = create_policy(gI, "html-only", {"createHTML": lambda s, *a: s})
raises("未实现的 createScript 在 throwIfMissing 下抛错",
       lambda: create_trusted_type(pol, "TrustedScript", "1+1"))
eq("throwIfMissing=False 时返回 None",
   get_policy_value(pol, "TrustedScript", "1+1", [], False), None)


def hostile(url, *a):
    raise TrustedTypeError("invalid URL")


pol2 = create_policy(gI, "cdn", {"createScriptURL": hostile})
raises("policy 回调抛出的异常传播到调用点",
       lambda: create_trusted_type(pol2, "TrustedScriptURL", "https://x/a.js"))

# ------------------------------------------------------ §3.8 属性汇点映射
eq("onclick → TrustedScript, sink = 'Element onclick'",
   get_trusted_type_data_for_attribute(HTML_NS, "div", "onclick", None),
   ("TrustedScript", "Element onclick"))
eq("iframe srcdoc → TrustedHTML",
   get_trusted_type_data_for_attribute(HTML_NS, "iframe", "srcdoc", None),
   ("TrustedHTML", "HTMLIFrameElement srcdoc"))
eq("script src → TrustedScriptURL",
   get_trusted_type_data_for_attribute(HTML_NS, "script", "src", None),
   ("TrustedScriptURL", "HTMLScriptElement src"))
eq("SVG script href(xlink) → TrustedScriptURL",
   get_trusted_type_data_for_attribute(SVG_NS, "script", "href", XLINK_NS),
   ("TrustedScriptURL", "SVGScriptElement href"))
eq("普通属性不在表里 → 不强制",
   get_trusted_type_data_for_attribute(HTML_NS, "div", "id", None), None)
eq("on 开头的非事件属性也按事件处理器处理（规范的自认歧义）",
   get_trusted_type_data_for_attribute(HTML_NS, "div", "onmouseover", None),
   ("TrustedScript", "Element onmouseover"))

gJ = Global(enforce_csp(**RTT))
raises("强制模式下 onclick 拒绝裸字符串",
       lambda: set_attribute(gJ, HTML_NS, "div", "onclick", "alert(1)"))
url_pol = create_policy(gJ, "cdn", {"createScriptURL": lambda s, *a: s,
                                    "createScript": lambda s, *a: s,
                                    "createHTML": lambda s, *a: s})
eq("TrustedScript 可写入 onclick",
   set_attribute(gJ, HTML_NS, "div", "onclick",
                 create_trusted_type(url_pol, "TrustedScript", "handle()")),
   "handle()")
eq("不在表里的属性即使强制也直通",
   set_attribute(gJ, HTML_NS, "div", "id", "main"), "main")

# -------------------------------------------- 其它汇点：script.src / text
gK = Global(enforce_csp(**RTT))
raises("强制模式下 script.src 拒绝裸字符串",
       lambda: set_script_src(gK, "https://cdn.example/a.js"))
raises("强制模式下 script.text 拒绝裸字符串",
       lambda: set_script_text(gK, "alert(1)"))

# ------------------------------------- §4.2.1.1 javascript: 预导航检查
gL = Global(enforce_csp(**RTT))
eq("非 javascript: URL 直接 Allowed",
   require_tt_pre_navigation_check(gL, "https://example.com/")[0], "Allowed")
eq("无 default policy 时 javascript: 导航被 Blocked",
   require_tt_pre_navigation_check(gL, "javascript:alert(1)")[0], "Blocked")

gM = Global(enforce_csp(**RTT, **{"trusted-types": "default"}))
create_policy(gM, "default", {"createScript": lambda s, *a: s.upper()})
res = require_tt_pre_navigation_check(gM, "javascript:alert(1)")
eq("有 default policy 时 javascript: 导航 Allowed", res[0], "Allowed")
eq("且 URL 被 default policy 改写", res[1], "javascript:ALERT(1)")

# ------------------------------------------------------- §4.2.3 的两种口径
gN = Global(report_csp(**RTT))
ck("report-only 也算‘要求 TT’（include_report_only=True）",
   does_sink_type_require_trusted_types(gN, "script", True))
ck("但只看强制策略时不算",
   not does_sink_type_require_trusted_types(gN, "script", False))

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
