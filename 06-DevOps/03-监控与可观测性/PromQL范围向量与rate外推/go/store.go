// Package main: 迷你内存 TSDB 的 Go 实现（样本存储 / 标签匹配 / lookback / staleness）。
//
// 语义照 Prometheus 官方 "Querying basics"：
//   - 即时选择器取「at or before 求值时刻的最新样本」，且该样本必须比 lookback
//     周期更新（严格小于）；lookback 默认 5m。
//   - 正则 matcher 完全锚定（env=~"foo" 等价 env=~"^foo$"）。
//   - 缺失标签与空值标签在匹配语义上等价。
//   - 选择器必须给出指标名，或至少一个不匹配空值的 matcher。
package main

import (
	"errors"
	"fmt"
	"regexp"
	"sort"
	"strings"
)

// Sample 是时间戳-值对，时间单位为秒。
type Sample struct {
	T float64
	V float64
}

// entry 是序列上的一个时间点：普通样本或 staleness 标记。
type entry struct {
	T     float64
	V     float64
	Stale bool
}

// Matcher 是单个标签匹配器，Op ∈ {"=", "!=", "=~", "!~"}。
type Matcher struct {
	Label string
	Op    string
	Value string
	re    *regexp.Regexp
}

// NewMatcher 构造匹配器；正则按 PromQL 口径**完全锚定**。
func NewMatcher(label, op, value string) (*Matcher, error) {
	m := &Matcher{Label: label, Op: op, Value: value}
	switch op {
	case "=", "!=":
	case "=~", "!~":
		re, err := regexp.Compile("^(?:" + value + ")$")
		if err != nil {
			return nil, err
		}
		m.re = re
	default:
		return nil, errors.New("非法匹配运算符: " + op)
	}
	return m, nil
}

// Matches 报告一组标签是否通过该匹配器。
func (m *Matcher) Matches(labels map[string]string) bool {
	actual := labels[m.Label] // 缺失即空串
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

// MatchesEmpty 报告该 matcher 是否接受空值（= 不指定该标签也能通过）。
func (m *Matcher) MatchesEmpty() bool {
	switch m.Op {
	case "=":
		return m.Value == ""
	case "!=":
		return m.Value != ""
	case "=~":
		return m.re.MatchString("")
	default:
		return !m.re.MatchString("")
	}
}

// SelectorLegal 对应官方约束：必须给出指标名，或至少一个不匹配空值的 matcher。
func SelectorLegal(name string, ms []*Matcher) bool {
	if name != "" {
		return true
	}
	for _, m := range ms {
		if !m.MatchesEmpty() {
			return true
		}
	}
	return false
}

// Series 是一条时间序列。
type Series struct {
	Name    string
	Labels  map[string]string
	Entries []entry
}

// Add 追加样本；时间必须严格递增。
func (s *Series) Add(t, v float64) error {
	if n := len(s.Entries); n > 0 && t <= s.Entries[n-1].T {
		return fmt.Errorf("样本必须按时间递增写入: %v", t)
	}
	s.Entries = append(s.Entries, entry{T: t, V: v})
	return nil
}

// MarkStale 写入 staleness 标记。
func (s *Series) MarkStale(t float64) error {
	if n := len(s.Entries); n > 0 && t <= s.Entries[n-1].T {
		return fmt.Errorf("staleness 标记必须按时间递增写入: %v", t)
	}
	s.Entries = append(s.Entries, entry{T: t, Stale: true})
	return nil
}

// Point 是即时向量的一个元素。
type Point struct {
	Labels map[string]string
	V      float64
}

// MatrixEntry 是范围向量的一个元素。
type MatrixEntry struct {
	Labels map[string]string
	Pts    []Sample
}

// MemStore 是极简内存时序库。
type MemStore struct {
	Lookback float64
	series   map[string]*Series
	keys     []string
}

// NewMemStore 构造存储；lookback 传 0 时取默认 300s。
func NewMemStore(lookback float64) *MemStore {
	if lookback == 0 {
		lookback = 300
	}
	return &MemStore{Lookback: lookback, series: map[string]*Series{}}
}

func seriesKey(name string, labels map[string]string) string {
	ks := make([]string, 0, len(labels))
	for k := range labels {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	var b strings.Builder
	b.WriteString(name)
	for _, k := range ks {
		b.WriteString("\x00")
		b.WriteString(k)
		b.WriteString("\x01")
		b.WriteString(labels[k])
	}
	return b.String()
}

// Series 返回（必要时创建）指定名字与标签的序列。
func (st *MemStore) Series(name string, labels map[string]string) *Series {
	k := seriesKey(name, labels)
	if s, ok := st.series[k]; ok {
		return s
	}
	cp := map[string]string{}
	for kk, vv := range labels {
		cp[kk] = vv
	}
	s := &Series{Name: name, Labels: cp}
	st.series[k] = s
	st.keys = append(st.keys, k)
	sort.Strings(st.keys)
	return s
}

// Add 便捷写入。
func (st *MemStore) Add(name string, labels map[string]string, t, v float64) error {
	return st.Series(name, labels).Add(t, v)
}

// MarkStale 便捷写入 staleness 标记。
func (st *MemStore) MarkStale(name string, labels map[string]string, t float64) error {
	return st.Series(name, labels).MarkStale(t)
}

func (st *MemStore) matching(name string, ms []*Matcher) []*Series {
	out := []*Series{}
	for _, k := range st.keys {
		s := st.series[k]
		if name != "" && s.Name != name {
			continue
		}
		ok := true
		for _, m := range ms {
			if !m.Matches(s.Labels) {
				ok = false
				break
			}
		}
		if ok {
			out = append(out, s)
		}
	}
	return out
}

// Select 求即时向量；被 lookback 或 staleness 排除的序列不出现。
func (st *MemStore) Select(name string, ms []*Matcher, evalT float64) []Point {
	out := []Point{}
	for _, s := range st.matching(name, ms) {
		var found *entry
		for i := range s.Entries {
			if s.Entries[i].T > evalT {
				break
			}
			found = &s.Entries[i]
		}
		if found == nil || found.Stale {
			continue
		}
		// 严格小于：恰好等于 lookback 周期时**不**返回
		if evalT-found.T >= st.Lookback {
			continue
		}
		out = append(out, Point{Labels: s.Labels, V: found.V})
	}
	return out
}

// RangeSelect 求范围向量，区间为**左开右闭** (start, end]。
func (st *MemStore) RangeSelect(name string, ms []*Matcher, start, end float64) []MatrixEntry {
	out := []MatrixEntry{}
	for _, s := range st.matching(name, ms) {
		pts := []Sample{}
		for _, e := range s.Entries {
			if e.T <= start || e.T > end {
				continue
			}
			if e.Stale {
				pts = []Sample{} // staleness 标记把序列切断
				continue
			}
			pts = append(pts, Sample{T: e.T, V: e.V})
		}
		if len(pts) > 0 {
			out = append(out, MatrixEntry{Labels: s.Labels, Pts: pts})
		}
	}
	return out
}
