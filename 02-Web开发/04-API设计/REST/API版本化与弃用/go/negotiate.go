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
	"strconv"
	"strings"
)

// -------------------------------------------------------- Accept 协商

// MediaRange 是一个解析后的 media-range。
type MediaRange struct {
	Type    string            // "" 表示 *（type/* 或 */*）
	Subtype string            // "" 表示 *
	Params  map[string]string
	Q       float64
}

// ParseAccept 解析 Accept 头（不处理 q 之外的扩展参数）。
func ParseAccept(header string) []MediaRange {
	out := []MediaRange{}
	for _, item := range strings.Split(header, ",") {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		segs := strings.Split(item, ";")
		raw := strings.ToLower(strings.TrimSpace(segs[0]))
		parts := strings.SplitN(raw, "/", 2)
		if len(parts) != 2 {
			continue
		}
		typ, sub := parts[0], parts[1]
		if typ == "*" {
			typ = ""
		}
		if sub == "*" {
			sub = ""
		}
		if typ == "" && sub != "" {
			continue
		}
		mr := MediaRange{Type: typ, Subtype: sub, Params: map[string]string{}, Q: 1.0}
		for _, p := range segs[1:] {
			p = strings.TrimSpace(p)
			if p == "" {
				continue
			}
			key, value := p, ""
			if i := strings.Index(p, "="); i >= 0 {
				key, value = p[:i], p[i+1:]
			}
			key = strings.ToLower(strings.TrimSpace(key))
			value = strings.Trim(strings.TrimSpace(value), `"`)
			if key == "q" {
				if q, err := strconv.ParseFloat(value, 64); err == nil {
					mr.Q = q
				} else {
					mr.Q = 0
				}
				continue
			}
			mr.Params[key] = value
		}
		out = append(out, mr)
	}
	return out
}

// Specificity 带参完整型 3 > 完整型 2 > type/* 1 > */* 0。
func Specificity(mr MediaRange) int {
	if mr.Type == "" {
		return 0
	}
	if mr.Subtype == "" {
		return 1
	}
	if len(mr.Params) > 0 {
		return 3
	}
	return 2
}

// SplitMediaType 把 `text/plain;format=flowed` 拆成类型、子类型与参数。
func SplitMediaType(mediaType string) (string, string, map[string]string) {
	head := strings.SplitN(strings.TrimSpace(mediaType), ";", 2)[0]
	params := map[string]string{}
	if i := strings.Index(mediaType, ";"); i >= 0 {
		for _, p := range strings.Split(mediaType[i+1:], ";") {
			if eq := strings.Index(p, "="); eq >= 0 {
				params[strings.ToLower(strings.TrimSpace(p[:eq]))] =
					strings.Trim(strings.TrimSpace(p[eq+1:]), `"`)
			}
		}
	}
	parts := strings.SplitN(head, "/", 2)
	if len(parts) != 2 {
		return head, "", params
	}
	return strings.ToLower(strings.TrimSpace(parts[0])),
		strings.ToLower(strings.TrimSpace(parts[1])), params
}

// Matches 判断 media-range 是否匹配某个媒体类型。
func (mr MediaRange) Matches(mediaType string) bool {
	typ, sub, params := SplitMediaType(mediaType)
	if mr.Type != "" && mr.Type != typ {
		return false
	}
	if mr.Subtype != "" && mr.Subtype != sub {
		return false
	}
	for k, v := range mr.Params {
		if got, ok := params[k]; !ok || got != v {
			return false
		}
	}
	return true
}

// Quality 取最具体的匹配 media-range 的 q（RFC 9110 §12.5.1）。
func Quality(ranges []MediaRange, mediaType string) float64 {
	best, bestSpec := 0.0, -1
	for _, mr := range ranges {
		if !mr.Matches(mediaType) {
			continue
		}
		if spec := Specificity(mr); spec > bestSpec {
			bestSpec, best = spec, mr.Q
		}
	}
	return best
}

// ---------------------------------------------------------------- Vary

// VaryIsWildcard 判断 Vary 是否含 `*`。
func VaryIsWildcard(value string) bool {
	for _, n := range strings.Split(value, ",") {
		if strings.TrimSpace(n) == "*" {
			return true
		}
	}
	return false
}

// CacheKeyMatches 判断缓存条目能否复用（§12.5.5 用途 1）。
func CacheKeyMatches(vary string, stored, incoming map[string]string) bool {
	if VaryIsWildcard(vary) {
		return false
	}
	for _, n := range strings.Split(vary, ",") {
		name := strings.ToLower(strings.TrimSpace(n))
		if name == "" {
			continue
		}
		if stored[name] != incoming[name] {
			return false
		}
	}
	return true
}
