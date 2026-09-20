// Go 侧对照实现（二）：RFC 9111 §4.1 的 Vary 匹配、§4.3.1 的条件请求生成，
// 以及 RFC 9110 §13.2.2 的前置条件优先级。
//
// §13.2.2 的顺序（前两条**只对 origin server 有意义**，缓存不得评估）：
//   1. If-Match            不匹配 → 412
//   2. If-Unmodified-Since 不满足 → 412
//   3. If-None-Match       命中   → GET/HEAD 回 304，其它方法回 412
//   4. If-Modified-Since   未修改 → 304（仅 GET/HEAD 且无 If-None-Match）
//   5. If-Range + Range    满足   → 206（仅 GET）
//   6. 否则执行方法
package main

import (
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
)

// caseInsensitive 是值可大小写归一化的字段集合（按各字段自身的规范）。
var caseInsensitive = map[string]bool{"accept-encoding": true, "accept-charset": true}

func normalize(name, value string) string {
	kept := make([]string, 0, 4)
	for _, p := range strings.Split(value, ",") {
		p = strings.TrimSpace(p)
		if p != "" {
			kept = append(kept, p)
		}
	}
	joined := strings.Join(kept, ", ")
	if caseInsensitive[name] {
		return strings.ToLower(joined)
	}
	return joined
}

// VaryMatches 对应 §4.1：可归一化空白与（该字段允许的）大小写；
// 缺席只能对缺席；"*" 恒不匹配。
func VaryMatches(storedReq, newReq map[string]string, vary string) bool {
	if strings.TrimSpace(vary) == "" {
		return true
	}
	for _, f := range strings.Split(vary, ",") {
		f = strings.ToLower(strings.TrimSpace(f))
		if f == "" {
			continue
		}
		if f == "*" {
			return false // 「always fails to match」
		}
		a, aOK := storedReq[f]
		b, bOK := newReq[f]
		if aOK != bOK {
			return false // 缺席只能对缺席
		}
		if !aOK {
			continue
		}
		if normalize(f, a) != normalize(f, b) {
			return false
		}
	}
	return true
}

// BuildValidationRequest 对应 §4.3.1：有 ETag 必须发 If-None-Match；
// 单条且非子范围且有 Last-Modified 时应该发 If-Modified-Since。
func BuildValidationRequest(r *Resp, subrange bool) map[string]string {
	out := map[string]string{}
	if etag, ok := r.Hdr["etag"]; ok {
		out["if-none-match"] = etag // MUST
	}
	if !subrange {
		if lm, ok := r.Hdr["last-modified"]; ok {
			out["if-modified-since"] = lm // SHOULD
		}
	}
	return out
}

// EvaluatePreconditions 对应 RFC 9110 §13.2.2。
// shortCircuit 为 true 表示条件全部通过、应当正常执行方法（返回值无意义）。
func EvaluatePreconditions(req map[string]string, r *Resp, isOrigin bool) (int, bool) {
	method := req["__method__"]
	if method == "" {
		method = "GET"
	}
	if isOrigin {
		if v, ok := req["if-match"]; ok {
			if !etagListMatch(v, r.Hdr["etag"]) {
				return 412, false
			}
		} else if v, ok := req["if-unmodified-since"]; ok {
			if !unmodifiedSince(v, r) {
				return 412, false
			}
		}
	}
	if v, ok := req["if-none-match"]; ok {
		if etagListMatch(v, r.Hdr["etag"]) {
			if method == "GET" || method == "HEAD" {
				return 304, false
			}
			return 412, false
		}
	} else if (method == "GET" || method == "HEAD") && req["if-modified-since"] != "" {
		if unmodifiedSince(req["if-modified-since"], r) {
			return 304, false
		}
	}
	if method == "GET" && req["range"] != "" && req["if-range"] != "" {
		return 206, false
	}
	return 200, true
}

func etagListMatch(headerValue, etag string) bool {
	if strings.TrimSpace(headerValue) == "*" {
		return etag != ""
	}
	if etag == "" {
		return false
	}
	for _, t := range strings.Split(headerValue, ",") {
		if strings.TrimSpace(t) == etag {
			return true
		}
	}
	return false
}

func unmodifiedSince(headerValue string, r *Resp) bool {
	since, err := strconv.ParseFloat(headerValue, 64)
	if err != nil {
		return false
	}
	lm, err := strconv.ParseFloat(r.Hdr["last-modified"], 64)
	if err != nil {
		if d, err2 := strconv.ParseFloat(r.Hdr["date"], 64); err2 == nil {
			lm = d
		}
	}
	return lm <= since
}

func main() {
	now := float64(time.Date(2024, 1, 1, 0, 0, 0, 0, time.UTC).Unix())

	r := &Resp{Hdr: map[string]string{"cache-control": "s-maxage=60, max-age=600"}}
	fl, _ := r.FreshnessLifetime(true)
	fmt.Printf("shared freshness     = %.0f\n", fl)

	h := &Resp{Hdr: map[string]string{
		"date":          fmt.Sprint(now),
		"last-modified": fmt.Sprint(now - 86400),
	}}
	if hl, ok := h.HeuristicLifetime(0.1); ok {
		fmt.Printf("heuristic (10%%)      = %.0f\n", hl)
	}

	age := &Resp{Hdr: map[string]string{"date": fmt.Sprint(now), "age": "10"}}
	fmt.Printf("current_age          = %.0f\n",
		age.CalculateAge(now+100, now, now+5, true))

	fmt.Printf("vary gzip vs br      = %v\n",
		VaryMatches(map[string]string{"accept-encoding": "gzip"},
			map[string]string{"accept-encoding": "br"}, "Accept-Encoding"))
	fmt.Printf("vary *               = %v\n",
		VaryMatches(map[string]string{"a": "1"}, map[string]string{"a": "1"}, "*"))

	pr := &Resp{Hdr: map[string]string{"etag": `"v2"`}}
	code, short := EvaluatePreconditions(
		map[string]string{"if-match": `"v1"`, "if-none-match": `"v2"`}, pr, true)
	fmt.Printf("origin: if-match     = %d short=%v\n", code, !short)
	code, _ = EvaluatePreconditions(
		map[string]string{"if-match": `"v1"`, "if-none-match": `"v2"`}, pr, false)
	fmt.Printf("cache : if-none-match= %d\n", code)

	vr := &Resp{Hdr: map[string]string{"etag": `"v1"`, "last-modified": fmt.Sprint(now)}}
	keys := make([]string, 0)
	for k := range BuildValidationRequest(vr, false) {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	fmt.Printf("validation headers   = %v\n", keys)
}
