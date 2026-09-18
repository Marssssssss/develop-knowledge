package main

import (
	"fmt"
	"strconv"
	"strings"
)

// 时长单位与 Prometheus model/time.go 口径一致（y = 365d）。
var timeUnits = map[string]float64{
	"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000,
}

var rangeFuncs = map[string]bool{
	"rate": true, "increase": true, "delta": true,
	"irate": true, "idelta": true, "resets": true, "changes": true,
}

// ParseDuration 解析 PromQL 时长字面量，如 5m / 1h30m / 250ms。
func ParseDuration(s string) (float64, error) {
	s = strings.TrimSpace(s)
	total, i := 0.0, 0
	for i < len(s) {
		j := i
		for j < len(s) && (isDigit(s[j]) || s[j] == '.') {
			j++
		}
		if j == i {
			return 0, fmt.Errorf("非法时长字面量: %q", s)
		}
		num, err := strconv.ParseFloat(s[i:j], 64)
		if err != nil {
			return 0, err
		}
		k := j
		for k < len(s) && !isDigit(s[k]) && s[k] != '.' {
			k++
		}
		mult, ok := timeUnits[s[j:k]]
		if !ok {
			return 0, fmt.Errorf("未知时长单位 %q (in %q)", s[j:k], s)
		}
		total += num * mult
		i = k
	}
	if i == 0 {
		return 0, fmt.Errorf("非法时长字面量: %q", s)
	}
	return total, nil
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }

func isIdentChar(c byte) bool {
	return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || isDigit(c) || c == '_' || c == ':'
}

// matchBrace 返回 s[start] 处开括号配对闭括号的下标。
func matchBrace(s string, start int, openCh, closeCh byte) (int, error) {
	if start >= len(s) || s[start] != openCh {
		return 0, fmt.Errorf("期望 %q", string(openCh))
	}
	depth := 0
	for i := start; i < len(s); i++ {
		switch s[i] {
		case openCh:
			depth++
		case closeCh:
			depth--
			if depth == 0 {
				return i, nil
			}
		}
	}
	return 0, fmt.Errorf("括号不配对: %q", s[start:])
}

// splitTopLevel 按顶层分隔符切分（跳过引号与括号内）。
func splitTopLevel(text string, sep byte) []string {
	parts, buf, depth, quote := []string{}, []byte{}, 0, byte(0)
	for i := 0; i < len(text); i++ {
		c := text[i]
		if quote != 0 {
			buf = append(buf, c)
			if c == quote {
				quote = 0
			}
			continue
		}
		switch c {
		case '"', '\'', '`':
			quote = c
		case '{', '[', '(':
			depth++
		case '}', ']', ')':
			depth--
		}
		if c == sep && depth == 0 {
			parts = append(parts, string(buf))
			buf = []byte{}
			continue
		}
		buf = append(buf, c)
	}
	parts = append(parts, string(buf))
	out := []string{}
	for _, p := range parts {
		if strings.TrimSpace(p) != "" {
			out = append(out, p)
		}
	}
	return out
}

// ParseMatchers 解析 {job="a",env=~"b|c"} 的花括号内部。
func ParseMatchers(body string) ([]*Matcher, error) {
	out := []*Matcher{}
	for _, item := range splitTopLevel(body, ',') {
		item = strings.TrimSpace(item)
		eq := strings.IndexAny(item, "=!~")
		if eq < 0 {
			return nil, fmt.Errorf("非法 matcher: %q", item)
		}
		label := strings.TrimSpace(item[:eq])
		rest := item[eq:]
		op := ""
		switch {
		case strings.HasPrefix(rest, "=~"):
			op, rest = "=~", rest[2:]
		case strings.HasPrefix(rest, "!~"):
			op, rest = "!~", rest[2:]
		case strings.HasPrefix(rest, "!="):
			op, rest = "!=", rest[2:]
		case strings.HasPrefix(rest, "="):
			op, rest = "=", rest[1:]
		default:
			return nil, fmt.Errorf("非法 matcher: %q", item)
		}
		val := strings.TrimSpace(rest)
		if len(val) < 2 || val[0] != val[len(val)-1] ||
			(val[0] != '"' && val[0] != '\'' && val[0] != 0x60) {
			return nil, fmt.Errorf("matcher 的值必须加引号: %q", item)
		}
		m, err := NewMatcher(label, op, val[1:len(val)-1])
		if err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out, nil
}
