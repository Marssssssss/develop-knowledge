// Package ratelimitheaders 解析 draft-ietf-httpapi-ratelimit-headers-11 定义的
// RateLimit-Policy 与 RateLimit 响应头部，并给出客户端等待决策。
package ratelimitheaders

import (
	"encoding/base64"
	"errors"
	"strconv"
	"strings"
)

// ErrMalformed 表示字段值不合规范；按 §7 客户端 MUST 忽略该字段。
var ErrMalformed = errors.New("malformed RateLimit field")

// QuotaUnits 规范定义的三种配额单位（§3.1.2）。
var QuotaUnits = map[string]bool{
	"requests":            true,
	"content-bytes":       true,
	"concurrent-requests": true,
}

// PolicyItem 对应 RateLimit-Policy 的一个 Item（§3.1）。
type PolicyItem struct {
	Name string
	Q    int
	Qu   string
	W    int
	HasW bool
	Pk   []byte
}

// LimitItem 对应 RateLimit 的一个 Item（§4.1）。
type LimitItem struct {
	Name string
	R    int
	T    int
	HasT bool
	Pk   []byte
}

// splitItems 按顶层逗号切分 SF list。
func splitItems(value string) []string {
	var items []string
	var buf []rune
	inStr, inB64 := false, false
	for _, ch := range value {
		switch {
		case ch == '"':
			inStr = !inStr
			buf = append(buf, ch)
		case ch == ':':
			inB64 = !inB64
			buf = append(buf, ch)
		case ch == ',' && !inStr && !inB64:
			items = append(items, strings.TrimSpace(string(buf)))
			buf = nil
		default:
			buf = append(buf, ch)
		}
	}
	if tail := strings.TrimSpace(string(buf)); tail != "" {
		items = append(items, tail)
	}
	return items
}

func parseItem(raw string) (string, map[string]string, error) {
	parts := strings.Split(raw, ";")
	tok := strings.TrimSpace(parts[0])
	if len(tok) < 2 {
		return "", nil, ErrMalformed
	}
	quoted := tok[0] == '"' && tok[len(tok)-1] == '"'
	byteseq := tok[0] == ':' && tok[len(tok)-1] == ':'
	if !quoted && !byteseq {
		return "", nil, ErrMalformed
	}
	params := map[string]string{}
	for _, p := range parts[1:] {
		kv := strings.SplitN(p, "=", 2)
		if len(kv) != 2 {
			return "", nil, ErrMalformed
		}
		params[strings.TrimSpace(kv[0])] = strings.TrimSpace(kv[1])
	}
	return tok[1 : len(tok)-1], params, nil
}

func asInt(params map[string]string, key string, required, allowZero bool) (int, bool, error) {
	raw, present := params[key]
	if !present {
		if required {
			return 0, false, ErrMalformed
		}
		return 0, false, nil
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return 0, false, ErrMalformed
	}
	if v < 0 || (v == 0 && !allowZero) {
		return 0, false, ErrMalformed
	}
	return v, true, nil
}

func asBytes(params map[string]string, key string) ([]byte, error) {
	raw, present := params[key]
	if !present {
		return nil, nil
	}
	if len(raw) < 2 || raw[0] != ':' || raw[len(raw)-1] != ':' {
		return nil, ErrMalformed
	}
	body := raw[1 : len(raw)-1]
	return base64.StdEncoding.DecodeString(body + strings.Repeat("=", -len(body)%4))
}

// ParsePolicy 解析 RateLimit-Policy（§3）。q 必填；w 非负且非零。
func ParsePolicy(value string) ([]PolicyItem, error) {
	raws := splitItems(value)
	if len(raws) == 0 {
		return nil, ErrMalformed
	}
	out := make([]PolicyItem, 0, len(raws))
	for _, raw := range raws {
		name, params, err := parseItem(raw)
		if err != nil {
			return nil, err
		}
		q, _, err := asInt(params, "q", true, true)
		if err != nil {
			return nil, err
		}
		qu := "requests"
		if v, ok := params["qu"]; ok {
			if len(v) < 2 || v[0] != '"' || v[len(v)-1] != '"' {
				return nil, ErrMalformed
			}
			qu = v[1 : len(v)-1]
			if !QuotaUnits[qu] {
				return nil, ErrMalformed
			}
		}
		w, hasW, err := asInt(params, "w", false, false)
		if err != nil {
			return nil, err
		}
		pk, err := asBytes(params, "pk")
		if err != nil {
			return nil, err
		}
		out = append(out, PolicyItem{Name: name, Q: q, Qu: qu, W: w, HasW: hasW, Pk: pk})
	}
	return out, nil
}

// ParseLimit 解析 RateLimit（§4）。r 必填；t 为非负整数（0 合法）。
func ParseLimit(value string) ([]LimitItem, error) {
	raws := splitItems(value)
	if len(raws) == 0 {
		return nil, ErrMalformed
	}
	out := make([]LimitItem, 0, len(raws))
	for _, raw := range raws {
		name, params, err := parseItem(raw)
		if err != nil {
			return nil, err
		}
		r, _, err := asInt(params, "r", true, true)
		if err != nil {
			return nil, err
		}
		t, hasT, err := asInt(params, "t", false, true)
		if err != nil {
			return nil, err
		}
		pk, err := asBytes(params, "pk")
		if err != nil {
			return nil, err
		}
		out = append(out, LimitItem{Name: name, R: r, T: t, HasT: hasT, Pk: pk})
	}
	return out, nil
}

// Rate 返回该 Item 的每秒配额；缺窗口时不可推算（返回 ok=false）。
func (l LimitItem) Rate() (float64, bool) {
	if !l.HasT || l.T == 0 {
		return 0, false
	}
	return float64(l.R) / float64(l.T), true
}

// SafeWait 客户端等待决策：Retry-After 优先，否则取所有 Item 中最保守的一个。
// 畸形字段按 §7 忽略，返回 0。
func SafeWait(rateLimit string, retryAfter float64, hasRetryAfter bool) float64 {
	if hasRetryAfter {
		return retryAfter
	}
	items, err := ParseLimit(rateLimit)
	if err != nil {
		return 0
	}
	best := 0.0
	for _, it := range items {
		r, ok := it.Rate()
		if !ok || r <= 0 {
			continue
		}
		if w := 1.0 / r; w > best {
			best = w
		}
	}
	return best
}
