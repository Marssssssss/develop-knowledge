// COOPCOEP跨源隔离 自检（Go 侧）。
package main

import "fmt"

var okCount int
var failures []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
		return
	}
	failures = append(failures, fmt.Sprintf("%s  %s", name, detail))
}

func eqStr(name, got, want string) {
	ck(name, got == want, fmt.Sprintf("got=%q want=%q", got, want))
}

const (
	A  = "https://a.example"
	B  = "https://b.example"
	A2 = "https://a.example:8443"
)

func corp(embedder, mode, reqOrigin, urlOrigin, header, httpsState, scheme string) bool {
	return CorpInternalCheck(embedder, mode, reqOrigin, urlOrigin, header, httpsState, scheme)
}

func main() {
	// HTML §7.1.3 match
	ck("两边都是 unsafe-none → true",
		MatchOpenerPolicyValues(UnsafeNone, A, UnsafeNone, A), "")
	ck("两边都是 unsafe-none 即使跨源也 true",
		MatchOpenerPolicyValues(UnsafeNone, A, UnsafeNone, B), "")
	ck("一边 unsafe-none → false",
		!MatchOpenerPolicyValues(SameOrigin, A, UnsafeNone, A), "")
	ck("同为 same-origin 且同源 → true",
		MatchOpenerPolicyValues(SameOrigin, A, SameOrigin, A), "")
	ck("同为 same-origin 但跨源 → false",
		!MatchOpenerPolicyValues(SameOrigin, A, SameOrigin, B), "")
	ck("同为 same-origin 但端口不同 → false",
		!MatchOpenerPolicyValues(SameOrigin, A, SameOrigin, A2), "")
	ck("值不同即使同源也 false",
		!MatchOpenerPolicyValues(SameOrigin, A, SameOriginAllowPopups, A), "")
	ck("same-origin-plus-COEP 只与自身同源匹配",
		MatchOpenerPolicyValues(SameOriginPlusCOEP, A, SameOriginPlusCOEP, A), "")

	// §7.1.3.2 BCG switch
	ck("非 about:blank：同为 same-origin 同源 → 不切换",
		!CheckCOOPRequiresSwitch(false, A, A, SameOrigin, SameOrigin), "")
	ck("非 about:blank：同为 same-origin 跨源 → 切换",
		CheckCOOPRequiresSwitch(false, B, A, SameOrigin, SameOrigin), "")
	ck("非 about:blank：两边 unsafe-none → 不切换",
		!CheckCOOPRequiresSwitch(false, B, A, UnsafeNone, UnsafeNone), "")
	ck("非 about:blank：新页 unsafe-none 而旧页 same-origin → 切换",
		CheckCOOPRequiresSwitch(false, A, A, UnsafeNone, SameOrigin), "")

	ck("popup：noopener-allow-popups → 一律切换",
		CheckPopupCOOPRequiresSwitch(A, A, NoopenerAllowPopups, UnsafeNone), "")
	ck("popup：opener=same-origin-allow-popups + 新页 unsafe-none → 不切换",
		!CheckPopupCOOPRequiresSwitch(B, A, UnsafeNone, SameOriginAllowPopups), "")
	ck("popup：opener=noopener-allow-popups + 新页 unsafe-none → 不切换",
		!CheckPopupCOOPRequiresSwitch(B, A, UnsafeNone, NoopenerAllowPopups), "")
	ck("popup：opener=same-origin + 新页 unsafe-none → 切换",
		CheckPopupCOOPRequiresSwitch(A, A, UnsafeNone, SameOrigin), "")
	ck("popup：两边 same-origin 同源 → 不切换",
		!CheckPopupCOOPRequiresSwitch(A, A, SameOrigin, SameOrigin), "")

	// §7.1.3.1 obtain an opener policy
	eqStr("same-origin + 兼容 COEP → 升级为 same-origin-plus-COEP",
		ObtainOpenerPolicy(SameOrigin, true, true).Value, SameOriginPlusCOEP)
	eqStr("same-origin 但 COEP 不兼容 → 仍是 same-origin",
		ObtainOpenerPolicy(SameOrigin, false, true).Value, SameOrigin)
	eqStr("未设置 COOP → unsafe-none",
		ObtainOpenerPolicy("", true, true).Value, UnsafeNone)
	eqStr("非安全上下文：即使 COOP=same-origin 也回落 unsafe-none",
		ObtainOpenerPolicy(SameOrigin, true, false).Value, SameOrigin)
	eqStr("same-origin-plus-COEP 不能由头直接设置",
		ObtainOpenerPolicy(SameOriginPlusCOEP, true, true).Value, UnsafeNone)

	// COEP §2.3 fail-open 表
	eqStr("(无头) → unsafe-none", ObtainEmbedderPolicy("", "").Value, UnsafeNone)
	eqStr("require-corp → require-corp",
		ObtainEmbedderPolicy("require-corp", "").Value, RequireCorp)
	eqStr("unknown-value → unsafe-none",
		ObtainEmbedderPolicy("unknown-value", "").Value, UnsafeNone)
	eqStr("require-corp, unknown-value → unsafe-none",
		ObtainEmbedderPolicy("require-corp, unknown-value", "").Value, UnsafeNone)
	eqStr("unknown-value, unknown-value → unsafe-none",
		ObtainEmbedderPolicy("unknown-value, unknown-value", "").Value, UnsafeNone)
	eqStr("unknown-value, require-corp → unsafe-none",
		ObtainEmbedderPolicy("unknown-value, require-corp", "").Value, UnsafeNone)
	eqStr("require-corp, require-corp → unsafe-none（最反直觉）",
		ObtainEmbedderPolicy("require-corp, require-corp", "").Value, UnsafeNone)
	eqStr("report-only 只影响 report_only_value",
		ObtainEmbedderPolicy("", "require-corp").ReportOnlyValue, RequireCorp)
	eqStr("report-only 不影响强制值",
		ObtainEmbedderPolicy("", "require-corp").Value, UnsafeNone)

	// COEP §3.2.1 CORP internal check
	ck("mode=cors 直接放行", corp(RequireCorp, "cors", A, B, "", "none", "https"), "")
	ck("mode=same-origin 直接放行",
		corp(RequireCorp, "same-origin", A, B, "", "none", "https"), "")
	ck("mode=websocket 直接放行",
		corp(RequireCorp, "websocket", A, B, "", "none", "https"), "")
	ck("mode=navigate 且 embedder unsafe-none → 放行",
		corp(UnsafeNone, "navigate", A, B, "", "none", "https"), "")
	ck("require-corp 下无 CORP 头 → 按 same-origin 处理，跨源被拦",
		!corp(RequireCorp, "no-cors", A, B, "", "none", "https"), "")
	ck("require-corp 下无 CORP 头 → 同源放行",
		corp(RequireCorp, "no-cors", A, A, "", "none", "https"), "")
	ck("CORP: cross-origin 放行任何来源",
		corp(RequireCorp, "no-cors", A, B, "cross-origin", "none", "https"), "")
	ck("CORP: same-origin 同源放行",
		corp(RequireCorp, "no-cors", A, A, SameOrigin, "none", "https"), "")
	ck("CORP: same-origin 跨源拦截",
		!corp(RequireCorp, "no-cors", A, B, SameOrigin, "none", "https"), "")
	ck("CORP: same-site 同站 + https 放行",
		corp(RequireCorp, "no-cors", "https://x.a.example", "https://y.a.example",
			"same-site", "none", "https"), "")
	ck("CORP: same-site 跨站拦截",
		!corp(RequireCorp, "no-cors", "https://x.a.example", "https://y.b.example",
			"same-site", "none", "https"), "")
	ck("CORP: same-site 的 note —— 安全响应不匹配非安全发起源",
		!corp(RequireCorp, "no-cors", "http://x.a.example", "http://y.a.example",
			"same-site", "modern", "http"), "")
	ck("CORP: same-site 且响应非 https 传输 → 放行",
		corp(RequireCorp, "no-cors", "http://x.a.example", "http://y.a.example",
			"same-site", "none", "http"), "")
	ck("未知 CORP 值 → 放行（fail-open）",
		corp(RequireCorp, "no-cors", A, B, "some-future-value", "none", "https"), "")
	// CORP 独立于 COEP 生效
	ck("embedder unsafe-none 但响应带 CORP: same-origin → 跨源 no-cors 仍被拦",
		!corp(UnsafeNone, "no-cors", A, B, SameOrigin, "none", "https"), "")
	ck("embedder unsafe-none 且无 CORP 头 → 跨源 no-cors 放行",
		corp(UnsafeNone, "no-cors", A, B, "", "none", "https"), "")
	ck("navigate 是唯一对 unsafe-none 有短路的 mode",
		corp(UnsafeNone, "navigate", A, B, SameOrigin, "none", "https"), "")

	// COEP §3.1.3 / §4.3
	eqStr("parent require-corp + 子文档 unsafe-none → Blocked",
		CheckNavigationAdherence(RequireCorp, UnsafeNone), "Blocked")
	eqStr("parent require-corp + 子文档 require-corp → Allowed",
		CheckNavigationAdherence(RequireCorp, RequireCorp), "Allowed")
	eqStr("parent unsafe-none + 子文档 unsafe-none → Allowed",
		CheckNavigationAdherence(UnsafeNone, UnsafeNone), "Allowed")

	// 跨源隔离综合判定
	ck("COOP same-origin + COEP require-corp → crossOriginIsolated",
		CrossOriginIsolatedFromHeaders(SameOrigin, "require-corp", true), "")
	ck("只有 COOP same-origin（无 COEP）→ 不隔离",
		!CrossOriginIsolatedFromHeaders(SameOrigin, "", true), "")
	ck("只有 COEP require-corp（无 COOP）→ 不隔离",
		!CrossOriginIsolatedFromHeaders("", "require-corp", true), "")
	ck("COEP 写错（require-corp, require-corp）→ 静默失去隔离",
		!CrossOriginIsolatedFromHeaders(SameOrigin, "require-corp, require-corp", true), "")
	ck("非安全上下文 → 不隔离",
		!CrossOriginIsolatedFromHeaders(SameOrigin, "require-corp", false), "")
	ck("COOP same-origin-allow-popups 无法达到跨源隔离",
		!CrossOriginIsolatedFromHeaders(SameOriginAllowPopups, "require-corp", true), "")
	ck("同站判定：a.example 的两个子域算 same site",
		SameSite("x.a.example", "y.a.example"), "")
	ck("同站判定：不同注册域不算 same site", !SameSite("a.example", "b.example"), "")
	ck("跨源隔离页面与普通同源页面之间仍要切换 BCG",
		CheckCOOPRequiresSwitch(false, A, A, SameOriginPlusCOEP, SameOrigin), "")

	fmt.Println("OK =", okCount)
	if len(failures) > 0 {
		fmt.Println("FAILED =", len(failures))
		for _, f := range failures {
			fmt.Println("  -", f)
		}
	} else {
		fmt.Println("ALL OK")
	}
}
