// Package main 转写 PostgreSQL 18 文档 5.12 的分区边界语义与 partprune.c 的裁剪阶段。
package main

import "sort"

// Partition 是分区的公共接口: 判定一个值或一个区间是否可能落在本分区。
type Partition interface {
	Name() string
	MatchValue(v interface{}) bool
	MatchInterval(lo, hi int64) bool
}

// RangePartition RANGE: [lower, upper)。
type RangePartition struct {
	Label string
	Lower *int64
	Upper *int64
}

func (p RangePartition) Name() string { return p.Label }

func (p RangePartition) MatchValue(v interface{}) bool {
	n, ok := toInt(v)
	if !ok {
		return false
	}
	if p.Lower != nil && n < *p.Lower {
		return false
	}
	if p.Upper != nil && n >= *p.Upper {
		return false
	}
	return true
}

func (p RangePartition) MatchInterval(lo, hi int64) bool {
	a, b := int64(-1)<<62, int64(1)<<62
	if p.Lower != nil {
		a = *p.Lower
	}
	if p.Upper != nil {
		b = *p.Upper
	}
	return max64(lo, a) < min64(hi, b)
}

// ListPartition LIST: 显式键值集合; IsDefault 表示 DEFAULT 分区。
type ListPartition struct {
	Label     string
	Values    map[string]bool
	IsDefault bool
}

func (p ListPartition) Name() string { return p.Label }

func (p ListPartition) MatchValue(v interface{}) bool {
	s, ok := v.(string)
	if !ok {
		return false
	}
	return p.Values[s]
}

func (p ListPartition) MatchInterval(lo, hi int64) bool {
	for k := range p.Values {
		var n int64
		for i := 0; i < len(k); i++ {
			n = n*10 + int64(k[i]-'0')
		}
		if n >= lo && n < hi {
			return true
		}
	}
	return false
}

// HashPartition HASH: hash(key) % modulus == remainder。
type HashPartition struct {
	Label     string
	Modulus   int64
	Remainder int64
}

func (p HashPartition) Name() string { return p.Label }

func (p HashPartition) MatchValue(v interface{}) bool {
	h := hashAny(v)
	return h%p.Modulus == p.Remainder
}

// MatchInterval HASH 无法按区间裁剪: 连续区间必然横跨所有余数。
func (p HashPartition) MatchInterval(lo, hi int64) bool { return true }

func hashAny(v interface{}) int64 {
	s := fmtOf(v)
	var h int64 = 1469598103934665603
	for i := 0; i < len(s); i++ {
		h ^= int64(s[i])
		h *= 1099511628211
	}
	if h < 0 {
		h = -h
	}
	return h
}

func fmtOf(v interface{}) string {
	switch x := v.(type) {
	case string:
		return x
	case int:
		return itoa(x)
	case int64:
		return itoa(int(x))
	}
	return ""
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	buf := []byte{}
	for n > 0 {
		buf = append([]byte{byte('0' + n%10)}, buf...)
		n /= 10
	}
	if neg {
		return "-" + string(buf)
	}
	return string(buf)
}

func toInt(v interface{}) (int64, bool) {
	switch x := v.(type) {
	case int:
		return int64(x), true
	case int64:
		return x, true
	}
	return 0, false
}

func max64(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

func min64(a, b int64) int64 {
	if a < b {
		return a
	}
	return b
}

// Prune 按算子裁剪, 返回命中的分区名与匹配状态。
func Prune(parts []Partition, op string, value interface{}) ([]string, string) {
	hits := []string{}
	switch op {
	case "=":
		for _, p := range parts {
			if lp, ok := p.(ListPartition); ok && lp.IsDefault {
				hits = append(hits, p.Name()) // DEFAULT 排除不掉
				continue
			}
			if p.MatchValue(value) {
				hits = append(hits, p.Name())
			}
		}
	case "<":
		n, _ := toInt(value)
		for _, p := range parts {
			if p.MatchInterval(minSentinel, n) {
				hits = append(hits, p.Name())
			}
		}
	case ">=":
		n, _ := toInt(value)
		for _, p := range parts {
			if p.MatchInterval(n, maxSentinel) {
				hits = append(hits, p.Name())
			}
		}
	default:
		return nil, "UNSUPPORTED"
	}
	sort.Strings(hits)
	return hits, "MATCH_CLAUSE"
}

const (
	minSentinel = int64(-1) << 62
	maxSentinel = int64(1) << 62
)

// Intersect AND 组合。
func Intersect(sets [][]string) []string {
	if len(sets) == 0 {
		return nil
	}
	cur := map[string]bool{}
	for _, s := range sets[0] {
		cur[s] = true
	}
	for _, s := range sets[1:] {
		next := map[string]bool{}
		for _, v := range s {
			if cur[v] {
				next[v] = true
			}
		}
		cur = next
	}
	return keys(cur)
}

// Union OR 组合。
func Union(sets [][]string) []string {
	out := map[string]bool{}
	for _, s := range sets {
		for _, v := range s {
			out[v] = true
		}
	}
	return keys(out)
}

func keys(m map[string]bool) []string {
	out := []string{}
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
