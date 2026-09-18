package main

// LogQL / Loki 的基础类型：日志条目、错误标签、单位换算、流选择器。
//
// 与 Python 版刻意保持同一套语义。跨语言移植这里有三处最容易写坏的地方，
// 都在下面的注释里点名：
//
//  1. Go 的 re.MatchString 是**搜索**语义，而 LogQL 流选择器的 =~ 是
//     **完全锚定**的；直接拿 MatchString 当匹配器会得到错误的宽泛结果。
//  2. 行过滤 |~ 反过来是**搜索**语义。同一个 `~`，两边锚定行为相反。
//  3. 缺失标签必须按空字符串处理（Go 里恰好是 map 的零值，误打误撞对了，
//     但不能因此以为"取不到就不匹配"）。

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const (
	// 官方文档正文把管线错误标签写作 error，实际实现用 __error__。
	ErrorLabel          = "__error__"
	JSONParserErr       = "JSONParserErr"
	LogfmtParserErr     = "LogfmtParserErr"
	RegexpParserErr     = "RegexpParserErr"
	PatternParserErr    = "PatternParserErr"
	SampleExtractionErr = "SampleExtractionErr"
)

// Entry 一条日志。Labels 可变：管线阶段会往里塞解析出来的标签。
type Entry struct {
	TsNs     int64
	Line     string
	Labels   map[string]string
	Value    float64 // unwrap 出来的数值样本
	HasValue bool
}

func NewEntry(tsNs int64, line string, labels map[string]string) *Entry {
	cp := make(map[string]string, len(labels))
	for k, v := range labels {
		cp[k] = v
	}
	return &Entry{TsNs: tsNs, Line: line, Labels: cp}
}

func (e *Entry) Copy() *Entry {
	cp := make(map[string]string, len(e.Labels))
	for k, v := range e.Labels {
		cp[k] = v
	}
	return &Entry{TsNs: e.TsNs, Line: e.Line, Labels: cp, Value: e.Value, HasValue: e.HasValue}
}

// AppendError 打上管线错误标签。多个错误如何合并属于实现细节，这里用逗号累加。
func (e *Entry) AppendError(errName string) {
	if old, ok := e.Labels[ErrorLabel]; ok {
		e.Labels[ErrorLabel] = old + ", " + errName
		return
	}
	e.Labels[ErrorLabel] = errName
}

// SeriesKey 把标签集压成稳定字符串键（排序后连接）。
// __error__ 不参与标识 —— 它是诊断量，不是流的身份。
func SeriesKey(labels map[string]string) string {
	keys := make([]string, 0, len(labels))
	for k := range labels {
		if k == ErrorLabel {
			continue
		}
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for _, k := range keys {
		b.WriteString(k)
		b.WriteByte(0x01)
		b.WriteString(labels[k])
		b.WriteByte(0x00)
	}
	return b.String()
}

// Matcher 流选择器里的一个标签匹配条件。
type Matcher struct {
	Label string
	Op    string
	Value string
	re    *regexp.Regexp
}

func NewMatcher(label, op, value string) (Matcher, error) {
	switch op {
	case "=", "!=":
		return Matcher{Label: label, Op: op, Value: value}, nil
	case "=~", "!~":
		// 必须自己补 ^(?:...)$ —— Go 的 MatchString 是搜索语义，
		// 不加锚点 /ready 会被 /readyz 命中，与 LogQL 的完全锚定不符。
		re, err := regexp.Compile("^(?:" + value + ")$")
		if err != nil {
			return Matcher{}, fmt.Errorf("非法正则 %q: %w", value, err)
		}
		return Matcher{Label: label, Op: op, Value: value, re: re}, nil
	}
	return Matcher{}, fmt.Errorf("非法匹配运算符: %q", op)
}

func (m Matcher) Matches(labels map[string]string) bool {
	candidate := labels[m.Label] // 缺失标签取零值 ""，等价于「缺失 ≡ 空串」
	switch m.Op {
	case "=":
		return candidate == m.Value
	case "!=":
		return candidate != m.Value
	case "=~":
		return m.re.MatchString(candidate)
	}
	return !m.re.MatchString(candidate)
}

// StreamSelector 多个 matcher 之间是 AND。
type StreamSelector struct {
	Matchers []Matcher
}

func (s StreamSelector) Matches(labels map[string]string) bool {
	for _, m := range s.Matchers {
		if !m.Matches(labels) {
			return false
		}
	}
	return true
}

// ---------------------------------------------------------------- 单位换算
//
// 字节单位是 1024 进制：官方把 chunk_target_size 的默认值 1572864 注释为
// 「1.5 MB」，1.5 × 1024² = 1572864。按 1000 进制算会与官方注释对不上。

var durationUnits = map[string]float64{
	"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1,
	"m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000,
}

var byteUnits = map[string]float64{
	"b": 1, "kb": 1024, "kib": 1024,
	"mb": 1024 * 1024, "mib": 1024 * 1024,
	"gb": 1024 * 1024 * 1024, "gib": 1024 * 1024 * 1024,
	"tb": 1024 * 1024 * 1024 * 1024,
}

var numRe = regexp.MustCompile(`^(-?\d+(?:\.\d+)?)([A-Za-z]*)$`)
var compoundRe = regexp.MustCompile(`(\d+(?:\.\d+)?)([A-Za-z]+)`)

// ParseDuration 把 5m / 1h30m 转成秒。
func ParseDuration(text string) (float64, error) {
	if m := numRe.FindStringSubmatch(text); m != nil {
		unit := strings.ToLower(m[2])
		factor, ok := durationUnits[unit]
		if !ok {
			return 0, fmt.Errorf("未知时长单位: %q", unit)
		}
		value, err := strconv.ParseFloat(m[1], 64)
		if err != nil {
			return 0, err
		}
		return value * factor, nil
	}
	total := 0.0
	pos := 0
	for _, idx := range compoundRe.FindAllStringSubmatchIndex(text, -1) {
		if idx[0] != pos {
			return 0, fmt.Errorf("非法时长字面量: %q", text)
		}
		unit := strings.ToLower(text[idx[4]:idx[5]])
		factor, ok := durationUnits[unit]
		if !ok {
			return 0, fmt.Errorf("未知时长单位: %q", unit)
		}
		value, err := strconv.ParseFloat(text[idx[2]:idx[3]], 64)
		if err != nil {
			return 0, err
		}
		total += value * factor
		pos = idx[1]
	}
	if pos == 0 || pos != len(text) {
		return 0, fmt.Errorf("非法时长字面量: %q", text)
	}
	return total, nil
}

// ParseBytes 把 256KB / 1.5MB 转成字节（1024 进制）。
func ParseBytes(text string) (float64, error) {
	m := numRe.FindStringSubmatch(text)
	if m == nil {
		return 0, fmt.Errorf("非法字节字面量: %q", text)
	}
	unit := strings.ToLower(m[2])
	if unit == "" {
		return 0, fmt.Errorf("字节字面量缺少单位: %q", text)
	}
	factor, ok := byteUnits[unit]
	if !ok {
		return 0, fmt.Errorf("未知字节单位: %q", unit)
	}
	value, err := strconv.ParseFloat(m[1], 64)
	if err != nil {
		return 0, err
	}
	return value * factor, nil
}
