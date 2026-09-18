// Package main 建模 Alertmanager 的 matcher、路由、抑制、静默,以及
// Prometheus 的告警状态机与通知节奏。资料来源见 ../README.md「参考资料」。
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

func errf(format string, a ...interface{}) error { return fmt.Errorf(format, a...) }

// Matcher 是 Alertmanager 的 matcher 语言:key="v" / key!="v" / key=~"re" /
// key!~"re"。没有 IN 操作符 —— 多值只能写成锚定正则,"a|b" 而不是 "a,b"。
type Matcher struct {
	Key   string
	Op    string
	Value string
	re    *regexp.Regexp
}

var matcherRe = regexp.MustCompile(`^([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"([^"]*)"$`)

// NewMatcher 校验运算符;正则分支预编译并做完全锚定(官方对 =~ 锚定)。
func NewMatcher(key, op, value string) (Matcher, error) {
	switch op {
	case "=", "!=", "=~", "!~":
	default:
		return Matcher{}, errf("非法运算符: %q", op)
	}
	m := Matcher{Key: key, Op: op, Value: value}
	if op == "=~" || op == "!~" {
		re, err := regexp.Compile("^(?:" + value + ")$")
		if err != nil {
			return Matcher{}, errf("非法正则: %q", value)
		}
		m.re = re
	}
	return m, nil
}

// ParseMatcher 解析 `severity="critical"`;两边的引号是必需的。
func ParseMatcher(text string) (Matcher, error) {
	sub := matcherRe.FindStringSubmatch(strings.TrimSpace(text))
	if sub == nil {
		return Matcher{}, errf("无法解析的 matcher: %q", text)
	}
	return NewMatcher(sub[1], sub[2], sub[3])
}

// ParseMatchers 批量解析,任一条失败即整体失败。
func ParseMatchers(texts []string) ([]Matcher, error) {
	out := make([]Matcher, 0, len(texts))
	for _, t := range texts {
		m, err := ParseMatcher(t)
		if err != nil {
			return nil, err
		}
		out = append(out, m)
	}
	return out
}

// MustMatchers 仅用于测试夹具。
func MustMatchers(texts ...string) []Matcher {
	m, err := ParseMatchers(texts)
	if err != nil {
		panic(err)
	}
	return m
}

// Matches 判定单条 matcher。缺失标签按空字符串参与匹配,所以
// `env!="prod"` 对没有 env 的告警返回 true —— 与直觉相反。
func (m Matcher) Matches(labels map[string]string) bool {
	actual := labels[m.Key]
	switch m.Op {
	case "=":
		return actual == m.Value
	case "!=":
		return actual != m.Value
	case "=~":
		return m.re.MatchString(actual)
	default:
		return !m.re.MatchString(actual)
	}
}

// LabelsMatch 全部 matcher 命中才算命中(AND 语义);空列表恒真。
func LabelsMatch(matchers []Matcher, labels map[string]string) bool {
	for _, m := range matchers {
		if !m.Matches(labels) {
			return false
		}
	}
	return true
}

// LabelSignature 稳定可读的标签签名,便于失败信息定位。
func LabelSignature(labels map[string]string) string {
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+labels[k])
	}
	return "{" + strings.Join(parts, ", ") + "}"
}

// Fingerprint 标签集指纹。真实实现用 xxhash 并对标签做特定编码,这里用
// SHA-256 前 16 位替代 —— 只保证稳定与区分度,不与实现对位。
func Fingerprint(labels map[string]string) string {
	keys := make([]string, 0, len(labels))
	for k := range labels {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	var b strings.Builder
	for i, k := range keys {
		if i > 0 {
			b.WriteByte(0)
		}
		b.WriteString(k)
		b.WriteByte(1)
		b.WriteString(labels[k])
	}
	sum := sha256.Sum256([]byte(b.String()))
	return hex.EncodeToString(sum[:])[:16]
}

// GroupByAll 是 group_by 的特殊值:按所有标签聚合,即"不聚合、每条告警一组"。
const GroupByAll = "..."

// GroupKey 计算组键。group_by 为 nil 或空 -> 所有告警同一组(键为 "");
// 只含 "..." -> 每条告警独占一组(用指纹当键);否则按指定标签的组合。
func GroupKey(labels map[string]string, groupBy []string) string {
	if len(groupBy) == 1 && groupBy[0] == GroupByAll {
		return Fingerprint(labels)
	}
	if len(groupBy) == 0 {
		return ""
	}
	keys := append([]string(nil), groupBy...)
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+labels[k])
	}
	return strings.Join(parts, "|")
}
