package main

import (
	"math"
	"regexp"
	"strconv"
	"strings"
)

// ---------------------------------------------------------------- 时长

var (
	durRe  = regexp.MustCompile(`^(\d+(?:\.\d+)?)(ms|s|m|h|d|w)$`)
	durMult = map[string]float64{"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
)

// ParseDuration 解析 `5m`、`30s`、`4h` 这类官方时长子集。
func ParseDuration(text string) (float64, error) {
	sub := durRe.FindStringSubmatch(strings.TrimSpace(text))
	if sub == nil {
		return 0, errf("无法解析的时长: %q", text)
	}
	v, err := strconv.ParseFloat(sub[1], 64)
	if err != nil {
		return 0, errf("无法解析的时长: %q", text)
	}
	return v * durMult[sub[2]], nil
}

// ---------------------------------------------------------------- 告警对象

// Alert 是 Alertmanager API v2 的告警对象(简化)。Resolved 为 true 表示已结束。
type Alert struct {
	Labels    map[string]string
	Resolved  bool
	StartsAt  float64
}

// Fingerprint 该告警的标签指纹。
func (a Alert) Fingerprint() string { return Fingerprint(a.Labels) }

// ---------------------------------------------------------------- 路由树

// Route 是路由树的一个节点。GroupBy/GroupWait 等为"是否显式设置"显式建模:
// GroupBy 为 nil 表示未设置(继承父 route),空切片表示显式设为空。
// 时长字段为 0 视为未设置(0 不是合法时长)。
type Route struct {
	Name           string
	Receiver       string
	Matchers       []Matcher
	GroupBy        []string
	GroupWait      float64
	GroupInterval  float64
	RepeatInterval float64
	Continue       bool
	Routes         []*Route
}

// Params 是 resolve 后的生效参数。
type Params struct {
	GroupBy        []string
	GroupWait      float64
	GroupInterval  float64
	RepeatInterval float64
}

// DefaultParams 官方默认值:group_wait=30s、group_interval=5m、repeat_interval=4h。
func DefaultParams() Params {
	return Params{GroupBy: []string{}, GroupWait: 30, GroupInterval: 300, RepeatInterval: 14400}
}

// ResolveParams 子 route 未设置的参数继承父 route,一路回退到官方默认值。
func ResolveParams(r *Route, parent *Params) Params {
	p := DefaultParams()
	if parent != nil {
		p = *parent
	}
	if r.GroupBy != nil {
		p.GroupBy = r.GroupBy
	}
	if r.GroupWait != 0 {
		p.GroupWait = r.GroupWait
	}
	if r.GroupInterval != 0 {
		p.GroupInterval = r.GroupInterval
	}
	if r.RepeatInterval != 0 {
		p.RepeatInterval = r.RepeatInterval
	}
	return p
}

// Hit 是一次路由命中的结果。
type Hit struct {
	Receiver string
	Path     []string
	Params   Params
}

// Dispatch 按官方 Match 语义返回命中结果。
//
//	1. 本 route 的 matchers 不命中 -> 空
//	2. 依次尝试子 route;命中某子 route 后若其 Continue 为 false 则停止同层匹配
//	3. 没有任何子 route 贡献结果 -> 由本 route 的 Receiver 兜底
func Dispatch(r *Route, labels map[string]string, parent *Params, path []string) []Hit {
	if len(r.Matchers) > 0 && !LabelsMatch(r.Matchers, labels) {
		return nil
	}
	p := ResolveParams(r, parent)
	var out []Hit
	for _, c := range r.Routes {
		if !LabelsMatch(c.Matchers, labels) {
			continue
		}
		childPath := append(append([]string(nil), path...), c.Name)
		out = append(out, Dispatch(c, labels, &p, childPath)...)
		if !c.Continue {
			break
		}
	}
	if len(out) == 0 && r.Receiver != "" {
		out = append(out, Hit{Receiver: r.Receiver, Path: path, Params: p})
	}
	return out
}

// Receivers 便捷投影:只取命中的 receiver 名。
func Receivers(hits []Hit) []string {
	out := make([]string, 0, len(hits))
	for _, h := range hits {
		out = append(out, h.Receiver)
	}
	return out
}

// ---------------------------------------------------------------- 抑制

// InhibitRule source 命中且与 target 在 Equal 标签上相等时,target 被抑制。
type InhibitRule struct {
	Source []Matcher
	Target []Matcher
	Equal  []string
}

// Inhibition 一条抑制判定。
type Inhibition struct {
	RuleIndex         int
	SourceFingerprint string
}

// EqualLabelsHold equal 里的每个标签在两边都必须存在且相等 —— 缺失等价于空
// 字符串,所以"两边都缺该标签"也算相等。把 instance 写进 equal 后,没有该标签
// 的聚合告警之间会互相抑制,这是最经典的误配。
func EqualLabelsHold(source, target map[string]string, equal []string) bool {
	for _, k := range equal {
		if source[k] != target[k] {
			return false
		}
	}
	return true
}

// InhibitedBy 返回抑制了 target 的判定列表;空表示未被抑制。告警不抑制自己。
func InhibitedBy(target Alert, active []Alert, rules []InhibitRule) []Inhibition {
	var out []Inhibition
	for idx, rule := range rules {
		if !LabelsMatch(rule.Target, target.Labels) {
			continue
		}
		for _, src := range active {
			if src.Fingerprint() == target.Fingerprint() {
				continue
			}
			if !LabelsMatch(rule.Source, src.Labels) {
				continue
			}
			if EqualLabelsHold(src.Labels, target.Labels, rule.Equal) {
				out = append(out, Inhibition{RuleIndex: idx, SourceFingerprint: src.Fingerprint()})
				break
			}
		}
	}
	return out
}

// ---------------------------------------------------------------- 静默

// Silence 动态创建的静默:所有 matcher 命中且时间落在窗口内即生效。
type Silence struct {
	ID        string
	Matchers  []Matcher
	StartsAt  float64
	EndsAt    float64
	CreatedBy string
	Comment   string
}

// ActiveAt 左闭右开:起始时刻生效,结束时刻失效。
func (s Silence) ActiveAt(t float64) bool { return s.StartsAt <= t && t < s.EndsAt }

// Affects 该静默是否作用于这条告警。
func (s Silence) Affects(a Alert, t float64) bool {
	return s.ActiveAt(t) && LabelsMatch(s.Matchers, a.Labels)
}

// SilencedBy 命中该告警的静默 id 列表;多个静默同时命中只是"被静默",幂等。
func SilencedBy(a Alert, silences []Silence, t float64) []string {
	var out []string
	for _, s := range silences {
		if s.Affects(a, t) {
			out = append(out, s.ID)
		}
	}
	return out
}

// EffectiveRepeatInterval repeat_interval 不是 group_interval 的倍数时向上取整。
func EffectiveRepeatInterval(repeatInterval, groupInterval float64) float64 {
	if groupInterval <= 0 {
		return repeatInterval
	}
	return math.Ceil(repeatInterval/groupInterval) * groupInterval
}
