// Go 侧对照实现（一）：RFC 9111 的新鲜度生命周期、Age 计算与 stale 许可判定。
//
// 官方口径：
//   §4.2    response_is_fresh = (freshness_lifetime > current_age)   —— 严格大于
//   §4.2.1  取第一个匹配：共享且 s-maxage → s-maxage；max-age；Expires − Date；否则启发式
//   §4.2.2  有显式过期时禁止启发式；有 Last-Modified 时典型取 10%
//   §4.2.3  apparent_age = max(0, response_time − date_value)
//           corrected_age_value = age_value + (response_time − request_time)
//           保守的 corrected_initial_age = max(apparent_age, corrected_age_value)
//           current_age = corrected_initial_age + (now − response_time)
//   §4.2.4  显式禁止（no-cache / must-revalidate / proxy-revalidate / s-maxage）时不许出 stale
//
// Vary 匹配与前置条件优先级见同包的 precond.go。
package main

import (
	"strconv"
	"strings"
)

// Resp 是一条存储响应；时间统一用 epoch 秒，避免引入日期解析的噪音。
type Resp struct {
	Status int
	Hdr    map[string]string
	ReqHdr map[string]string // 产生这条响应的原始请求头（Vary 匹配用）
	Body   string
}

// ccDirectives 解析 Cache-Control，重复指令取第一次出现（§4.2.1 注）。
func ccDirectives(v string) map[string]string {
	out := map[string]string{}
	for _, part := range strings.Split(v, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if i := strings.Index(part, "="); i >= 0 {
			k := strings.ToLower(strings.TrimSpace(part[:i]))
			val := strings.Trim(strings.TrimSpace(part[i+1:]), `"`)
			if _, ok := out[k]; !ok {
				out[k] = val
			}
			continue
		}
		k := strings.ToLower(part)
		if _, ok := out[k]; !ok {
			out[k] = ""
		}
	}
	return out
}

// ccInt 取整数型指令；解析失败返回 ok=false，对应「invalid freshness information」。
func ccInt(d map[string]string, key string) (int, bool) {
	v, ok := d[key]
	if !ok {
		return 0, false
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0, false
	}
	return n, true
}

func (r *Resp) cc() map[string]string { return ccDirectives(r.Hdr["cache-control"]) }

func (r *Resp) dateValue() float64 {
	if d, err := strconv.ParseFloat(r.Hdr["date"], 64); err == nil {
		return d
	}
	return 0
}

// FreshnessLifetime 对应 §4.2.1；ok 为 false 表示「无显式过期」。
func (r *Resp) FreshnessLifetime(shared bool) (float64, bool) {
	d := r.cc()
	if shared {
		if n, ok := ccInt(d, "s-maxage"); ok {
			return float64(n), true
		}
	}
	if n, ok := ccInt(d, "max-age"); ok {
		return float64(n), true
	}
	if e, ok := r.Hdr["expires"]; ok {
		if ev, err := strconv.ParseFloat(e, 64); err == nil {
			return ev - r.dateValue(), true
		}
	}
	return 0, false
}

// HeuristicLifetime 对应 §4.2.2：有显式过期时禁止（ok=false）。
func (r *Resp) HeuristicLifetime(fraction float64) (float64, bool) {
	if _, ok := r.FreshnessLifetime(true); ok {
		return 0, false
	}
	if _, ok := r.FreshnessLifetime(false); ok {
		return 0, false
	}
	lm, err := strconv.ParseFloat(r.Hdr["last-modified"], 64)
	if err != nil {
		return 0, false
	}
	v := fraction * (r.dateValue() - lm)
	if v < 0 {
		v = 0
	}
	return v, true
}

// CalculateAge 对应 §4.2.3；conservative 决定是否取 max(apparent, corrected)。
func (r *Resp) CalculateAge(now, requestTime, responseTime float64, conservative bool) float64 {
	apparent := responseTime - r.dateValue()
	if apparent < 0 {
		apparent = 0
	}
	ageValue := 0.0
	if a, err := strconv.ParseFloat(r.Hdr["age"], 64); err == nil {
		ageValue = a
	}
	corrected := ageValue + (responseTime - requestTime)
	initial := corrected
	if conservative && apparent > initial {
		initial = apparent
	}
	return initial + (now - responseTime)
}

// IsFresh 对应 §4.2 的 response_is_fresh。
func (r *Resp) IsFresh(shared bool, now, requestTime, responseTime float64) bool {
	fl, ok := r.FreshnessLifetime(shared)
	if !ok {
		h, ok2 := r.HeuristicLifetime(0.1)
		if !ok2 {
			return false
		}
		fl = h
	}
	return fl > r.CalculateAge(now, requestTime, responseTime, true)
}

// MayServeStale 对应 §4.2.4。
func (r *Resp) MayServeStale(shared bool, reqCC string, disconnected bool) bool {
	d := r.cc()
	for _, forbidden := range []string{"no-cache", "must-revalidate"} {
		if _, ok := d[forbidden]; ok {
			return false
		}
	}
	if shared {
		if _, ok := d["proxy-revalidate"]; ok {
			return false
		}
		if _, ok := d["s-maxage"]; ok {
			return false
		}
	}
	if disconnected {
		return true
	}
	_, ok := ccInt(ccDirectives(reqCC), "max-stale")
	return ok
}
