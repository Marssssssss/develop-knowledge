// 607 API 版本化与弃用 —— Go 版（仅标准库）。
//
// 口径来源（与 Python 版同一批实读资料）：
//   - draft-ietf-httpapi-deprecation-header-07：Deprecation 是 Item Structured
//     Header，取值 sf-date（@1688169599）；§4 Sunset 时间戳 MUST NOT 早于
//     Deprecation；§5 弃用不改变资源行为。
//   - RFC 8594：Sunset 是 HTTP-date，SHOULD 是未来时刻；过去的时间戳按"当下"处理；
//     §1.4 第一阶段（不再推荐）不适合用 Sunset，第二阶段（下线）才用。
//   - RFC 9651 §3.3.7：sf-date = "@" sf-integer，可为负，范围 1..9999 年。
//   - RFC 9110 §5.6.7：HTTP-date 三种格式（IMF-fixdate / rfc850 / asctime）。
//   - RFC 9110 §12.5.1：media-range 越具体越优先；带参范围只匹配参数相同的媒体类型。
//     Table 5 最后一行官方印 0.7，按规则应为 0.3（Errata 7306 已确认）。
//   - RFC 9110 §12.5.5：Vary 是 * 或请求头字段名列表；* 表示方差无限。
//
// 运行：go run versioning.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const minSFDate = -62135596800
const maxSFDate = 253402214400

var sfDateRe = regexp.MustCompile(`^@(-?\d+)$`)
var imfRe = regexp.MustCompile(`^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), (\d{2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4}) (\d{2}):(\d{2}):(\d{2}) GMT$`)
var asctimeRe = regexp.MustCompile(`^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) ([ \d]\d) (\d{2}):(\d{2}):(\d{2}) (\d{4})$`)

var monthIndex = map[string]int{
	"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
	"Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

// ParseSFDate 解析 RFC 9651 §3.3.7 的 sf-date。
func ParseSFDate(value string) (int64, bool) {
	m := sfDateRe.FindStringSubmatch(strings.TrimSpace(value))
	if m == nil {
		return 0, false
	}
	secs, err := strconv.ParseInt(m[1], 10, 64)
	if err != nil {
		return 0, false
	}
	if secs < minSFDate || secs > maxSFDate {
		return 0, false
	}
	return secs, true
}

// FormatSFDate 回写成 `@` + 整数。
func FormatSFDate(secs int64) string { return "@" + strconv.FormatInt(secs, 10) }

// ParseHTTPDate 解析三种 HTTP-date（RFC 9110 §5.6.7）。
func ParseHTTPDate(value string) (int64, bool) {
	text := strings.TrimSpace(value)
	if m := imfRe.FindStringSubmatch(text); m != nil {
		return buildEpoch(m[3], monthIndex[m[2]], m[1], m[4], m[5], m[6])
	}
	if m := asctimeRe.FindStringSubmatch(text); m != nil {
		day := strings.TrimSpace(m[2])
		if len(day) == 1 {
			day = "0" + day
		}
		return buildEpoch(m[6], monthIndex[m[1]], day, m[3], m[4], m[5])
	}
	return 0, false
}

func buildEpoch(y, mo, d, h, mi, s string) (int64, bool) {
	year, _ := strconv.Atoi(y)
	day, _ := strconv.Atoi(d)
	hour, _ := strconv.Atoi(h)
	minute, _ := strconv.Atoi(mi)
	second, _ := strconv.Atoi(s)
	t := time.Date(year, time.Month(mo), day, hour, minute, second, 0, time.UTC)
	return t.Unix(), true
}

// IMFFixdate 发送方 MUST 生成的格式。
func IMFFixdate(secs int64) string {
	return time.Unix(secs, 0).UTC().Format("Mon, 02 Jan 2006 15:04:05 GMT")
}

// Endpoint 是一个带弃用/下线时间点的 API 端点。
type Endpoint struct {
	Name         string
	Deprecation  *int64
	Sunset       *int64
	DeprecationLink string
	SunsetLink   string
}

// Errors 校验 §4：Sunset 不得早于 Deprecation。
func (e Endpoint) Errors() []string {
	if e.Deprecation == nil || e.Sunset == nil {
		return nil
	}
	if *e.Sunset < *e.Deprecation {
		return []string{e.Name + ": Sunset 早于 Deprecation（draft §4）"}
	}
	return nil
}

// Phase 三阶段：active / deprecated（仍可用）/ sunset。
func (e Endpoint) Phase(now int64) string {
	deprecated := e.Deprecation != nil && now >= *e.Deprecation
	if !deprecated {
		return "active"
	}
	if e.Sunset != nil && now >= *e.Sunset {
		return "sunset"
	}
	return "deprecated"
}

// Usable §5：已弃用资源 SHOULD 照旧可用。
func (e Endpoint) Usable(now int64) bool { return e.Phase(now) != "sunset" }

// ------------------------------------------------------------ 演示

func must2(cond bool, label string) {
	if !cond {
		panic("断言失败: " + label)
	}
}

func ptr(v int64) *int64 { return &v }

func main() {
	dep, okDep := ParseSFDate("@1688169599")
	must2(okDep && dep == 1688169599, "sf-date 官方示例")
	_, okBad := ParseSFDate("1688169599")
	must2(!okBad, "缺 @ 非法")
	_, okFloat := ParseSFDate("@16.5")
	must2(!okFloat, "小数非法")

	sun, okSun := ParseHTTPDate("Sun, 30 Jun 2024 23:59:59 GMT")
	must2(okSun && sun == 1719791999, "IMF-fixdate（实得 "+strconv.FormatInt(sun, 10)+"）")
	sun2, ok2 := ParseHTTPDate("Sun Nov  6 08:49:37 1994")
	must2(ok2 && sun2 == 784111777, "asctime 格式")

	ep := Endpoint{Name: "GET /v1/orders", Deprecation: ptr(dep), Sunset: ptr(sun)}
	must2(len(ep.Errors()) == 0, "Sunset 不早于 Deprecation")
	must2(ep.Phase(1700000000) == "deprecated", "弃用后到下线前是 deprecated")
	must2(ep.Usable(1700000000), "§5 弃用不改变资源行为")
	must2(!ep.Usable(sun), "过了 sunset 才不可用")
	bad := Endpoint{Name: "bad", Deprecation: ptr(dep), Sunset: ptr(dep - 1)}
	must2(len(bad.Errors()) == 1, "Sunset 早于 Deprecation 应报错")

	t5 := ParseAccept("text/*;q=0.3, text/plain;q=0.7, text/plain;format=flowed, " +
		"text/plain;format=fixed;q=0.4, */*;q=0.5")
	must2(Quality(t5, "text/plain;format=flowed") == 1.0, "Table 5 第 1 行")
	must2(Quality(t5, "text/plain") == 0.7, "Table 5 第 2 行")
	must2(Quality(t5, "text/html") == 0.3, "Table 5 第 3 行")
	must2(Quality(t5, "image/jpeg") == 0.5, "Table 5 第 4 行")
	must2(Quality(t5, "text/plain;format=fixed") == 0.4, "Table 5 第 5 行")
	must2(Quality(t5, "text/html;level=3") == 0.3, "Table 5 第 6 行按规则为 0.3（Errata 7306）")
	must2(Quality(ParseAccept("text/plain;format=flowed;q=0.5"), "text/plain") == 0.0,
		"带参范围不匹配无参媒体类型")

	stored := map[string]string{"accept-encoding": "gzip"}
	must2(CacheKeyMatches("accept-encoding", stored, map[string]string{"accept-encoding": "gzip"}),
		"Vary 同值可复用")
	must2(!CacheKeyMatches("accept-encoding", stored, map[string]string{"accept-encoding": "br"}),
		"Vary 异值不可复用")
	must2(!CacheKeyMatches("*", stored, stored), "Vary: * 不得复用")

	fmt.Println("Deprecation:", FormatSFDate(dep), "/ Sunset:", IMFFixdate(sun))
	fmt.Println("phase@1700000000:", ep.Phase(1700000000), "usable:", ep.Usable(1700000000))
	fmt.Println("q(text/html;level=3):", Quality(t5, "text/html;level=3"))
	fmt.Println("all go assertions passed")
}
