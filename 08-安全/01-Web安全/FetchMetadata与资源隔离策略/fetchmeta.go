// Package main 实现 Fetch Metadata 请求头最小模型。
//
// 依据 W3C《Fetch Metadata Request Headers》
// （https://w3c.github.io/webappsec-fetch-metadata/ 140594 B 全文实读）：
//   - §2 四个头都是 RFC 9651 Structured Field：
//     Sec-Fetch-Dest/Mode/Site = sf-token，Sec-Fetch-User = sf-boolean。
//   - §2.3 Site 算法：初值 same-origin；用户显式触发的导航 → none；
//     遍历 url list：同源 continue，否则先置 cross-site，
//     不同站才 break，同站则改为 same-site。
//   - §2.4 User：非导航请求或 user-activation 为假 → 不发这个头。
//   - §3 追加前先判 potentially trustworthy URL，不满足则一个头都不发。
//   - §4.1 重定向走完整个 url list：任一跨站即 cross-site。
//   - §4.2 Sec- 前缀 → forbidden response-header name → JS 不可伪造。
package main

import "strings"

// 规范 §2 示例里明确给出的五个 destination（集合视为开放集）
var knownDest = map[string]bool{
	"empty": true, "image": true, "worker": true, "document": true, "iframe": true,
}

// ValidMode 是 §2.2 的合法取值。
var ValidMode = map[string]bool{
	"cors": true, "navigate": true, "no-cors": true, "same-origin": true, "websocket": true,
}

// ValidSite 是 §2.3 的合法取值。
var ValidSite = map[string]bool{
	"cross-site": true, "same-origin": true, "same-site": true, "none": true,
}

// 简化 Public Suffix List
var publicSuffixes = map[string]bool{"com": true, "net": true, "org": true, "co.uk": true}

// RegistrableDomain 求 eTLD+1。先试更长的后缀，避免把 co.uk 当成 RD。
func RegistrableDomain(host string) string {
	labels := strings.Split(strings.ToLower(strings.TrimRight(host, ".")), ".")
	for _, n := range []int{3, 2, 1} {
		if len(labels) > n {
			suffix := strings.Join(labels[len(labels)-n:], ".")
			if publicSuffixes[suffix] {
				return strings.Join(labels[len(labels)-n-1:], ".")
			}
		}
	}
	if len(labels) >= 2 {
		return strings.Join(labels[len(labels)-2:], ".")
	}
	return strings.ToLower(host)
}

// Origin 是一个源。
type Origin struct {
	Scheme string
	Host   string
	Port   int
}

func defaultPort(scheme string) int {
	switch scheme {
	case "https", "wss":
		return 443
	case "http", "ws":
		return 80
	}
	return 0
}

func (o Origin) effectivePort() int {
	if o.Port != 0 {
		return o.Port
	}
	return defaultPort(o.Scheme)
}

// SameOrigin 判断同源：scheme + host + 有效端口全等。
func (o Origin) SameOrigin(other Origin) bool {
	return o.Scheme == other.Scheme && o.Host == other.Host &&
		o.effectivePort() == other.effectivePort()
}

// SameSite 取 URL 的「schemelessly same site」：registrable domain 相同。
// 规范完整的 "same site" 还要求 scheme 相容，本 demo 只取域这一半。
func (o Origin) SameSite(other Origin) bool {
	return RegistrableDomain(o.Host) == RegistrableDomain(other.Host)
}

// Req 是一个请求；URLList 是重定向链上的全部 URL。
type Req struct {
	Origin         Origin
	URLList        []Origin
	Dest           string
	Mode           string
	Navigation     bool
	UserActivation bool
	UserInitiated  bool
	Trustworthy    bool
}

// SetSecFetchSite 实现 §2.3，返回 (值, 实际检查过的 url 个数)。
func SetSecFetchSite(r Req) (string, int) {
	value := "same-origin"
	examined := 0
	if r.Navigation && r.UserInitiated {
		return "none", 0
	}
	for _, url := range r.URLList {
		examined++
		if r.Origin.SameOrigin(url) {
			continue
		}
		value = "cross-site"
		if !r.Origin.SameSite(url) {
			break
		}
		value = "same-site"
	}
	return value, examined
}

// SetSecFetchUser 实现 §2.4，返回空串表示这个头不出现。
func SetSecFetchUser(r Req) string {
	if !r.Navigation || !r.UserActivation {
		return ""
	}
	return "?1"
}

// AppendFetchMetadata 实现 §3，返回最终发出的 Sec-Fetch-* 头。
func AppendFetchMetadata(r Req) map[string]string {
	if !r.Trustworthy {
		return map[string]string{}
	}
	site, _ := SetSecFetchSite(r)
	h := map[string]string{
		"Sec-Fetch-Dest": r.Dest,
		"Sec-Fetch-Mode": r.Mode,
		"Sec-Fetch-Site": site,
	}
	if u := SetSecFetchUser(r); u != "" {
		h["Sec-Fetch-User"] = u
	}
	return h
}

// NormalizeIncoming 服务端侧规范化。
// §2.2/§2.3 要求 Mode 与 Site 的非法值被忽略；规范对 Dest 无此要求，故原样透传。
func NormalizeIncoming(h map[string]string) map[string]string {
	out := map[string]string{}
	if d, ok := h["Sec-Fetch-Dest"]; ok {
		out["dest"] = d
	}
	if m, ok := h["Sec-Fetch-Mode"]; ok && ValidMode[m] {
		out["mode"] = m
	}
	if s, ok := h["Sec-Fetch-Site"]; ok && ValidSite[s] {
		out["site"] = s
	}
	return out
}

// IsolationPolicy 资源隔离策略示例 —— 工程惯例，不是规范条文。
func IsolationPolicy(h map[string]string) string {
	n := NormalizeIncoming(h)
	site, hasSite := n["site"]
	if !hasSite {
		return "allow" // 老浏览器：fail-open
	}
	if n["mode"] == "navigate" || n["dest"] == "document" {
		if site == "same-origin" || site == "same-site" || site == "none" {
			return "allow"
		}
		return "block"
	}
	if site == "same-origin" || site == "same-site" {
		return "allow"
	}
	return "block"
}

// IsForbiddenResponseHeaderName 实现 §4.2 的 Sec- 前缀禁令。
func IsForbiddenResponseHeaderName(name string) bool {
	low := strings.ToLower(name)
	if strings.HasPrefix(low, "sec-") {
		return true
	}
	return low == "set-cookie" || low == "set-cookie2"
}

// JSSetHeader 模拟 JS 侧设置请求头，返回是否成功。
func JSSetHeader(h map[string]string, name, value string) bool {
	if IsForbiddenResponseHeaderName(name) {
		return false
	}
	h[name] = value
	return true
}
