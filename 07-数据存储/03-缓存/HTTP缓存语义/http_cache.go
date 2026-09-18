// HTTP 缓存判定核心（RFC 9111 §3/§4 + RFC 5861）的 Go 实现。
//
// 构建/运行： go run http_cache.go
// 与 http_cache_core.py 是同一套规范的两个实现，断言逐条对齐。
package main

import (
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
)

// ------------------------------------------------------------ 指令解析

// splitCommas 按逗号切分但保留引号内的逗号。
// `no-cache="Set-Cookie,ETag"` 是一个指令，朴素 Split(",") 会把它拆坏。
func splitCommas(s string) []string {
	var parts []string
	var cur strings.Builder
	inQ := false
	for _, r := range s {
		switch {
		case r == '"':
			inQ = !inQ
			cur.WriteRune(r)
		case r == ',' && !inQ:
			parts = append(parts, cur.String())
			cur.Reset()
		default:
			cur.WriteRune(r)
		}
	}
	return append(parts, cur.String())
}

func parseCacheControl(v string) map[string]string {
	out := map[string]string{}
	for _, part := range splitCommas(v) {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if i := strings.Index(part, "="); i >= 0 {
			name := strings.ToLower(strings.TrimSpace(part[:i]))
			arg := strings.TrimSpace(part[i+1:])
			if len(arg) >= 2 && strings.HasPrefix(arg, "\"") && strings.HasSuffix(arg, "\"") {
				arg = arg[1 : len(arg)-1]
			}
			out[name] = arg
		} else {
			out[strings.ToLower(part)] = ""
		}
	}
	return out
}

const maxDelta = 2147483648 // RFC 9111 §1.2.2：delta-seconds 上限 2^31

func deltaSeconds(cc map[string]string, key string) (int, bool) {
	raw, ok := cc[key]
	if !ok || raw == "" {
		return 0, false
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < 0 {
		return 0, false // 非法值 -> 视为陈旧，不产生寿命
	}
	if n > maxDelta {
		n = maxDelta
	}
	return n, true
}

// ------------------------------------------------------------ 新鲜度

// freshnessLifetime 按 §4.2.1 顺序取第一个匹配：s-maxage(共享) > max-age > Expires-Date > 启发式
func freshnessLifetime(headers map[string]string, shared bool,
	lastModified, now int, hasLM bool) (int, string) {
	cc := parseCacheControl(headers["cache-control"])
	if shared {
		if v, ok := deltaSeconds(cc, "s-maxage"); ok {
			return v, "s-maxage"
		}
	}
	if v, ok := deltaSeconds(cc, "max-age"); ok {
		return v, "max-age"
	}
	if _, ok := headers["expires"]; ok {
		expires, _ := strconv.Atoi(headers["expires"])
		date := 0
		if v, ok := headers["date"]; ok {
			date, _ = strconv.Atoi(v)
		} else {
			date = now // §4.2.1：无 Date 时用「收到响应的时刻」
		}
		return expires - date, "expires-date"
	}
	if hasLM && now-lastModified > 0 {
		return int(float64(now-lastModified) * 0.10), "heuristic" // §4.2.2 典型 10%
	}
	return 0, "none"
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// currentAge 实现 §4.2.3。conservative=true 时 corrected_initial_age = max(apparent, corrected)。
func currentAge(ageValue, dateValue, requestTime, responseTime, now int, conservative bool) int {
	apparentAge := maxInt(0, responseTime-dateValue) // 负值截断为 0
	responseDelay := responseTime - requestTime
	correctedAge := ageValue + responseDelay
	initial := correctedAge
	if conservative {
		initial = maxInt(apparentAge, correctedAge)
	}
	return initial + (now - responseTime)
}

// responseIsFresh：§4.2 的 `freshness_lifetime > current_age`，严格大于。
func responseIsFresh(lifetime, age int) bool { return lifetime > age }

// ------------------------------------------------------------ 陈旧复用

// reuseReason 返回能否不经校验复用陈旧响应。
func reuseReason(headers map[string]string, shared, disconnected bool) (bool, string) {
	cc := parseCacheControl(headers["cache-control"])
	if v, ok := cc["no-cache"]; ok && v == "" {
		return false, "no-cache(unqualified)"
	}
	if _, ok := cc["must-revalidate"]; ok {
		if disconnected {
			return false, "must-revalidate->504"
		}
		return false, "must-revalidate"
	}
	if shared {
		if _, ok := cc["proxy-revalidate"]; ok {
			return false, "proxy-revalidate"
		}
		if _, ok := deltaSeconds(cc, "s-maxage"); ok {
			return false, "s-maxage"
		}
	}
	return true, "allowed"
}

// ------------------------------------------------------------ RFC 5861

func staleWindow(lifetime, age, swr int) string {
	if age < lifetime {
		return "fresh"
	}
	if age < lifetime+swr {
		return "swr"
	}
	return "stale"
}

func mayServeStaleIfError(age, lifetime, sie int) bool { return age < lifetime+sie }

// ------------------------------------------------------------ Vary

func varyMatch(vary string, stored, fresh map[string]string) bool {
	if vary == "" {
		return true
	}
	for _, f := range strings.Split(vary, ",") {
		f = strings.TrimSpace(strings.ToLower(f))
		if f == "*" {
			return false // §4.1：含 "*" 永远不匹配
		}
		s, sOK := stored[f]
		n, nOK := fresh[f]
		if sOK != nOK { // 缺失只能匹配缺失
			return false
		}
		if s != n {
			return false
		}
	}
	return true
}

func secondaryCacheKey(uri, vary string, req map[string]string) string {
	if vary == "" {
		return uri
	}
	fields := strings.Split(vary, ",")
	for i := range fields {
		fields[i] = strings.TrimSpace(strings.ToLower(fields[i]))
	}
	sort.Strings(fields) // 排序保证字段名顺序无关
	var b strings.Builder
	b.WriteString(uri)
	for _, f := range fields {
		b.WriteString("|")
		b.WriteString(f)
		b.WriteString("=")
		b.WriteString(req[f])
	}
	return b.String()
}

// ------------------------------------------------------------ 自检

var fails []string

// check 固定三参数（与仓库既有 Go demo 一致，便于 _docs/tools/go_sanity.py 核查实参个数）
func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s
", label)
		return
	}
	fails = append(fails, label+" "+detail)
	fmt.Printf("  FAIL %s %s
", label, detail)
}

func main() {
	fmt.Println("[1] 解析")
	d := parseCacheControl("max-age=600, stale-while-revalidate=30")
	check("max-age", d["max-age"] == "600", fmt.Sprint(d))
	// 反引号原始串先赋给变量再传入：直接写在 check(...) 实参里会让
	// go_sanity.py 把串内的逗号当成实参分隔符（工具不解析反引号串）
	qualified := `no-cache="Set-Cookie,ETag"`
	check("引号内逗号不切", parseCacheControl(qualified)["no-cache"] == "Set-Cookie,ETag", "")
	check("指令名大小写不敏感", parseCacheControl("Max-Age=60")["max-age"] == "60", "")
	v, _ := deltaSeconds(map[string]string{"m": "99999999999"}, "m")
	check("delta 上限 2^31", v == maxDelta, fmt.Sprint(v))

	fmt.Println("[2] freshness 优先级")
	h := map[string]string{"cache-control": "s-maxage=600, max-age=60", "date": "1000"}
	fl, src := freshnessLifetime(h, true, 0, 0, false)
	check("共享取 s-maxage", fl == 600 && src == "s-maxage", fmt.Sprintf("%d %s", fl, src))
	fl, src = freshnessLifetime(h, false, 0, 0, false)
	check("私有取 max-age", fl == 60 && src == "max-age", fmt.Sprintf("%d %s", fl, src))
	fl, src = freshnessLifetime(map[string]string{"expires": "1300", "date": "1000"}, true, 0, 0, false)
	check("Expires-Date", fl == 300 && src == "expires-date", fmt.Sprintf("%d %s", fl, src))
	fl, src = freshnessLifetime(map[string]string{"date": "1000"}, true, 1000, 101000, true)
	check("启发式 10%", fl == 10000 && src == "heuristic", fmt.Sprintf("%d %s", fl, src))

	fmt.Println("[3] 严格大于")
	check("99<100 fresh", responseIsFresh(100, 99), "")
	check("100==100 stale", !responseIsFresh(100, 100), "")

	fmt.Println("[4] current_age")
	a1 := currentAge(0, 1000, 900, 905, 2000, true)
	check("时钟落后 apparent 归零", a1 == 1100, fmt.Sprint(a1))
	c := currentAge(100, 0, 1000, 1002, 1010, true)
	n := currentAge(100, 0, 1000, 1002, 1010, false)
	check("保守合成取 max(apparent,corrected)", c == 1010, fmt.Sprint(c))
	check("非保守仅用 corrected", n == 110, fmt.Sprint(n))
	check("二者差 900", c-n == 900, fmt.Sprint(c-n))

	fmt.Println("[5] 陈旧复用")
	ok, why := reuseReason(map[string]string{"cache-control": "no-cache"}, true, false)
	check("no-cache 无参必须校验", !ok && why == "no-cache(unqualified)", why)
	ok, _ = reuseReason(map[string]string{"cache-control": `no-cache="Set-Cookie"`}, true, false)
	check("no-cache 限定形式可复用", ok, "")
	ok, why = reuseReason(map[string]string{"cache-control": "must-revalidate"}, true, true)
	check("must-revalidate+断网 -> 504", !ok && why == "must-revalidate->504", why)
	ok, why = reuseReason(map[string]string{"cache-control": "s-maxage=600"}, true, false)
	check("s-maxage 阻止陈旧复用", !ok && why == "s-maxage", why)
	ok, _ = reuseReason(map[string]string{"cache-control": "proxy-revalidate"}, false, false)
	check("proxy-revalidate 对私有缓存无效", ok, "")

	fmt.Println("[6] RFC 5861")
	check("599 fresh", staleWindow(600, 599, 30) == "fresh", "")
	check("600 进入 swr", staleWindow(600, 600, 30) == "swr", "")
	check("630 真陈旧", staleWindow(600, 630, 30) == "stale", "")
	check("sie 窗口内", mayServeStaleIfError(650, 600, 60), "")
	check("sie 窗口外", !mayServeStaleIfError(670, 600, 60), "")

	fmt.Println("[7] Vary")
	hdr := map[string]string{"accept-encoding": "gzip"}
	check("Vary:* 永不匹配", !varyMatch("*", hdr, hdr), "")
	check("Vary 值相同匹配", varyMatch("Accept-Encoding", hdr, hdr), "")
	check("Vary 值不同不匹配", !varyMatch("Accept-Encoding", hdr, map[string]string{"accept-encoding": "br"}), "")
	check("Vary 缺失只能匹配缺失", !varyMatch("Accept-Encoding", hdr, map[string]string{}), "")
	k1 := secondaryCacheKey("/p", "Accept-Encoding, User-Agent",
		map[string]string{"accept-encoding": "gzip", "user-agent": "curl"})
	k2 := secondaryCacheKey("/p", "User-Agent, Accept-Encoding",
		map[string]string{"user-agent": "curl", "accept-encoding": "gzip"})
	check("二级键对字段顺序稳定", k1 == k2, k1+" vs "+k2)

	fmt.Println()
	if len(fails) > 0 {
		fmt.Printf("FAILED %d
", len(fails))
		os.Exit(1)
	}
	fmt.Println("ALL PASS")
}
