// Package tracecontext 实现 W3C Trace Context Level 1 的 traceparent / tracestate 处理。
//
// 口径与 python/ 版本逐条对齐，均来自 https://www.w3.org/TR/trace-context/：
//   - version 为 2 位小写十六进制，"ff" 非法；
//   - trace-id 32 位、parent-id 16 位、trace-flags 2 位，全零非法，大写非法；
//   - sampled 是 trace-flags 的 bit 0，必须掩码取值；
//   - 高版本按位置解析（dash 在下标 2/35/52），短于 55 字符重开 trace；
//   - tracestate 最多 32 个成员，变更的 key 移到最左，截断先删 >128 字符的条目再从尾部删。
//
// Go 侧额外演示一件 Python 版没有的事：**把 flags 当整数比较**在 Go 里同样会错，
// 而且因为 flags 是 uint8，误写成 flags == 1 不会有任何编译错误——只能靠断言约束。
package tracecontext

import "strings"

const (
	// MaxMembers 是 tracestate 允许的 list-member 上限。
	MaxMembers = 32
	// BigEntryLen 是"超长条目"的阈值：截断时优先删除。
	BigEntryLen = 128
	// PropagateChars 是规范建议至少传播的组合头部字符数。
	PropagateChars = 512
)

// Reason 描述解析失败的处置方式。
type Reason string

const (
	// ReasonNone 表示解析成功。
	ReasonNone Reason = ""
	// ReasonIgnore 表示丢弃 traceparent，且不得再解析 tracestate。
	ReasonIgnore Reason = "ignore"
	// ReasonRestart 表示重开一条 trace，并清掉 tracestate。
	ReasonRestart Reason = "restart"
)

// TraceParent 是解析结果。
type TraceParent struct {
	OK         bool
	Reason     Reason
	Version    string
	TraceID    string
	ParentID   string
	TraceFlags string
	Sampled    bool
	Downgraded bool
}

func isHexLC(s string) bool {
	if len(s) == 0 {
		return false
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f')) {
			return false
		}
	}
	return true
}

func allZero(s string) bool {
	return strings.Count(s, "0") == len(s) && len(s) > 0
}

// ParseTraceParent 解析 traceparent 头值。
func ParseTraceParent(value string) TraceParent {
	out := TraceParent{}
	v := strings.TrimSpace(value)
	if len(v) < 3 || v[2] != '-' {
		out.Reason = ReasonRestart
		return out
	}
	version := v[0:2]
	if !isHexLC(version) {
		out.Reason = ReasonRestart
		return out
	}
	if version == "ff" {
		out.Reason = ReasonIgnore
		return out
	}
	out.Version = version

	var traceID, parentID, flags string
	if version == "00" {
		parts := strings.Split(v, "-")
		if len(parts) != 4 {
			out.Reason = ReasonIgnore
			return out
		}
		traceID, parentID, flags = parts[1], parts[2], parts[3]
	} else {
		if len(v) < 55 {
			out.Reason = ReasonRestart
			return out
		}
		if v[35] != '-' || v[52] != '-' {
			out.Reason = ReasonRestart
			return out
		}
		traceID, parentID, flags = v[3:35], v[36:52], v[53:55]
		if len(v) > 55 && v[55] != '-' {
			out.Reason = ReasonRestart
			return out
		}
		out.Downgraded = true
	}
	// v00 的字段非法一律是"忽略整个头"；高版本解析失败才是"重开 trace"
	fail := ReasonRestart
	if version == "00" {
		fail = ReasonIgnore
	}
	if len(traceID) != 32 || !isHexLC(traceID) || allZero(traceID) {
		out.Reason = fail
		return out
	}
	if len(parentID) != 16 || !isHexLC(parentID) || allZero(parentID) {
		out.Reason = fail
		return out
	}
	if len(flags) != 2 || !isHexLC(flags) {
		out.Reason = fail
		return out
	}
	out.OK = true
	out.TraceID, out.ParentID, out.TraceFlags = traceID, parentID, flags
	out.Sampled = ParseFlags(flags)&0x01 == 0x01
	return out
}

// ParseFlags 把 2 位十六进制转成 uint8。
func ParseFlags(flags string) uint8 {
	var v uint8
	for i := 0; i < len(flags); i++ {
		c := flags[i]
		var d uint8
		switch {
		case c >= '0' && c <= '9':
			d = c - '0'
		case c >= 'a' && c <= 'f':
			d = c - 'a' + 10
		}
		v = v*16 + d
	}
	return v
}

// Sampled 判断 bit 0。传整数进来是刻意的：提醒调用方别写 flags == 1。
func Sampled(flags uint8) bool { return flags&0x01 == 0x01 }

// Format 拼回 traceparent。
func Format(version, traceID, parentID string, flags uint8) string {
	const hexd = "0123456789abcdef"
	return version + "-" + traceID + "-" + parentID + "-" +
		string([]byte{hexd[flags>>4], hexd[flags&0x0F]})
}

// Member 是 tracestate 的一个 key/value。
type Member struct {
	Key   string
	Value string
}

func entryLen(m Member) int { return len(m.Key) + 1 + len(m.Value) }

// Update 写入一个 key（新增或覆写）并移到最左，其余成员保持相对顺序。
func Update(members []Member, key, value string) []Member {
	rest := make([]Member, 0, len(members)+1)
	for _, m := range members {
		if m.Key != key {
			rest = append(rest, m)
		}
	}
	return append([]Member{{Key: key, Value: value}}, rest...)
}

// Truncate 先删超长条目，再从尾部整条删，直到压进 budget 个字符。
func Truncate(members []Member, budget int) []Member {
	size := func(ms []Member) int {
		n := 0
		for _, m := range ms {
			n += entryLen(m)
		}
		return n + len(ms) - 1
	}
	kept := make([]Member, 0, len(members))
	for _, m := range members {
		if entryLen(m) <= BigEntryLen {
			kept = append(kept, m)
		}
	}
	for len(kept) > 0 && size(kept) > budget {
		kept = kept[:len(kept)-1]
	}
	return kept
}

// FormatTracestate 拼回 tracestate。
func FormatTracestate(members []Member) string {
	parts := make([]string, 0, len(members))
	for _, m := range members {
		parts = append(parts, m.Key+"="+m.Value)
	}
	return strings.Join(parts, ",")
}

// ParseTracestate 解析 tracestate，返回至多 MaxMembers 个成员，重复 key 保留最左。
func ParseTracestate(header string) []Member {
	members := make([]Member, 0, MaxMembers)
	if header == "" {
		return members
	}
	for _, raw := range strings.Split(header, ",") {
		m := strings.TrimSpace(raw)
		if m == "" {
			continue
		}
		i := strings.Index(m, "=")
		if i < 0 {
			continue
		}
		k, v := strings.TrimSpace(m[:i]), strings.TrimSpace(m[i+1:])
		if !ValidKey(k) || !ValidValue(v) {
			continue
		}
		dup := false
		for _, e := range members {
			if e.Key == k {
				dup = true
				break
			}
		}
		if dup {
			continue
		}
		members = append(members, Member{Key: k, Value: v})
		if len(members) == MaxMembers {
			break
		}
	}
	return members
}

func allowedKeyChar(c byte) bool {
	return (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
		c == '_' || c == '-' || c == '*' || c == '/'
}

// ValidKey 校验 simple-key 与 multi-tenant-key。
func ValidKey(key string) bool {
	if len(key) == 0 || len(key) > 256 {
		return false
	}
	if !((key[0] >= 'a' && key[0] <= 'z') || (key[0] >= '0' && key[0] <= '9')) {
		return false
	}
	if i := strings.Index(key, "@"); i >= 0 {
		tenant, system := key[:i], key[i+1:]
		if strings.Contains(system, "@") {
			return false
		}
		if len(tenant) < 1 || len(tenant) > 241 || len(system) < 1 || len(system) > 14 {
			return false
		}
		for j := 0; j < len(tenant); j++ {
			if !allowedKeyChar(tenant[j]) {
				return false
			}
		}
		for j := 0; j < len(system); j++ {
			if !allowedKeyChar(system[j]) {
				return false
			}
		}
		return true
	}
	for j := 0; j < len(key); j++ {
		if !allowedKeyChar(key[j]) {
			return false
		}
	}
	return true
}

// ValidValue 校验：0x20–0x7E 且不含逗号与等号，长度 ≤ 256。
func ValidValue(value string) bool {
	if len(value) > 256 {
		return false
	}
	for i := 0; i < len(value); i++ {
		c := value[i]
		if c < 0x20 || c > 0x7E || c == ',' || c == '=' {
			return false
		}
	}
	return true
}
