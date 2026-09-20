// Package main 实现 COOP / COEP / CORP 跨源隔离最小模型。
//
// 依据（全部实读）：
//   - HTML 标准 §7.1.3 Cross-origin opener policies
//     （https://html.spec.whatwg.org/multipage/browsers.html）：
//     五种 opener policy value、match opener policy values、
//     check if (popup) COOP values require a browsing context group switch、
//     obtain an opener policy（非安全上下文直接回落 unsafe-none；same-origin
//     且 COEP 与跨源隔离兼容时升级为 same-origin-plus-COEP）。
//   - WICG Cross-Origin-Embedder-Policy
//     （https://wicg.github.io/cross-origin-embedder-policy/）：
//     §2.3 的 fail-open 表（require-corp, require-corp → unsafe-none）、
//     §3.2.1 的 cross-origin resource policy internal check、
//     §3.1.3 / §4.3 的 cascading vs requiring embedder policies。
package main

import "strings"

// opener policy value 常量。
const (
	UnsafeNone            = "unsafe-none"
	RequireCorp           = "require-corp"
	SameOrigin            = "same-origin"
	SameOriginAllowPopups = "same-origin-allow-popups"
	SameOriginPlusCOEP    = "same-origin-plus-COEP"
	NoopenerAllowPopups   = "noopener-allow-popups"
)

// SameOriginCheck 判定两个 origin 是否同源（本模型直接用字符串相等）。
func SameOriginCheck(a, b string) bool { return a == b }

// RegistrableDomain 是教学用的简化 same-site 判定：取最后两个标签。
func RegistrableDomain(host string) string {
	parts := strings.Split(host, ".")
	if len(parts) >= 2 {
		return strings.Join(parts[len(parts)-2:], ".")
	}
	return host
}

// SameSite 判定两个主机是否同站。
func SameSite(a, b string) bool {
	return RegistrableDomain(a) == RegistrableDomain(b)
}

// MatchOpenerPolicyValues 实现 HTML §7.1.3 的 match。
func MatchOpenerPolicyValues(docCOOP, docOrigin, respCOOP, respOrigin string) bool {
	if docCOOP == UnsafeNone && respCOOP == UnsafeNone {
		return true
	}
	if docCOOP == UnsafeNone || respCOOP == UnsafeNone {
		return false
	}
	if docCOOP == respCOOP && SameOriginCheck(docOrigin, respOrigin) {
		return true
	}
	return false
}

// CheckPopupCOOPRequiresSwitch 实现 popup 变体。
func CheckPopupCOOPRequiresSwitch(respOrigin, activeOrigin, respCOOP,
	activeCOOP string) bool {
	if respCOOP == NoopenerAllowPopups {
		return true
	}
	if (activeCOOP == SameOriginAllowPopups || activeCOOP == NoopenerAllowPopups) &&
		respCOOP == UnsafeNone {
		return false
	}
	if MatchOpenerPolicyValues(activeCOOP, activeOrigin, respCOOP, respOrigin) {
		return false
	}
	return true
}

// CheckCOOPRequiresSwitch 实现非 popup 变体。
func CheckCOOPRequiresSwitch(isInitialAboutBlank bool, respOrigin, activeOrigin,
	respCOOP, activeCOOP string) bool {
	if isInitialAboutBlank {
		return CheckPopupCOOPRequiresSwitch(respOrigin, activeOrigin, respCOOP, activeCOOP)
	}
	if MatchOpenerPolicyValues(activeCOOP, activeOrigin, respCOOP, respOrigin) {
		return false
	}
	return true
}

// OpenerPolicy 是 opener policy 结构。
type OpenerPolicy struct {
	Value           string
	ReportOnlyValue string
}

// ObtainOpenerPolicy 实现 §7.1.3.1。
func ObtainOpenerPolicy(coopToken string, coepCompatibleWithIsolation,
	secureContext bool) OpenerPolicy {
	p := OpenerPolicy{Value: UnsafeNone, ReportOnlyValue: UnsafeNone}
	if !secureContext {
		return p
	}
	switch coopToken {
	case SameOrigin:
		if coepCompatibleWithIsolation {
			p.Value = SameOriginPlusCOEP
		} else {
			p.Value = SameOrigin
		}
	case SameOriginAllowPopups:
		p.Value = SameOriginAllowPopups
	case NoopenerAllowPopups:
		p.Value = NoopenerAllowPopups
	}
	return p
}

// EmbedderPolicy 是 embedder policy 结构（COEP §3.1）。
type EmbedderPolicy struct {
	Value                       string
	ReportOnlyValue             string
	ReportingEndpoint           string
	ReportOnlyReportingEndpoint string
}

// parseSHToken 极简 Structured Header item(token)解析。
func parseSHToken(raw string) string {
	v := strings.TrimSpace(raw)
	if v == "" || strings.Contains(v, ",") {
		return ""
	}
	return v
}

// ObtainEmbedderPolicy 实现 COEP §2.3：解析失败一律 fail-open。
func ObtainEmbedderPolicy(coepHeader, reportOnlyHeader string) EmbedderPolicy {
	p := EmbedderPolicy{Value: UnsafeNone, ReportOnlyValue: UnsafeNone}
	if parseSHToken(coepHeader) == RequireCorp {
		p.Value = RequireCorp
	}
	if parseSHToken(reportOnlyHeader) == RequireCorp {
		p.ReportOnlyValue = RequireCorp
	}
	return p
}

// COEPCompatibleWithCrossOriginIsolation 判断 COEP 是否兼容跨源隔离。
func COEPCompatibleWithCrossOriginIsolation(p EmbedderPolicy) bool {
	return p.Value == RequireCorp
}

// CorpInternalCheck 实现 COEP §3.2.1 的 internal check。
func CorpInternalCheck(embedderPolicyValue, mode, requestOrigin, currentURLOrigin,
	corpHeader, responseHTTPSState, requestScheme string) bool {
	if mode == "same-origin" || mode == "cors" || mode == "websocket" {
		return true
	}
	if mode == "navigate" && embedderPolicyValue == UnsafeNone {
		return true
	}
	policy := corpHeader
	if policy == "" && embedderPolicyValue == RequireCorp {
		policy = SameOrigin
	}
	switch policy {
	case "", "cross-origin":
		return true
	case SameOrigin:
		return SameOriginCheck(requestOrigin, currentURLOrigin)
	case "same-site":
		return SameSite(requestOrigin, currentURLOrigin) &&
			(requestScheme == "https" || responseHTTPSState == "none")
	}
	return true
}

// CheckNavigationAdherence 实现 COEP §3.1.3 / §4.3。
func CheckNavigationAdherence(parentPolicyValue, childPolicyValue string) string {
	if parentPolicyValue != RequireCorp {
		return "Allowed"
	}
	if childPolicyValue == UnsafeNone {
		return "Blocked"
	}
	return "Allowed"
}

// IsCrossOriginIsolated 判定是否达到跨源隔离。
func IsCrossOriginIsolated(coopValue string, coep EmbedderPolicy) bool {
	if coopValue != SameOriginPlusCOEP {
		return false
	}
	return COEPCompatibleWithCrossOriginIsolation(coep)
}

// CrossOriginIsolatedFromHeaders 从两个响应头直接判定跨源隔离。
func CrossOriginIsolatedFromHeaders(coopToken, coepHeader string, secure bool) bool {
	coep := ObtainEmbedderPolicy(coepHeader, "")
	coop := ObtainOpenerPolicy(coopToken,
		COEPCompatibleWithCrossOriginIsolation(coep), secure)
	return IsCrossOriginIsolated(coop.Value, coep)
}
