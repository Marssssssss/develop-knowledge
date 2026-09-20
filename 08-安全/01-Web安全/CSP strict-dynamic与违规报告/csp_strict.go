// Package main 实现 CSP3 的 'strict-dynamic' 与违规报告最小模型。
//
// 依据 W3C Content Security Policy Level 3（https://www.w3.org/TR/CSP3/ 全文实读）：
//   - §6.7.1.1 script 指令 pre-request check：'none' → Blocked；SRI 匹配 →
//     Allowed；**含 'strict-dynamic' 时：parser-inserted → Blocked，否则 Allowed**；
//     否则走 URL 匹配。
//   - §6.7.2.6：空源列表与单元素 'none' → 不匹配；'none' 与其它表达式并存时
//     不起作用。
//   - §6.7.3.2：遇 nonce/hash → 立即 Does Not Allow；type ∈ {script,
//     script attribute, navigation} 且遇 'strict-dynamic' → 立即 Does Not Allow；
//     'unsafe-inline' 置 allow。
//   - §6.7.3.3：allow-all-inline → Matches；type ∈ {script, style} 且元素
//     nonceable 时比对 nonce；遇 'strict-dynamic' 且 type == script 且元素非
//     parser-inserted → Matches；hash 比对前把 '-'→'+'、'_'→'/'。
//   - §8.2：strict-dynamic 下 host/scheme/'self'/'unsafe-inline' 在加载 script
//     时被忽略；document.write 产生的元素算 parser-inserted。
//   - §8.5 Strict CSP：script-src 只用 nonce/hash + strict-dynamic，且 base-uri
//     为 'self' 或 'none'。
//   - §2.4 / §5：sample 取前 40 字符；外部文件违规不带 sample；report-uri 已弃用。
package main

import (
	"crypto/sha256"
	"crypto/sha512"
	"encoding/base64"
	"strings"
)

// NoncePrefix 与 HashPrefixes 用于识别源表达式的种类。
const NoncePrefix = "'nonce-"

// HashPrefixes 是三种受支持的 hash-source 前缀。
var HashPrefixes = []string{"'sha256-", "'sha384-", "'sha512-"}

// DeprecatedDirectives 是被弃用的指令（§1：report-uri 已被 report-to 取代）。
var DeprecatedDirectives = []string{"report-uri"}

// ParseSourceList 按空白切分源列表。
func ParseSourceList(value string) []string {
	v := strings.TrimSpace(value)
	if v == "" {
		return nil
	}
	return strings.Fields(v)
}

func isKeyword(expr, name string) bool {
	return strings.EqualFold(expr, "'"+name+"'")
}

func isNonceSource(expr string) bool {
	return strings.HasPrefix(expr, NoncePrefix) && strings.HasSuffix(expr, "'")
}

func isHashSource(expr string) bool {
	for _, p := range HashPrefixes {
		if strings.HasPrefix(expr, p) && strings.HasSuffix(expr, "'") {
			return true
		}
	}
	return false
}

// DoesURLMatchSourceList 实现 §6.7.2.6。
func DoesURLMatchSourceList(url string, list []string, selfOrigin string) bool {
	if len(list) == 0 {
		return false
	}
	if len(list) == 1 && isKeyword(list[0], "none") {
		return false
	}
	for _, expr := range list {
		if isKeyword(expr, "none") {
			continue
		}
		if isKeyword(expr, "self") {
			if strings.HasPrefix(url, selfOrigin) {
				return true
			}
			continue
		}
		if strings.HasSuffix(expr, ":") && !strings.Contains(expr, "://") {
			if strings.HasPrefix(url, expr) {
				return true
			}
			continue
		}
		if expr == "*" || strings.HasPrefix(url, expr) {
			return true
		}
	}
	return false
}

// AllowsAllInline 实现 §6.7.3.2。
func AllowsAllInline(list []string, elementType string) bool {
	allow := false
	for _, expr := range list {
		if isNonceSource(expr) || isHashSource(expr) {
			return false
		}
		if (elementType == "script" || elementType == "script attribute" ||
			elementType == "navigation") && isKeyword(expr, "strict-dynamic") {
			return false
		}
		if isKeyword(expr, "unsafe-inline") {
			allow = true
		}
	}
	return allow
}

// Element 建模一个 HTML 元素。
type Element struct {
	Tag            string
	Nonce          string
	ParserInserted bool
	Nonceable      bool
}

// NewElement 构造元素，默认 parser-inserted 且 nonceable。
func NewElement(nonce string, parserInserted bool) Element {
	return Element{Tag: "script", Nonce: nonce, ParserInserted: parserInserted,
		Nonceable: true}
}

func base64Digest(algo, source string) string {
	switch algo {
	case "sha256":
		s := sha256.Sum256([]byte(source))
		return base64.StdEncoding.EncodeToString(s[:])
	case "sha384":
		s := sha512.Sum384([]byte(source))
		return base64.StdEncoding.EncodeToString(s[:])
	default:
		s := sha512.Sum512([]byte(source))
		return base64.StdEncoding.EncodeToString(s[:])
	}
}

// DoesElementMatch 实现 §6.7.3.3。
func DoesElementMatch(el Element, list []string, elementType, source string) bool {
	if AllowsAllInline(list, elementType) {
		return true
	}
	if (elementType == "script" || elementType == "style") && el.Nonceable {
		for _, expr := range list {
			if isNonceSource(expr) {
				expected := expr[len(NoncePrefix) : len(expr)-1]
				if el.Nonce != "" && el.Nonce == expected {
					return true
				}
			}
		}
	}
	for _, expr := range list {
		if isKeyword(expr, "strict-dynamic") {
			if elementType == "script" && !el.ParserInserted {
				return true
			}
			continue
		}
		if isHashSource(expr) {
			var algo, expected string
			switch {
			case strings.HasPrefix(expr, "'sha256-"):
				algo, expected = "sha256", expr[len("'sha256-"):len(expr)-1]
			case strings.HasPrefix(expr, "'sha384-"):
				algo, expected = "sha384", expr[len("'sha384-"):len(expr)-1]
			case strings.HasPrefix(expr, "'sha512-"):
				algo, expected = "sha512", expr[len("'sha512-"):len(expr)-1]
			default:
				continue
			}
			expected = strings.ReplaceAll(strings.ReplaceAll(expected, "-", "+"), "_", "/")
			if base64Digest(algo, source) == expected {
				return true
			}
		}
	}
	return false
}

// ScriptRequest 建模一次脚本请求。
type ScriptRequest struct {
	URL            string
	ParserMetadata string
	Integrity      string
}

func integrityMatches(integrity string, list []string) bool {
	if integrity == "" {
		return false
	}
	for _, expr := range list {
		if !isHashSource(expr) {
			continue
		}
		if strings.Contains(expr, integrity) {
			return true
		}
	}
	return false
}

// ScriptPreRequestCheck 实现 §6.7.1.1。
func ScriptPreRequestCheck(r ScriptRequest, list []string, selfOrigin string) string {
	if len(list) == 0 || (len(list) == 1 && isKeyword(list[0], "none")) {
		return "Blocked"
	}
	if integrityMatches(r.Integrity, list) {
		return "Allowed"
	}
	for _, expr := range list {
		if isKeyword(expr, "strict-dynamic") {
			if r.ParserMetadata == "parser-inserted" {
				return "Blocked"
			}
			return "Allowed"
		}
	}
	if DoesURLMatchSourceList(r.URL, list, selfOrigin) {
		return "Allowed"
	}
	return "Blocked"
}
