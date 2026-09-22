// 606 HATEOAS 三种表示形态对照 —— Go 版（仅标准库）。
//
// 口径来源（与 Python 版同一批实读资料）：
//   - HAL draft-kelly-json-hal-08：_links 的值是 Link Object 或数组；href REQUIRED；
//     href 为 URI Template 时 templated SHOULD 为 true；CURIE 由根资源上 rel=curies 的
//     一组 Link Object 建立，href 含 {rel} token；§8.3 hypertext cache pattern 允许
//     客户端优先读同 rel 的嵌入资源。
//   - Siren：class MUST 是字符串数组；link.rel MUST 是非空字符串数组；action.name
//     在实体内唯一；有 fields 且未给 type 时默认 application/x-www-form-urlencoded。
//   - JSON:API v1.1：成员名首尾必须是「全局允许字符」且不含保留字符（@ 仅可在首位）；
//     Content-Type 上除 ext/profile 之外的媒体类型参数 → 415；
//     sort 字段按给出顺序应用，`-` 前缀为降序。
//
// 运行：go run hateoas.go jsonapi.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"regexp"
	"strings"
)

// -------------------------------------------------------------- JSON:API

const jsonapiMediaType = "application/vnd.api+json"

var globallyAllowed = regexp.MustCompile(`[A-Za-z0-9\x{0080}-\x{10FFFF}]`)
var middleAllowed = regexp.MustCompile(`[A-Za-z0-9\x{0080}-\x{10FFFF}_\- ]`)

var reservedChars = map[rune]bool{
	'+': true, ',': true, '.': true, '[': true, ']': true, '!': true,
	'"': true, '#': true, '$': true, '%': true, '&': true, '\'': true,
	'(': true, ')': true, '*': true, '/': true, ':': true, ';': true,
	'<': true, '=': true, '>': true, '?': true, '\\': true, '^': true,
	'`': true, '{': true, '|': true, '}': true, '~': true, '\u007f': true,
}

// IsLegalMemberName 判定 JSON:API 成员名（§Member Names）。
func IsLegalMemberName(name string) bool {
	runes := []rune(name)
	if len(runes) == 0 {
		return false
	}
	for i, r := range runes {
		if r == '@' && i != 0 {
			return false
		}
		if reservedChars[r] && r != '@' {
			return false
		}
		if runes[i] < 0x20 {
			return false
		}
	}
	body := runes
	if runes[0] == '@' {
		body = runes[1:]
	}
	if len(body) == 0 {
		return false
	}
	if !globallyAllowed.MatchString(string(body[0])) {
		return false
	}
	if !globallyAllowed.MatchString(string(body[len(body)-1])) {
		return false
	}
	for _, r := range body[1 : len(body)-1] {
		if !middleAllowed.MatchString(string(r)) {
			return false
		}
	}
	return true
}

// NegotiateContentType 返回 (决策, 状态码)：带 ext/profile 之外的参数即 415。
func NegotiateContentType(contentType string) (string, int) {
	parts := strings.Split(contentType, ";")
	if strings.TrimSpace(strings.ToLower(parts[0])) != jsonapiMediaType {
		return "not-jsonapi", 200
	}
	for _, p := range parts[1:] {
		p = strings.TrimSpace(strings.ToLower(p))
		if p == "" {
			continue
		}
		name := p
		if i := strings.Index(p, "="); i >= 0 {
			name = strings.TrimSpace(p[:i])
		}
		if name != "ext" && name != "profile" {
			return "unsupported-param", 415
		}
	}
	return "ok", 200
}

// ParseSort 把 sort 参数解析成 (字段, 是否降序) 序列。
func ParseSort(value string) [][2]interface{} {
	out := [][2]interface{}{}
	for _, field := range strings.Split(value, ",") {
		field = strings.TrimSpace(field)
		if field == "" {
			continue
		}
		if strings.HasPrefix(field, "-") {
			out = append(out, [2]interface{}{field[1:], true})
		} else {
			out = append(out, [2]interface{}{field, false})
		}
	}
	return out
}

// IsValidFamilyName 判定「基名 + 方括号」的参数族形状。
func IsValidFamilyName(name string) bool {
	base := name
	rest := ""
	if i := strings.Index(name, "["); i >= 0 {
		base, rest = name[:i], name[i:]
	}
	if !IsLegalMemberName(base) {
		return false
	}
	re := regexp.MustCompile(`\[([^\[\]]*)\]`)
	for _, m := range re.FindAllStringSubmatch(rest, -1) {
		if m[1] == "" {
			continue
		}
		for _, member := range strings.Split(m[1], ".") {
			if !IsLegalMemberName(member) {
				return false
			}
		}
	}
	// 出现未闭合的方括号则不合法
	if strings.Contains(rest, "[") && !re.MatchString(rest) {
		return false
	}
	return true
}

// ValidateQueryParam 实现自定义参数必须至少含一个非 a-z 字符（否则 400）。
func ValidateQueryParam(name string) bool {
	if !IsValidFamilyName(name) {
		return false
	}
	base := name
	if i := strings.Index(name, "["); i >= 0 {
		base = name[:i]
	}
	for _, r := range base {
		if r < 'a' || r > 'z' {
			return true
		}
	}
	return false
}
