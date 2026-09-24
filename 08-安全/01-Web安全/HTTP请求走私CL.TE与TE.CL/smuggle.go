// Package main 实现 HTTP/1.1 请求走私最小模型。
//
// 依据 RFC 9112（https://www.rfc-editor.org/rfc/rfc9112.txt 全文实读）：
//   - §6.3 消息体长度八条判定，**按优先级顺序**：
//     1) HEAD 响应 / 1xx / 204 / 304 → 首空行即结束；
//     2) CONNECT 的 2xx 响应 → 隧道，忽略 CL 与 TE；
//     3) TE 与 CL 并存 → TE 覆盖 CL，且是走私嫌疑；
//     4) TE 存在：chunked 为最终编码 → 按 chunked；
//     否则请求 → 400，响应 → 读到关闭；
//     5) 无 TE 且 CL 非法 → 400（除非逗号列表全合法且全相同）；
//     6) 无 TE 且 CL 合法 → 定长；7) 请求 → 0；8) 响应 → 读到关闭。
//   - §7.1 chunk = chunk-size [ chunk-ext ] CRLF chunk-data CRLF；
//     last-chunk = 1*("0") [ chunk-ext ] CRLF，随后 trailer-section CRLF。
//   - §11.2 请求走私 = 利用不同接收方的解析差异隐藏额外请求。
package main

import (
	"strings"
)

// FramingKind 是 §6.3 的判定种类。
type FramingKind string

// §6.3 的六种判定种类。
const (
	KindNone    FramingKind = "none"
	KindTunnel  FramingKind = "tunnel"
	KindChunked FramingKind = "chunked"
	KindClose   FramingKind = "close"
	KindError   FramingKind = "error"
	KindLength  FramingKind = "length"
)

// Framing 是 §6.3 的一条判定结果。
type Framing struct {
	Rule    int
	Kind    FramingKind
	Value   int
	Suspect bool
}

// Headers 是保序、大小写不敏感的头部集合。
type Headers struct {
	Pairs [][2]string
}

// GetAll 返回同名头部的全部值。
func (h Headers) GetAll(name string) []string {
	var out []string
	for _, p := range h.Pairs {
		if strings.EqualFold(p[0], name) {
			out = append(out, p[1])
		}
	}
	return out
}

// Get 返回同名头部按逗号拼接的结果，缺失时返回空串与 false。
func (h Headers) Get(name string) (string, bool) {
	vs := h.GetAll(name)
	if len(vs) == 0 {
		return "", false
	}
	return strings.Join(vs, ", "), true
}

func isToken(s string) bool {
	if s == "" {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		ok := (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')
		if !ok && strings.IndexByte("!#$%&'*+-.^_`|~", c) < 0 {
			return false
		}
	}
	return true
}

// ParseTE 解析 Transfer-Encoding：名字大小写不敏感，逗号分隔。
// 返回小写编码列表；遇到非法 token 返回 nil。
func ParseTE(value string) []string {
	var out []string
	for _, part := range strings.Split(value, ",") {
		tok := strings.TrimSpace(part)
		if tok == "" {
			continue
		}
		if !isToken(tok) {
			return nil
		}
		out = append(out, strings.ToLower(tok))
	}
	return out
}

// ParseCL 解析 Content-Length：逗号列表须全为十进制且全相同。
func ParseCL(value string) (int, bool) {
	parts := strings.Split(value, ",")
	if len(parts) == 0 {
		return 0, false
	}
	seen := -1
	for _, p := range parts {
		s := strings.TrimSpace(p)
		if s == "" {
			return 0, false
		}
		for i := 0; i < len(s); i++ {
			if s[i] < '0' || s[i] > '9' {
				return 0, false
			}
		}
		n := 0
		for i := 0; i < len(s); i++ {
			n = n*10 + int(s[i]-'0')
		}
		if seen < 0 {
			seen = n
		} else if seen != n {
			return 0, false
		}
	}
	return seen, true
}

// DetermineFraming 按 §6.3 八条判定的优先级顺序给出结果。
func DetermineFraming(h Headers, isRequest bool, method string, status int) Framing {
	teRaw, hasTE := h.Get("Transfer-Encoding")
	clRaw, hasCL := h.Get("Content-Length")
	var te []string
	if hasTE {
		te = ParseTE(teRaw)
	}

	if !isRequest {
		if method == "HEAD" {
			return Framing{1, KindNone, 0, false}
		}
		if (status >= 100 && status < 200) || status == 204 || status == 304 {
			return Framing{1, KindNone, 0, false}
		}
		if method == "CONNECT" && status >= 200 && status < 300 {
			return Framing{2, KindTunnel, 0, false}
		}
	}

	if len(te) > 0 {
		suspect := hasCL
		rule := 4
		if suspect {
			rule = 3
		}
		if te[len(te)-1] == "chunked" {
			return Framing{rule, KindChunked, 0, suspect}
		}
		if isRequest {
			return Framing{4, KindError, 400, suspect}
		}
		return Framing{4, KindClose, 0, suspect}
	}

	if hasCL {
		if n, ok := ParseCL(clRaw); ok {
			return Framing{6, KindLength, n, false}
		}
		return Framing{5, KindError, 400, false}
	}

	if isRequest {
		return Framing{7, KindNone, 0, false}
	}
	return Framing{8, KindClose, 0, false}
}
