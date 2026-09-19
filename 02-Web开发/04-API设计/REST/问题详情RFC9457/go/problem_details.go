// RFC 9457 Problem Details for HTTP APIs —— Go 版对照实现（仅标准库）。
//
// 口径来源（实读 rfc-editor.org/rfc/rfc9457.txt 全文）：
//   - §3.1    "If a member's value type does not match the specified type, the member MUST be
//     ignored" —— 类型不符不是报错，而是当作该成员不存在。
//   - §3.1.1  type 缺省 "about:blank"；相对 URI 按文档 base URI 解析，故不同资源上的同名
//     相对 type 会解析成不同绝对 URI。
//   - §3.1.2  status "is only advisory"，但生成器 MUST 与真实 HTTP 状态码一致。
//   - §3.2    "Clients consuming problem details MUST ignore any such extensions that they
//     don't recognize"。
//   - §4      扩展名 SHOULD 以 ALPHA 开头、只由 ALPHA/DIGIT/"_" 组成、且 SHOULD >= 3 字符
//     （为了能序列化成 XML）。
//   - §4.2.1  about:blank 的 title SHOULD 等于该状态码的推荐短语。
//   - 附录 A  非规范性 JSON Schema：status 为 integer，minimum 100 / maximum 599。
//
// 运行：go run problem_details.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"sort"
	"strings"
)

const (
	// MediaType 是 JSON 变体的媒体类型。
	MediaType = "application/problem+json"
	// DefaultType 是 §4.2.1 注册的唯一问题类型。
	DefaultType = "about:blank"
)

// statusPhrases 收 RFC 9110 §15.5/§15.6 的推荐短语（本 demo 用到的几条）。
var statusPhrases = map[int]string{
	400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
	409: "Conflict", 412: "Precondition Failed", 422: "Unprocessable Content",
	429: "Too Many Requests", 500: "Internal Server Error", 503: "Service Unavailable",
}

// Problem 是 §3 的 problem details 对象。
type Problem struct {
	Type       string
	Status     int // 0 表示成员缺失或被忽略
	Title      string
	Detail     string
	Instance   string
	Extensions map[string]interface{}
	Ignored    []string
}

// isStringVal / isIntVal 复刻"成员类型不符即忽略"。
func isStringVal(v interface{}) bool {
	_, ok := v.(string)
	return ok
}

func isIntVal(v interface{}) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case float64:
		if n == float64(int(n)) {
			return int(n), true
		}
		return 0, false
	default:
		return 0, false
	}
}

// IsValidExtensionName 复刻 §4 的命名 SHOULD 规则。
func IsValidExtensionName(name string) bool {
	if len(name) < 3 {
		return false
	}
	c := name[0]
	if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) {
		return false
	}
	for i := 0; i < len(name); i++ {
		ch := name[i]
		ok := (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') ||
			(ch >= '0' && ch <= '9') || ch == '_'
		if !ok {
			return false
		}
	}
	return true
}

// RemoveDotSegments 是 RFC 3986 §5.2.4 的路径规范化。
func RemoveDotSegments(path string) string {
	var out []string
	for path != "" {
		switch {
		case strings.HasPrefix(path, "../"):
			path = path[3:]
		case strings.HasPrefix(path, "./"):
			path = path[2:]
		case strings.HasPrefix(path, "/./"):
			path = "/" + path[3:]
		case path == "/.":
			path = "/"
		case strings.HasPrefix(path, "/../"):
			path = "/" + path[4:]
			if len(out) > 0 {
				out = out[:len(out)-1]
			}
		case path == "/..":
			path = "/"
			if len(out) > 0 {
				out = out[:len(out)-1]
			}
		case path == "." || path == "..":
			path = ""
		default:
			idx := strings.Index(path[1:], "/")
			if idx < 0 {
				out = append(out, path)
				path = ""
			} else {
				out = append(out, path[:idx+1])
				path = path[idx+1:]
			}
		}
	}
	return strings.Join(out, "")
}

// ResolveURI 把 ref 相对 base 解析成绝对 URI（只覆盖 http/https 场景）。
func ResolveURI(base, ref string) string {
	if strings.Contains(ref, "://") {
		return ref
	}
	scheme := base[:strings.Index(base, "://")]
	rest := base[strings.Index(base, "://")+3:]
	authority := rest
	basePath := ""
	if i := strings.Index(rest, "/"); i >= 0 {
		authority = rest[:i]
		basePath = rest[i:]
	}
	prefix := scheme + "://" + authority
	switch {
	case strings.HasPrefix(ref, "//"):
		return scheme + ":" + ref
	case strings.HasPrefix(ref, "/"):
		return prefix + RemoveDotSegments(ref)
	case ref == "":
		return prefix + RemoveDotSegments(basePath)
	}
	merged := "/" + ref
	if j := strings.LastIndex(basePath, "/"); j >= 0 {
		merged = basePath[:j] + "/" + ref
	}
	return prefix + RemoveDotSegments(merged)
}

// Parse 从已解码的 JSON 对象构造 Problem，记录被忽略的成员。
func Parse(raw map[string]interface{}, baseURI string) *Problem {
	p := &Problem{Type: DefaultType, Extensions: map[string]interface{}{}}
	if v, ok := raw["type"]; ok {
		if isStringVal(v) {
			p.Type = v.(string)
		} else {
			p.Ignored = append(p.Ignored, "type")
		}
	}
	if v, ok := raw["status"]; ok {
		if n, ok2 := isIntVal(v); ok2 && n >= 100 && n <= 599 {
			p.Status = n
		} else {
			p.Ignored = append(p.Ignored, "status")
		}
	}
	for name, dst := range map[string]*string{"title": &p.Title, "detail": &p.Detail, "instance": &p.Instance} {
		if v, ok := raw[name]; ok {
			if isStringVal(v) {
				*dst = v.(string)
			} else {
				p.Ignored = append(p.Ignored, name)
			}
		}
	}
	reserved := map[string]bool{"type": true, "status": true, "title": true, "detail": true, "instance": true}
	for k, v := range raw {
		if !reserved[k] {
			p.Extensions[k] = v
		}
	}
	if baseURI != "" {
		if !strings.Contains(p.Type, "://") {
			p.Type = ResolveURI(baseURI, p.Type)
		}
		if p.Instance != "" && !strings.Contains(p.Instance, "://") {
			p.Instance = ResolveURI(baseURI, p.Instance)
		}
	}
	return p
}

// TitleForDisplay 复刻 §4.2.1：about:blank 且无 title 时用状态码推荐短语。
func (p *Problem) TitleForDisplay() string {
	if p.Type == DefaultType && p.Title == "" && p.Status != 0 {
		return statusPhrases[p.Status]
	}
	return p.Title
}

// StatusConsistent 检查生成器是否做到了 status 成员与真实状态码一致。
func (p *Problem) StatusConsistent(httpStatus int) bool {
	return p.Status == 0 || p.Status == httpStatus
}

// Build 是生成器侧：不给 type 就用 about:blank 并自动补推荐短语。
func Build(httpStatus int, typeURI, title, detail string, ext map[string]interface{}) *Problem {
	p := &Problem{Status: httpStatus, Extensions: map[string]interface{}{}}
	if typeURI != "" {
		p.Type = typeURI
		p.Title = title
	} else {
		p.Type = DefaultType
		p.Title = statusPhrases[httpStatus]
	}
	p.Detail = detail
	for k, v := range ext {
		p.Extensions[k] = v
	}
	return p
}

func (p *Problem) render() string {
	var b strings.Builder
	fmt.Fprintf(&b, "type=%s status=%d title=%q\n", p.Type, p.Status, p.TitleForDisplay())
	var keys []string
	for k := range p.Extensions {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintf(&b, "  ext %s = %v (name ok: %v)\n", k, p.Extensions[k], IsValidExtensionName(k))
	}
	if len(p.Ignored) > 0 {
		fmt.Fprintf(&b, "  ignored: %s\n", strings.Join(p.Ignored, ","))
	}
	return b.String()
}

func main() {
	fmt.Println("RFC 9457 Problem Details —— Go 版")
	fmt.Println("media type =", MediaType)

	good := map[string]interface{}{
		"type":     "https://example.com/probs/out-of-credit",
		"title":    "You do not have enough credit.",
		"detail":   "Your current balance is 30, but that costs 50.",
		"instance": "/account/12345/msgs/abc",
		"balance":  float64(30),
		"accounts": []interface{}{"/account/12345", "/account/67890"},
	}
	fmt.Print(Parse(good, "").render())

	bad := map[string]interface{}{"status": "404", "title": float64(1)}
	fmt.Print(Parse(bad, "").render())

	rel := map[string]interface{}{"type": "example-problem"}
	fmt.Println("相对 type @ /foo/bar/123 →",
		Parse(rel, "https://api.example.org/foo/bar/123").Type)
	fmt.Println("相对 type @ /widget/456  →",
		Parse(rel, "https://api.example.org/widget/456").Type)

	b := Build(404, "", "", "", nil)
	fmt.Printf("build(404): type=%s title=%s consistent=%v\n",
		b.Type, b.Title, b.StatusConsistent(404))
	fmt.Println("422 里写 404 → consistent =", b.StatusConsistent(422))
	fmt.Println("remove_dot_segments(/a/b/../c) =", RemoveDotSegments("/a/b/../c"))
}
