// Package ratelimitsvc 转写 envoyproxy/ratelimit 的描述符匹配与配额判定。
//
// 依据 envoyproxy/ratelimit@main：
//   - src/config/config_impl.go：GetLimit 的匹配顺序、wildcardMatch、unlimited 校验
//   - src/limiter/base_limiter.go：GetResponseDescriptorStatus 的阈值判据与 shadow_mode
//   - src/limiter/cache_key.go：GenerateCacheKey
//   - src/utils/time.go：MonthStartUnix / MonthExpirationSeconds
package ratelimitsvc

import (
	"errors"
	"math"
	"strconv"
	"strings"
	"time"
)

// ErrConfig 对应 Go 侧的 panic(newRateLimitConfigError(...))。
var ErrConfig = errors.New("rate limit config error")

// Units 合法的时间单位（UNKNOWN 不算）。
var Units = map[string]bool{
	"second": true, "minute": true, "hour": true, "day": true, "month": true, "year": true,
}

// RateLimit 一条限额。
type RateLimit struct {
	RequestsPerUnit uint32
	Unit            string
	Unlimited       bool
	ShadowMode      bool
}

// FromYaml 校验 unlimited 与 unit 的互斥关系。
func FromYaml(spec map[string]interface{}) (*RateLimit, error) {
	unlimited, _ := spec["unlimited"].(bool)
	unit, _ := spec["unit"].(string)
	valid := Units[unit]
	if unlimited && valid {
		return nil, errors.New("should not specify rate limit unit when unlimited")
	}
	if !unlimited && !valid {
		return nil, errors.New("invalid rate limit unit '" + unit + "'")
	}
	rpu, _ := spec["requests_per_unit"].(uint32)
	return &RateLimit{RequestsPerUnit: rpu, Unit: unit, Unlimited: unlimited}, nil
}

// DescriptorNode 配置树的一个节点。
type DescriptorNode struct {
	Limit       *RateLimit
	Descriptors map[string]*DescriptorNode
	Wildcards   [][2][]string // (config key, pattern parts)
}

// NewNode 构造空节点。
func NewNode() *DescriptorNode {
	return &DescriptorNode{Descriptors: map[string]*DescriptorNode{}}
}

// WildcardMatch config_impl.go:wildcardMatch 的逐行转写。
func WildcardMatch(parts []string, value string) bool {
	if len(parts) == 1 {
		return parts[0] == value
	}
	if !strings.HasPrefix(value, parts[0]) {
		return false
	}
	if !strings.HasSuffix(value, parts[len(parts)-1]) {
		return false
	}
	totalFixed := 0
	for _, p := range parts {
		totalFixed += len(p)
	}
	if len(value) < totalFixed {
		return false
	}
	remaining := value[len(parts[0]):]
	if last := parts[len(parts)-1]; last != "" {
		remaining = remaining[:len(remaining)-len(last)]
	}
	for _, part := range parts[1 : len(parts)-1] {
		idx := strings.Index(remaining, part)
		if idx < 0 {
			return false
		}
		remaining = remaining[idx+len(part):]
	}
	return true
}

// Config 一个 domain 的配置树。
type Config struct {
	Domains map[string]*DescriptorNode
}

// NewConfig 构造。
func NewConfig() *Config { return &Config{Domains: map[string]*DescriptorNode{}} }

// GetLimit 返回 (limit, matchedKey)。limit 为 nil 表示未匹配到。
func (c *Config) GetLimit(domain string, entries [][2]string, override *RateLimit) (*RateLimit, string) {
	root, ok := c.Domains[domain]
	if !ok {
		return nil, ""
	}
	// Envoy 侧自带 override：完全绕过配置树，且不启用 shadow_mode。
	if override != nil {
		return override, "override"
	}
	cur := root.Descriptors
	prev := root
	var found *RateLimit
	finalKey := ""
	for i, kv := range entries {
		finalKey = kv[0] + "_" + kv[1]
		next := cur[finalKey]
		if next == nil {
			for _, wc := range prev.Wildcards {
				if WildcardMatch(wc[1], finalKey) {
					next = cur[wc[0]]
					break
				}
			}
		}
		if next == nil {
			finalKey = kv[0] // 回落到「只按 key」的默认条目
			next = cur[finalKey]
		}
		if next == nil {
			break
		}
		// 只有深度完全匹配（最后一个 entry）才采用该层的 limit。
		if next.Limit != nil && i == len(entries)-1 {
			found = next.Limit
		}
		if len(next.Descriptors) > 0 {
			cur, prev = next.Descriptors, next
		} else {
			break
		}
	}
	return found, finalKey
}

// LimitInfo 一次判定的输入。
type LimitInfo struct {
	Limit          *RateLimit
	Before         uint64
	After          uint64
	Addend         uint64
	NearLimitRatio float64
}

// NewLimitInfo 由限额、自增前计数与 hits_addend 构造。
func NewLimitInfo(limit *RateLimit, before, addend uint64, ratio float64) *LimitInfo {
	return &LimitInfo{Limit: limit, Before: before, After: before + addend,
		Addend: addend, NearLimitRatio: ratio}
}

// OverLimitThreshold 即 requests_per_unit。
func (l *LimitInfo) OverLimitThreshold() uint64 { return uint64(l.Limit.RequestsPerUnit) }

// NearLimitThreshold floor(threshold * ratio)。
func (l *LimitInfo) NearLimitThreshold() uint64 {
	return uint64(math.Floor(float64(l.OverLimitThreshold()) * l.NearLimitRatio))
}

// Status 返回 (code, limitRemaining, stats)。
func Status(cacheKey string, info *LimitInfo, overWithLocalCache bool) (string, uint32, []string) {
	var stats []string
	if cacheKey == "" {
		return "OK", 0, stats
	}
	if overWithLocalCache {
		return "OVER_LIMIT", 0, []string{
			"OverLimit", "OverLimitWithLocalCache"}
	}
	threshold, near := info.OverLimitThreshold(), info.NearLimitThreshold()
	if info.After > threshold { // 严格大于
		if info.Before >= threshold {
			stats = append(stats, "OverLimit(all)")
		} else {
			stats = append(stats, "OverLimit(partial)")
			stats = append(stats, "NearLimit(partial)")
		}
		if info.Limit.ShadowMode {
			if info.Before >= threshold {
				stats = append(stats, "ShadowMode(all)")
			} else {
				stats = append(stats, "ShadowMode(partial)")
			}
			return "OK", 0, stats
		}
		return "OVER_LIMIT", 0, stats
	}
	if info.After > near {
		if info.Before >= near {
			stats = append(stats, "NearLimit(all)")
		} else {
			stats = append(stats, "NearLimit(partial)")
		}
	}
	return "OK", 0, append(stats, "WithinLimit")
}

// UnitDivider 秒/分/时/日四种单位的窗口除数。
var UnitDivider = map[string]int64{
	"second": 1, "minute": 60, "hour": 3600, "day": 86400,
}

// MonthStartUnix utils.MonthStartUnix：UTC 下当月 1 日 00:00:00。
func MonthStartUnix(nowUnix int64) int64 {
	t := time.Unix(nowUnix, 0).UTC()
	return time.Date(t.Year(), t.Month(), 1, 0, 0, 0, 0, time.UTC).Unix()
}

// MonthExpirationSeconds utils.MonthExpirationSeconds：距当月（UTC）结束的秒数。
func MonthExpirationSeconds(nowUnix int64) int64 {
	now := time.Unix(nowUnix, 0).UTC()
	next := time.Date(now.Year(), now.Month()+1, 1, 0, 0, 0, 0, time.UTC)
	return int64(next.Sub(now).Seconds())
}

// GenerateCacheKey CacheKeyGenerator.GenerateCacheKey 的转写。
// limit 为 nil 时返回空键 —— 空键在 Status 里被短路成「永远 OK」。
func GenerateCacheKey(prefix, domain string, entries [][2]string, limit *RateLimit,
	nowUnix int64, useCalendarMonth bool, shareThreshold []string) (string, bool) {
	if limit == nil {
		return "", false
	}
	var b strings.Builder
	b.WriteString(prefix)
	b.WriteString(domain)
	b.WriteByte('_')
	for i, kv := range entries {
		value := kv[1]
		if i < len(shareThreshold) && shareThreshold[i] != "" {
			value = shareThreshold[i]
		}
		b.WriteString(kv[0])
		b.WriteByte('_')
		b.WriteString(value)
		b.WriteByte('_')
	}
	var bucketStart int64
	if useCalendarMonth && limit.Unit == "month" {
		bucketStart = MonthStartUnix(nowUnix)
	} else {
		bucketStart = (nowUnix / UnitDivider[limit.Unit]) * UnitDivider[limit.Unit]
	}
	return b.String() + strconv.FormatInt(bucketStart, 10), limit.Unit == "second"
}
