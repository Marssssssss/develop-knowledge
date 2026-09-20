"""CSP strict-dynamic与违规报告 自检。

依据 W3C CSP3(https://www.w3.org/TR/CSP3/ 全文实读)的
§6.7.1.1 / §6.7.2.6 / §6.7.3.2 / §6.7.3.3 / §8.2 / §8.5 / §2.4 / §5 / §1。
"""

import base64
import hashlib

from csp_strict import (Element, Global, Policy, ScriptRequest, Violation,
                        allows_all_inline, create_violation, does_element_match,
                        does_url_match_source_list, is_strict_csp,
                        parse_source_list, script_pre_request_check,
                        DEPRECATED_DIRECTIVES)

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


ORIGIN = "https://example.com"

# ------------------------------------- §6.7.3.2 allow-all-inline 的分型
ck("只看 'unsafe-inline' → script 允许全部内联",
   allows_all_inline(["'unsafe-inline'"], "script"))
ck("'unsafe-inline' 'strict-dynamic' 在 script 下**不**允许全部内联",
   not allows_all_inline(["'unsafe-inline'", "'strict-dynamic'"], "script"))
ck("'strict-dynamic' 'unsafe-inline' 在 script 下同样不允许（先遇到即返回）",
   not allows_all_inline(["'strict-dynamic'", "'unsafe-inline'"], "script"))
ck("同一列表对 style 仍然允许全部内联（strict-dynamic 只管 script 系）",
   allows_all_inline(["'unsafe-inline'", "'strict-dynamic'"], "style"))
ck("同一列表对 'script attribute' 不允许",
   not allows_all_inline(["'unsafe-inline'", "'strict-dynamic'"], "script attribute"))
ck("http://example.com 'strict-dynamic' 在 script 下不允许",
   not allows_all_inline(["http://example.com", "'strict-dynamic'"], "script"))
ck("nonce-source 出现即不允许全部内联",
   not allows_all_inline(["'unsafe-inline'", "'nonce-abc'"], "script"))
ck("hash-source 出现即不允许全部内联",
   not allows_all_inline(["'unsafe-inline'", "'sha256-abc'"], "script"))
ck("只有 'unsafe-inline' 时 style 也允许",
   allows_all_inline(["'unsafe-inline'"], "style"))

# ------------------------------------- §6.7.3.3 元素匹配
NONCE = "DhcnhD3khTMePgXwdayK9BsMqXjhguVV"
dyn = ["'nonce-" + NONCE + "'", "'strict-dynamic'"]

ok_el = Element(nonce=NONCE, parser_inserted=True)
ck("parser-inserted 且 nonce 匹配 → Matches",
   does_element_match(ok_el, dyn, "script", ""))
bad_el = Element(nonce="wrong", parser_inserted=True)
ck("nonce 不匹配且不走 strict-dynamic 分支（parser-inserted）→ Does Not Match",
   not does_element_match(bad_el, dyn, "script", ""))
runtime_el = Element(nonce=None, parser_inserted=False)
ck("非 parser-inserted 的 script 元素被 strict-dynamic 放行",
   does_element_match(runtime_el, dyn, "script", ""))
writeln_el = Element(nonce=None, parser_inserted=True)
ck("document.write 产生的（parser-inserted）元素被 strict-dynamic 拒绝",
   not does_element_match(writeln_el, dyn, "script", ""))
ck("type 为 style 时 strict-dynamic 不起作用",
   not does_element_match(Element(parser_inserted=False), dyn, "style", ""))

# hash 匹配与 base64url 归一化
script_src = "alert(1);"
dig = base64.b64encode(hashlib.sha256(script_src.encode()).digest()).decode()
urlsafe = dig.replace("+", "-").replace("/", "_")          # 保留 padding
hash_list = ["'sha256-" + urlsafe + "'"]
ck("base64url 写的 hash 也能匹配（规范把 '-'→'+' '_'→'/' 归一化）",
   does_element_match(Element(parser_inserted=True), hash_list, "script", script_src))
# 口径标注：规范只规定了 '-'→'+'、'_'→'/' 的替换，未规定 padding 的处理方式。
# base64-value 语法允许 0~2 个 '='，本模型不做补齐，故去掉 padding 的形式不匹配。
unpadded = dig.replace("+", "-").replace("/", "_").rstrip("=")
ck("口径：去 padding 的写法在本模型下不匹配（规范未规定 padding 归一）",
   not does_element_match(Element(parser_inserted=True),
                          ["'sha256-" + unpadded + "'"], "script", script_src))
ck("hash 不匹配 → Does Not Match",
   not does_element_match(Element(parser_inserted=True), hash_list, "script",
                          "alert(2);"))
ck("事件处理器属性（script attribute）不吃 nonce（规范 note）",
   not does_element_match(Element(nonce=NONCE, parser_inserted=True),
                          dyn, "script attribute", ""))

# ------------------------------------- §6.7.1.1 script 请求的 pre-request check
ck("strict-dynamic + 非 parser-inserted 请求 → Allowed",
   script_pre_request_check(ScriptRequest("https://cdn.example/a.js",
                                          "not-parser-inserted"), dyn, ORIGIN)
   == "Allowed")
ck("strict-dynamic + parser-inserted 请求 → Blocked",
   script_pre_request_check(ScriptRequest("https://cdn.example/a.js",
                                          "parser-inserted"), dyn, ORIGIN)
   == "Blocked")
eq("host-source 在没有 strict-dynamic 时正常工作",
   script_pre_request_check(ScriptRequest("https://cdn.example/a.js",
                                          "not-parser-inserted"),
                            ["https://cdn.example"], ORIGIN), "Allowed")
eq("strict-dynamic 下 host-source 被忽略（cdn 不匹配也照样 Allowed）",
   script_pre_request_check(ScriptRequest("https://evil.example/a.js",
                                          "not-parser-inserted"), dyn, ORIGIN),
   "Allowed")
eq("'none' 单独出现 → Blocked",
   script_pre_request_check(ScriptRequest("https://cdn.example/a.js"),
                            ["'none'"], ORIGIN), "Blocked")
eq("空源列表等价于 'none' → Blocked",
   script_pre_request_check(ScriptRequest("https://cdn.example/a.js"),
                            [], ORIGIN), "Blocked")

# ------------------------------------- §6.7.2.6 URL 匹配细节
ck("'none' 与其它表达式并存时不起作用（«'none', https://example.com»）",
   does_url_match_source_list("https://example.com/a.js",
                              ["'none'", "https://example.com"], ORIGIN))
ck("只有 'none' 一项 → 不匹配",
   not does_url_match_source_list("https://example.com/a.js", ["'none'"], ORIGIN))
ck("空列表 → 不匹配",
   not does_url_match_source_list("https://example.com/a.js", [], ORIGIN))
ck("'self' 匹配本站",
   does_url_match_source_list("https://example.com/a.js", ["'self'"], ORIGIN))
ck("'self' 不匹配他站",
   not does_url_match_source_list("https://evil.example/a.js", ["'self'"], ORIGIN))
ck("scheme-source 'https:' 匹配任意 https 源",
   does_url_match_source_list("https://cdn.example/a.js", ["https:"], ORIGIN))

# ------------------------------------- §2.4 sample 与报告体
g = Global(document_url="https://example.com/page", status_code=200,
           source_file="https://example.com/page", line_number=7,
           column_number=13)
pol = Policy("script-src 'nonce-abc' 'strict-dynamic'", "enforce",
             **{"script-src": "'nonce-abc' 'strict-dynamic'",
                "report-to": "csp-endpoint"})
long_src = "y" * 90
v = create_violation(g, pol, "script-src", source=long_src)
eq("sample 截断到前 40 字符", v.sample, "y" * 40)
eq("disposition 为 enforce", v.disposition, "enforce")
body = v.as_report_body()
ck("报告体带 sourceFile / lineNumber / columnNumber",
   body.get("sourceFile") == "https://example.com/page"
   and body.get("lineNumber") == 7 and body.get("columnNumber") == 13, str(body))

ext = create_violation(g, pol, "script-src", blocked_url="https://cdn.example/a.js")
eq("外部文件违规不带 sample", ext.sample, "")
ck("外部文件违规带 blockedURL", ext.as_report_body().get("blockedURL")
   == "https://cdn.example/a.js")

# §1:report-uri 已弃用,report-to 基于 REPORTING
ck("report-uri 被标记弃用", "report-uri" in DEPRECATED_DIRECTIVES)
eq("report-to 端点被识别", pol.reporting_endpoints()[0], "csp-endpoint")
pol2 = Policy("script-src 'self'; report-uri /r", "enforce",
              **{"script-src": "'self'", "report-uri": "/r"})
eq("report-uri 仍然可用但标注 deprecated",
   pol2.reporting_endpoints()[0][0], "deprecated")

# ------------------------------------- §8.5 Strict CSP 判定
strict = Policy("script-src 'strict-dynamic' 'nonce-X'; base-uri 'self'",
                "enforce", **{"script-src": "'strict-dynamic' 'nonce-X'",
                              "base-uri": "'self'"})
ck("nonce + strict-dynamic + base-uri 'self' 是 Strict CSP", is_strict_csp(strict))
hash_strict = Policy("s", "enforce",
                     **{"script-src": "'strict-dynamic' 'sha256-abc'",
                        "base-uri": "'none'"})
ck("hash + strict-dynamic + base-uri 'none' 也是 Strict CSP", is_strict_csp(hash_strict))
not_strict = Policy("s", "enforce",
                    **{"script-src": "'strict-dynamic' 'nonce-X' https://cdn.example",
                       "base-uri": "'self'"})
ck("混了 host-source 就不是 Strict CSP", not is_strict_csp(not_strict))
no_base = Policy("s", "enforce",
                 **{"script-src": "'strict-dynamic' 'nonce-X'"})
ck("缺 base-uri 也不是 Strict CSP", not is_strict_csp(no_base))
no_dyn = Policy("s", "enforce",
                **{"script-src": "'nonce-X'", "base-uri": "'self'"})
ck("只有 nonce 没有 strict-dynamic 不是 Strict CSP", not is_strict_csp(no_dyn))

# ------------------------------------- §8.2 的原文警告（可观测后果）
ck("strict-dynamic 下运行时脚本的 URL 若可被攻击者控制 → 任意脚本被放行",
   script_pre_request_check(ScriptRequest("https://attacker.example/x.js",
                                          "not-parser-inserted"), dyn, ORIGIN)
   == "Allowed")

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
