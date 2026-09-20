// Package main 实现 Subresource Integrity 与 Integrity-Policy 最小模型。
//
// 依据 W3C Subresource Integrity（https://www.w3.org/TR/SRI/ 全文实读）：
//   - §2 valid SRI hash algorithm token set 是**有序**集合
//     «sha256, sha384, sha512»，更强的排在后面。
//   - §3.3.1/§2 digest 用**标准 base64**（RFC 4648 §4，带 '+' '/' 与 '=' 填充）。
//   - §3.3.2 Parse metadata：按空格切分 → 每项按 '?' 切掉选项 → 按 '-' 切成
//     (算法, base64值)；不认识的算法跳过。
//   - §3.3.3 取最强：下标更大者胜出并清空结果集；**下标相同则都保留**。
//   - §3.3.4 解析结果为空集 → true；否则只用最强那批比对。note：SRI 需要 CORS。
//   - §3.7 校验失败 → network error + error 事件。
//   - §3.8 Integrity-Policy 是 RFC 9651 Dictionary；sources 只可能 "inline"
//     （缺省即 inline）；blocked-destinations 取 script / style。
//   - §3.8.2：有 integrity 且 mode ∈ {cors, same-origin} → Allowed；
//     local URL → Allowed；两个策略都空 → Allowed；report-only 只报不拦。
package main

import (
	"crypto/sha256"
	"crypto/sha512"
	"encoding/base64"
	"strings"
)

// ValidAlgos 是 §2 的有序集合。
var ValidAlgos = []string{"sha256", "sha384", "sha512"}

// DigestOf 实现 §3.3.1：算法作用于字节后再做标准 base64 编码。
func DigestOf(data []byte, algo string) string {
	var raw []byte
	switch algo {
	case "sha256":
		s := sha256.Sum256(data)
		raw = s[:]
	case "sha384":
		s := sha512.Sum384(data)
		raw = s[:]
	default:
		s := sha512.Sum512(data)
		raw = s[:]
	}
	return base64.StdEncoding.EncodeToString(raw)
}

// IntegrityMetadata 生成 "sha384-<digest>" 形式的元数据。
func IntegrityMetadata(data []byte, algo string) string {
	return algo + "-" + DigestOf(data, algo)
}

// Metadata 是解析后的单条完整性元数据。
type Metadata struct {
	Alg string
	Val string
}

// ParseMetadata 实现 §3.3.2。
func ParseMetadata(metadata string) []Metadata {
	var result []Metadata
	for _, item := range strings.Fields(metadata) {
		expression := strings.SplitN(item, "?", 2)[0]
		parts := strings.SplitN(expression, "-", 2)
		algo := strings.ToLower(parts[0])
		valid := false
		for _, a := range ValidAlgos {
			if a == algo {
				valid = true
			}
		}
		if !valid {
			continue
		}
		val := ""
		if len(parts) > 1 {
			val = parts[1]
		}
		result = append(result, Metadata{Alg: algo, Val: val})
	}
	return result
}

func indexOfAlgo(algo string) int {
	for i, a := range ValidAlgos {
		if a == algo {
			return i
		}
	}
	return -1
}

// GetStrongestMetadata 实现 §3.3.3。
func GetStrongestMetadata(parsed []Metadata) []Metadata {
	var result []Metadata
	var strongest Metadata
	first := true
	for _, item := range parsed {
		if first {
			result = append(result, item)
			strongest = item
			first = false
			continue
		}
		cur := indexOfAlgo(strongest.Alg)
		next := indexOfAlgo(item.Alg)
		if next < cur {
			continue
		}
		if next > cur {
			strongest = item
			result = []Metadata{item}
		} else {
			result = append(result, item)
		}
	}
	return result
}

// DoBytesMatch 实现 §3.3.4。
func DoBytesMatch(data []byte, metadata string) bool {
	parsed := ParseMetadata(metadata)
	if len(parsed) == 0 {
		return true
	}
	for _, item := range GetStrongestMetadata(parsed) {
		if DigestOf(data, item.Alg) == item.Val {
			return true
		}
	}
	return false
}

// VerifySubresource 建模 §3.3.4 note 的 CORS 要求。
func VerifySubresource(data []byte, metadata, urlOrigin, docOrigin string,
	crossorigin bool) (bool, string) {
	if urlOrigin != docOrigin && !crossorigin {
		return false, "cross-origin 且未声明 crossorigin：SRI 需要 CORS（§3.3.4 note）"
	}
	if !DoBytesMatch(data, metadata) {
		return false, "integrity 校验失败：返回 network error（§3.7）"
	}
	return true, "ok"
}

// IntegrityPolicy 是 §3.8 的 integrity policy 结构。
type IntegrityPolicy struct {
	Sources             []string
	BlockedDestinations []string
	Endpoints           []string
}

// IsEmpty 判断策略是否为空。
func (p *IntegrityPolicy) IsEmpty() bool {
	return len(p.BlockedDestinations) == 0 && len(p.Sources) == 0
}

// ProcessIntegrityPolicy 解析 RFC 9651 Dictionary 形式的策略值。
func ProcessIntegrityPolicy(dict map[string][]string) *IntegrityPolicy {
	p := &IntegrityPolicy{}
	sources, has := dict["sources"]
	if !has || contains(sources, "inline") {
		p.Sources = append(p.Sources, "inline")
	}
	for _, dest := range []string{"script", "style"} {
		if contains(dict["blocked-destinations"], dest) {
			p.BlockedDestinations = append(p.BlockedDestinations, dest)
		}
	}
	p.Endpoints = append(p.Endpoints, dict["endpoints"]...)
	return p
}

func contains(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

// PolicyContainer 持有强制策略与 report-only 策略。
type PolicyContainer struct {
	IntegrityPolicy            *IntegrityPolicy
	ReportOnlyIntegrityPolicy  *IntegrityPolicy
}

// IntegrityRequest 建模一次子资源请求。
type IntegrityRequest struct {
	URL         string
	Destination string
	Mode        string
	Integrity   string
	Local       bool
}

// IntegrityViolation 是 §3.8.3 的 IntegrityViolationReportBody。
type IntegrityViolation struct {
	DocumentURL string
	BlockedURL  string
	Destination string
	ReportOnly  bool
}

// ShouldRequestBeBlocked 实现 §3.8.2。
func ShouldRequestBeBlocked(r IntegrityRequest, c *PolicyContainer,
	documentURL string, violations *[]IntegrityViolation) string {
	parsed := ParseMetadata(r.Integrity)
	if len(parsed) > 0 && (r.Mode == "cors" || r.Mode == "same-origin") {
		return "Allowed"
	}
	if r.Local {
		return "Allowed"
	}
	policy := c.IntegrityPolicy
	reportPolicy := c.ReportOnlyIntegrityPolicy
	if policy.IsEmpty() && reportPolicy.IsEmpty() {
		return "Allowed"
	}
	block := contains(policy.Sources, "inline") &&
		contains(policy.BlockedDestinations, r.Destination)
	reportBlock := contains(reportPolicy.Sources, "inline") &&
		contains(reportPolicy.BlockedDestinations, r.Destination)
	if block || reportBlock {
		if violations != nil {
			v := IntegrityViolation{DocumentURL: documentURL, BlockedURL: r.URL,
				Destination: r.Destination, ReportOnly: false}
			if reportBlock && !block {
				v.ReportOnly = true
			}
			*violations = append(*violations, v)
		}
	}
	if block {
		return "Blocked"
	}
	return "Allowed"
}
