// RFC 9110 §13 条件请求 + §8.8.3 校验器比较 —— Go 版对照实现（仅标准库）。
//
// 口径来源（实读 rfc-editor.org/rfc/rfc9110.txt）：
//   - §8.8.3.2 Table 3：强比较要求"both are not weak"且 opaque-tag 逐字符相同；
//     弱比较只看 opaque-tag。→ 服务器给弱 ETag 时 If-Match **永远**不成立。
//   - §13.1.1 If-Match "MUST use the strong comparison function"；
//     If-Match 失败 MAY 回 2xx 若变更已被应用。
//   - §13.1.2 If-None-Match "MUST use the weak comparison function"；
//     条件为假时 GET/HEAD → 304，其他方法 → 412。
//   - §13.1.3 IMS 判据是 "last modification date is earlier or equal to the date provided
//     → condition is false"；有 If-None-Match 时 MUST ignore IMS；非 GET/HEAD 也 MUST ignore。
//   - §13.1.4 IUS 被 If-Match 屏蔽；判据是 last_modified <= date → true。
//   - §13.1.5 If-Range 是精确匹配（ETag 走强比较、日期走相等），与 IUS 的 "<=" 不同。
//   - §13.2.1 基线响应不是 2xx/412 时 MUST ignore 全部条件；CONNECT/OPTIONS/TRACE 忽略条件。
//   - §13.2.2 六步优先级：If-Match → IUS → INM → IMS → If-Range → 执行。
//
// 运行：go run conditional.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"strings"
)

type tag struct {
	weak   bool
	opaque string
}

func parseEntityTag(raw string) (tag, bool) {
	if len(raw) < 2 {
		return tag{}, false
	}
	weak := false
	body := raw
	if strings.HasPrefix(body, "W/") {
		weak = true
		body = body[2:]
	} else if strings.HasPrefix(body, "w/") {
		return tag{}, false // weak 前缀大小写敏感
	}
	if len(body) < 2 || body[0] != '"' || body[len(body)-1] != '"' {
		return tag{}, false
	}
	return tag{weak: weak, opaque: body[1 : len(body)-1]}, true
}

// StrongCompare 是 §8.8.3.2 的强比较。
func StrongCompare(a, b string) bool {
	ta, ok1 := parseEntityTag(a)
	tb, ok2 := parseEntityTag(b)
	if !ok1 || !ok2 {
		return false
	}
	return !ta.weak && !tb.weak && ta.opaque == tb.opaque
}

// WeakCompare 是 §8.8.3.2 的弱比较。
func WeakCompare(a, b string) bool {
	ta, ok1 := parseEntityTag(a)
	tb, ok2 := parseEntityTag(b)
	if !ok1 || !ok2 {
		return false
	}
	return ta.opaque == tb.opaque
}

// Resource 是目标资源的当前状态。
type Resource struct {
	Exists       bool
	ETag         string
	LastModified int64
}

// Response 携带状态码、原因与被忽略的条件头。
type Response struct {
	Status  int
	Reason  string
	Ignored []string
	Applied bool
}

type server struct {
	res        Resource
	baseStatus int
}

func (s *server) has(h map[string]string, k string) (string, bool) {
	v, ok := h[k]
	return v, ok
}

func splitTags(v string) []string {
	if strings.TrimSpace(v) == "*" {
		return []string{"*"}
	}
	var out []string
	for _, p := range strings.Split(v, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func (s *server) evalIfMatch(v string) bool {
	if len(splitTags(v)) == 1 && splitTags(v)[0] == "*" {
		return s.res.Exists
	}
	for _, t := range splitTags(v) {
		if StrongCompare(t, s.res.ETag) {
			return true
		}
	}
	return false
}

func (s *server) evalIfNoneMatch(v string) bool {
	if len(splitTags(v)) == 1 && splitTags(v)[0] == "*" {
		return !s.res.Exists
	}
	if !s.res.Exists {
		return true
	}
	for _, t := range splitTags(v) {
		if WeakCompare(t, s.res.ETag) {
			return false
		}
	}
	return true
}

// Handle 走 §13.2.2 的六步。日期统一用 epoch 秒，避免引入时间解析。
func (s *server) Handle(method string, h map[string]string, hasRange, already bool) Response {
	m := strings.ToUpper(method)
	var ignored []string
	if m == "CONNECT" || m == "OPTIONS" || m == "TRACE" {
		return Response{200, "method does not select a representation", nil, true}
	}
	base := s.baseStatus
	if !((base >= 200 && base < 300) || base == 412) {
		return Response{base, "baseline failure/redirect takes precedence", nil, false}
	}
	r := s.res

	if v, ok := s.has(h, "If-Match"); ok {
		if !s.evalIfMatch(v) {
			if already {
				return Response{200, "already applied (§13.1.1 MAY)", nil, false}
			}
			return Response{412, "If-Match failed", nil, false}
		}
	} else if v, ok := s.has(h, "If-Unmodified-Since"); ok {
		var ts int64
		fmt.Sscanf(v, "%d", &ts)
		if r.LastModified > ts {
			if already {
				return Response{200, "already applied (§13.1.4 MAY)", nil, false}
			}
			return Response{412, "If-Unmodified-Since failed", nil, false}
		}
	}

	if v, ok := s.has(h, "If-None-Match"); ok {
		if _, ok2 := s.has(h, "If-Modified-Since"); ok2 {
			ignored = append(ignored, "If-Modified-Since")
		}
		if !s.evalIfNoneMatch(v) {
			if m == "GET" || m == "HEAD" {
				return Response{304, "If-None-Match failed on GET/HEAD", ignored, false}
			}
			return Response{412, "If-None-Match failed on unsafe method", ignored, false}
		}
	} else if v, ok := s.has(h, "If-Modified-Since"); ok {
		if m != "GET" && m != "HEAD" {
			ignored = append(ignored, "If-Modified-Since(non-GET/HEAD)")
		} else {
			var ts int64
			fmt.Sscanf(v, "%d", &ts)
			if r.LastModified <= ts {
				return Response{304, "If-Modified-Since false", ignored, false}
			}
		}
	}

	if v, ok := s.has(h, "If-Range"); ok {
		if !(m == "GET" && hasRange) {
			ignored = append(ignored, "If-Range(no Range or non-GET)")
		} else if StrongCompare(v, r.ETag) {
			return Response{206, "If-Range true → partial content", ignored, true}
		} else {
			ignored = append(ignored, "Range(If-Range false)")
			return Response{200, "If-Range false → whole representation", ignored, true}
		}
	}
	return Response{200, "perform requested method", ignored, true}
}

func main() {
	fmt.Println("RFC 9110 §13 条件请求 —— Go 版")
	fmt.Println("W/1 vs 1  strong =", StrongCompare(`W/"1"`, `"1"`), " weak =", WeakCompare(`W/"1"`, `"1"`))
	fmt.Println("1  vs 1   strong =", StrongCompare(`"1"`, `"1"`), " weak =", WeakCompare(`"1"`, `"1"`))

	weak := Resource{true, `W/"1"`, 1000}
	strong := Resource{true, `"1"`, 1000}
	fmt.Println("弱 ETag 资源 + If-Match W/1 →",
		(&server{weak, 200}).Handle("PUT", map[string]string{"If-Match": `W/"1"`}, false, false).Status)
	fmt.Println("强 ETag 资源 + If-Match 1   →",
		(&server{strong, 200}).Handle("PUT", map[string]string{"If-Match": `"1"`}, false, false).Status)
	fmt.Println("INM 命中 → GET 返",
		(&server{strong, 200}).Handle("GET", map[string]string{"If-None-Match": `"1"`}, false, false).Status)
	fmt.Println("INM 命中 → POST 返",
		(&server{strong, 200}).Handle("POST", map[string]string{"If-None-Match": `"1"`}, false, false).Status)
	fmt.Println("基线 404 →",
		(&server{strong, 404}).Handle("GET", map[string]string{"If-None-Match": `"1"`}, false, false).Status)

	// lost update：后写者拿着旧 ETag
	old := `"v1"`
	cur := Resource{true, `"v2"`, 1001}
	fmt.Println("并发 PUT 不带 If-Match →",
		(&server{cur, 200}).Handle("PUT", nil, false, false).Status, "(覆盖)")
	fmt.Println("并发 PUT 带旧 ETag     →",
		(&server{cur, 200}).Handle("PUT", map[string]string{"If-Match": old}, false, false).Status)
}
