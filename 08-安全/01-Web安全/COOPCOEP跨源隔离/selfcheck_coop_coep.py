"""COOPCOEP跨源隔离 自检。

依据：
  * HTML 标准 §7.1.3 Cross-origin opener policies（https://html.spec.whatwg.org/multipage/browsers.html 实读）
  * WICG Cross-Origin-Embedder-Policy（https://wicg.github.io/cross-origin-embedder-policy/ 实读）
    —— 含 §2.3 的 fail-open 表与 §3.2.1 的 cross-origin resource policy internal check
"""

from coop_coep import (NOOPENER_ALLOW_POPUPS, REQUIRE_CORP, SAME_ORIGIN,
                       SAME_ORIGIN_ALLOW_POPUPS, SAME_ORIGIN_PLUS_COEP,
                       UNSAFE_NONE, check_coop_requires_switch,
                       check_navigation_adherence,
                       check_popup_coop_requires_switch,
                       coep_compatible_with_cross_origin_isolation,
                       corp_internal_check, cross_origin_isolated_from_headers,
                       is_cross_origin_isolated, match_opener_policy_values,
                       obtain_embedder_policy, obtain_opener_policy,
                       same_site)

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


A = "https://a.example"
B = "https://b.example"
A2 = "https://a.example:8443"

# ---------------------------------------- HTML §7.1.3 match opener policy values
ck("两边都是 unsafe-none → true",
   match_opener_policy_values(UNSAFE_NONE, A, UNSAFE_NONE, A))
ck("两边都是 unsafe-none 即使跨源也 true",
   match_opener_policy_values(UNSAFE_NONE, A, UNSAFE_NONE, B))
ck("一边 unsafe-none → false",
   not match_opener_policy_values(SAME_ORIGIN, A, UNSAFE_NONE, A))
ck("同为 same-origin 且同源 → true",
   match_opener_policy_values(SAME_ORIGIN, A, SAME_ORIGIN, A))
ck("同为 same-origin 但跨源 → false",
   not match_opener_policy_values(SAME_ORIGIN, A, SAME_ORIGIN, B))
ck("同为 same-origin 但端口不同（不同源）→ false",
   not match_opener_policy_values(SAME_ORIGIN, A, SAME_ORIGIN, A2))
ck("值不同（same-origin vs same-origin-allow-popups）即使同源也 false",
   not match_opener_policy_values(SAME_ORIGIN, A, SAME_ORIGIN_ALLOW_POPUPS, A))
ck("same-origin-plus-COEP 只与 same-origin-plus-COEP 同源匹配",
   match_opener_policy_values(SAME_ORIGIN_PLUS_COEP, A,
                              SAME_ORIGIN_PLUS_COEP, A))

# --------------------------------------------------------- §7.1.3.2 BCG switch
ck("非 about:blank 导航：同为 same-origin 同源 → 不切换",
   not check_coop_requires_switch(False, A, A, SAME_ORIGIN, SAME_ORIGIN))
ck("非 about:blank 导航：同为 same-origin 跨源 → 切换",
   check_coop_requires_switch(False, B, A, SAME_ORIGIN, SAME_ORIGIN))
ck("非 about:blank 导航：两边 unsafe-none → 不切换",
   not check_coop_requires_switch(False, B, A, UNSAFE_NONE, UNSAFE_NONE))
ck("非 about:blank 导航：新页面 unsafe-none 而旧页 same-origin → 切换",
   check_coop_requires_switch(False, A, A, UNSAFE_NONE, SAME_ORIGIN))

ck("popup：responseCOOP=noopener-allow-popups → 一律切换",
   check_popup_coop_requires_switch(A, A, NOOPENER_ALLOW_POPUPS, UNSAFE_NONE))
ck("popup：opener 是 same-origin-allow-popups 且新页 unsafe-none → 不切换",
   not check_popup_coop_requires_switch(B, A, UNSAFE_NONE,
                                        SAME_ORIGIN_ALLOW_POPUPS))
ck("popup：opener 是 noopener-allow-popups 且新页 unsafe-none → 不切换",
   not check_popup_coop_requires_switch(B, A, UNSAFE_NONE,
                                        NOOPENER_ALLOW_POPUPS))
ck("popup：opener 是 same-origin 而新页 unsafe-none → 切换",
   check_popup_coop_requires_switch(A, A, UNSAFE_NONE, SAME_ORIGIN))
ck("popup：两边 same-origin 同源 → 不切换",
   not check_popup_coop_requires_switch(A, A, SAME_ORIGIN, SAME_ORIGIN))

# --------------------------------------- §7.1.3.1 obtain an opener policy
eq("same-origin + 兼容 COEP → 升级为 same-origin-plus-COEP",
   obtain_opener_policy(SAME_ORIGIN, True)["value"], SAME_ORIGIN_PLUS_COEP)
eq("same-origin 但 COEP 不兼容 → 仍是 same-origin",
   obtain_opener_policy(SAME_ORIGIN, False)["value"], SAME_ORIGIN)
eq("未设置 COOP → unsafe-none",
   obtain_opener_policy(None, True)["value"], UNSAFE_NONE)
eq("非安全上下文：即使 COOP=same-origin 也回落到 unsafe-none",
   obtain_opener_policy(SAME_ORIGIN, True, secure_context=False)["value"],
   UNSAFE_NONE)
eq("same-origin-plus-COEP 不能由头直接设置（未知 token 回落 unsafe-none）",
   obtain_opener_policy(SAME_ORIGIN_PLUS_COEP, True)["value"], UNSAFE_NONE)

# --------------------------------------- COEP §2.3 的 fail-open 表（官方原表）
eq("(无头)                      → unsafe-none",
   obtain_embedder_policy(None)["value"], UNSAFE_NONE)
eq("require-corp                → require-corp",
   obtain_embedder_policy("require-corp")["value"], REQUIRE_CORP)
eq("unknown-value               → unsafe-none",
   obtain_embedder_policy("unknown-value")["value"], UNSAFE_NONE)
eq("require-corp, unknown-value → unsafe-none（合并成 list 不是 token）",
   obtain_embedder_policy("require-corp, unknown-value")["value"], UNSAFE_NONE)
eq("unknown-value, unknown-value→ unsafe-none",
   obtain_embedder_policy("unknown-value, unknown-value")["value"], UNSAFE_NONE)
eq("unknown-value, require-corp → unsafe-none",
   obtain_embedder_policy("unknown-value, require-corp")["value"], UNSAFE_NONE)
eq("require-corp, require-corp  → unsafe-none（最反直觉的一条）",
   obtain_embedder_policy("require-corp, require-corp")["value"], UNSAFE_NONE)
eq("report-only 头只影响 report_only_value",
   obtain_embedder_policy(None, "require-corp")["report_only_value"], REQUIRE_CORP)
eq("report-only 不影响强制值",
   obtain_embedder_policy(None, "require-corp")["value"], UNSAFE_NONE)

# -------------------------------- COEP §3.2.1 cross-origin resource policy check
ck("mode=cors 直接放行（CORS 已经做了授权）",
   corp_internal_check(REQUIRE_CORP, "cors", A, B, None))
ck("mode=same-origin 直接放行",
   corp_internal_check(REQUIRE_CORP, "same-origin", A, B, None))
ck("mode=websocket 直接放行",
   corp_internal_check(REQUIRE_CORP, "websocket", A, B, None))
ck("mode=navigate 且 embedder 是 unsafe-none → 放行",
   corp_internal_check(UNSAFE_NONE, "navigate", A, B, None))

ck("require-corp 下没有 CORP 头 → 按 same-origin 处理，跨源被拦",
   not corp_internal_check(REQUIRE_CORP, "no-cors", A, B, None))
ck("require-corp 下没有 CORP 头 → 同源放行",
   corp_internal_check(REQUIRE_CORP, "no-cors", A, A, None))
ck("CORP: cross-origin 显式放行任何来源",
   corp_internal_check(REQUIRE_CORP, "no-cors", A, B, "cross-origin"))
ck("CORP: same-origin 同源放行",
   corp_internal_check(REQUIRE_CORP, "no-cors", A, A, SAME_ORIGIN))
ck("CORP: same-origin 跨源拦截",
   not corp_internal_check(REQUIRE_CORP, "no-cors", A, B, SAME_ORIGIN))
ck("CORP: same-site 同站 + https 放行",
   corp_internal_check(REQUIRE_CORP, "no-cors", "https://x.a.example",
                       "https://y.a.example", "same-site"))
ck("CORP: same-site 跨站拦截",
   not corp_internal_check(REQUIRE_CORP, "no-cors", "https://x.a.example",
                           "https://y.b.example", "same-site"))
ck("CORP: same-site 的 note —— 安全响应不匹配非安全发起源",
   not corp_internal_check(REQUIRE_CORP, "no-cors", "http://x.a.example",
                           "http://y.a.example", "same-site",
                           response_https_state="modern",
                           request_scheme="http"))
ck("CORP: same-site 且响应不是 https 传输（state=none）→ 放行",
   corp_internal_check(REQUIRE_CORP, "no-cors", "http://x.a.example",
                       "http://y.a.example", "same-site",
                       response_https_state="none", request_scheme="http"))
ck("未知 CORP 值 → 放行（fail-open）",
   corp_internal_check(REQUIRE_CORP, "no-cors", A, B, "some-future-value"))
# 关键：CORP **独立于 COEP** 生效 —— 没开 COEP 的站点也能用 CORP 自保
ck("embedder 是 unsafe-none 但响应带 CORP: same-origin → 跨源 no-cors 仍被拦",
   not corp_internal_check(UNSAFE_NONE, "no-cors", A, B, SAME_ORIGIN))
ck("embedder 是 unsafe-none 且没有 CORP 头 → 跨源 no-cors 放行",
   corp_internal_check(UNSAFE_NONE, "no-cors", A, B, None))
ck("navigate 是唯一对 unsafe-none 有短路的 mode（跨源 iframe 不受 CORP 影响）",
   corp_internal_check(UNSAFE_NONE, "navigate", A, B, SAME_ORIGIN))

# --------------------------------- COEP §3.1.3 / §4.3 嵌套文档必须自己声明
eq("parent require-corp + 子文档 unsafe-none → Blocked",
   check_navigation_adherence(REQUIRE_CORP, UNSAFE_NONE), "Blocked")
eq("parent require-corp + 子文档 require-corp → Allowed",
   check_navigation_adherence(REQUIRE_CORP, REQUIRE_CORP), "Allowed")
eq("parent unsafe-none + 子文档 unsafe-none → Allowed",
   check_navigation_adherence(UNSAFE_NONE, UNSAFE_NONE), "Allowed")

# ------------------------------------------------------ 跨源隔离的综合判定
ck("COOP same-origin + COEP require-corp → crossOriginIsolated",
   cross_origin_isolated_from_headers(SAME_ORIGIN, "require-corp"))
ck("只有 COOP same-origin（无 COEP）→ 不隔离",
   not cross_origin_isolated_from_headers(SAME_ORIGIN, None))
ck("只有 COEP require-corp（无 COOP）→ 不隔离",
   not cross_origin_isolated_from_headers(None, "require-corp"))
ck("COEP 头写错（require-corp, require-corp）→ 静默失去隔离",
   not cross_origin_isolated_from_headers(SAME_ORIGIN, "require-corp, require-corp"))
ck("非安全上下文 → 不隔离",
   not cross_origin_isolated_from_headers(SAME_ORIGIN, "require-corp", secure=False))
ck("COOP same-origin-allow-popups 无法达到跨源隔离",
   not cross_origin_isolated_from_headers(SAME_ORIGIN_ALLOW_POPUPS, "require-corp"))
ck("同站判定：a.example 的两个子域算 same site",
   same_site("x.a.example", "y.a.example"))
ck("同站判定：不同注册域不算 same site",
   not same_site("a.example", "b.example"))

# 组合语义：same-origin-plus-COEP 只与自身匹配，因此 COOP 不同的文档无法共享 BCG
ck("跨源隔离页面与普通同源页面之间仍要切换 BCG（值不同）",
   check_coop_requires_switch(False, A, A, SAME_ORIGIN_PLUS_COEP, SAME_ORIGIN))

print("OK =", OK)
if FAIL:
    print("FAILED =", len(FAIL))
    for f in FAIL:
        print("  -", f)
else:
    print("ALL OK")
