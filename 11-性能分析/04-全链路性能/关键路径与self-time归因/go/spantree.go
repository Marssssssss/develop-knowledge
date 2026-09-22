// Package spantree 做 span 树的 self time、关键路径与跨进程配对归因。
//
// 数据模型来自 OpenTelemetry Trace API 规范：
//   - 同一 trace 的 span 共享 trace_id；parent_id 为空的是根 span；
//   - SpanKind 中 CLIENT/PRODUCER 是出站，SERVER/CONSUMER 是入站；
//     "When the context of a CLIENT span is propagated, CLIENT span usually
//     becomes a parent of a remote SERVER span" —— 这是两侧配对与差值归因的依据。
//
// Go 与 Python 转写的差异点：
//   - map 遍历顺序随机，所以 children 必须显式排序后再做顺序敏感的计算，
//     否则"关键路径"会在同分（tie）时给出不确定的结果；
//   - 用 float64 表示毫秒，比较时统一走 eps，不要直接 ==。
package spantree

import "sort"

// Kind 是 SpanKind。
type Kind string

const (
	// Client 表示出站调用，等待响应。
	Client Kind = "CLIENT"
	// Server 表示处理外部发起的入站请求。
	Server Kind = "SERVER"
	// Internal 是默认值，表示进程内操作。
	Internal Kind = "INTERNAL"
	// Producer 表示发起/调度一个延迟执行的操作。
	Producer Kind = "PRODUCER"
	// Consumer 表示处理 producer 发起的操作。
	Consumer Kind = "CONSUMER"
)

// Span 是一条 span 的最小字段集。
type Span struct {
	SpanID   string
	ParentID string
	Name     string
	Kind     Kind
	StartMS  float64
	EndMS    float64
}

// Duration 返回 span 时长（毫秒）。
func (s *Span) Duration() float64 { return s.EndMS - s.StartMS }

// Tree 是按 parent_id 建好的树。
type Tree struct {
	ByID    map[string]*Span
	Kids    map[string][]*Span
	Roots   []*Span
	Orphans []*Span
}

// Build 建树。parent_id 非空但父 span 不在集合里的 span 记为 Orphan，
// 并提升为伪根——否则它会连带自己的子树一起从视图里消失。
func Build(spans []*Span) *Tree {
	t := &Tree{
		ByID: map[string]*Span{},
		Kids: map[string][]*Span{},
	}
	for _, s := range spans {
		t.ByID[s.SpanID] = s
	}
	for _, s := range spans {
		switch {
		case s.ParentID == "":
			t.Roots = append(t.Roots, s)
		case t.ByID[s.ParentID] != nil:
			t.Kids[s.ParentID] = append(t.Kids[s.ParentID], s)
		default:
			t.Orphans = append(t.Orphans, s)
			t.Roots = append(t.Roots, s)
		}
	}
	// 稳定顺序：让 tie 的结果可复现
	sort.Slice(t.Roots, func(i, j int) bool { return t.Roots[i].SpanID < t.Roots[j].SpanID })
	return t
}

// Children 返回某 span 的子节点（可能为空切片）。
func (t *Tree) Children(id string) []*Span { return t.Kids[id] }

// SelfTime 是 duration - Σ child.duration。子 span 并发时为负——这是并发的信号。
func (t *Tree) SelfTime(s *Span) float64 {
	total := 0.0
	for _, c := range t.Kids[s.SpanID] {
		total += c.Duration()
	}
	return s.Duration() - total
}

// ParallelWait 是 duration - max(child.duration)；并发子 span 下才有意义。
func (t *Tree) ParallelWait(s *Span) float64 {
	cs := t.Kids[s.SpanID]
	if len(cs) == 0 {
		return s.Duration()
	}
	mx := 0.0
	for _, c := range cs {
		if c.Duration() > mx {
			mx = c.Duration()
		}
	}
	return s.Duration() - mx
}

// CriticalPath 以 self time 为点权求根到叶最长路径，返回 (合计, span_id 链)。
// 同分时取 span_id 字典序最小者，保证 Go 侧结果确定。
func (t *Tree) CriticalPath(s *Span) (float64, []string) {
	own := t.SelfTime(s)
	bestCost, bestPath := 0.0, []string(nil)
	kids := append([]*Span(nil), t.Kids[s.SpanID]...)
	sort.Slice(kids, func(i, j int) bool { return kids[i].SpanID < kids[j].SpanID })
	for _, c := range kids {
		cost, path := t.CriticalPath(c)
		if bestPath == nil || cost > bestCost {
			bestCost, bestPath = cost, path
		}
	}
	return own + bestCost, append([]string{s.SpanID}, bestPath...)
}

// Gap 是一次跨进程跳转两侧的时长差。
type Gap struct {
	ClientID string
	ServerID string
	ClientMS float64
	ServerMS float64
	GapMS    float64
}

func isPair(parent, child Kind) bool {
	return (parent == Client && child == Server) || (parent == Producer && child == Consumer)
}

// PairGaps 找出所有 (CLIENT, SERVER) / (PRODUCER, CONSUMER) 父子对并算差值。
func (t *Tree) PairGaps() []Gap {
	out := []Gap(nil)
	ids := make([]string, 0, len(t.ByID))
	for id := range t.ByID {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		p := t.ByID[id]
		for _, c := range t.Kids[id] {
			if !isPair(p.Kind, c.Kind) {
				continue
			}
			out = append(out, Gap{
				ClientID: p.SpanID, ServerID: c.SpanID,
				ClientMS: p.Duration(), ServerMS: c.Duration(),
				GapMS:    p.Duration() - c.Duration(),
			})
		}
	}
	return out
}

// Anomalies 报出结构性异常：多根、孤儿、负 self time。
func (t *Tree) Anomalies() []string {
	out := []string(nil)
	if len(t.Roots) > 1 {
		out = append(out, "multi_root")
	}
	if len(t.Orphans) > 0 {
		ids := make([]string, 0, len(t.Orphans))
		for _, s := range t.Orphans {
			ids = append(ids, s.SpanID)
		}
		sort.Strings(ids)
		out = append(out, "orphan:"+joinIDs(ids))
	}
	ids := make([]string, 0, len(t.ByID))
	for id := range t.ByID {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		if t.SelfTime(t.ByID[id]) < -1e-9 {
			out = append(out, "negative_self_time:"+id)
		}
	}
	return out
}

func joinIDs(ids []string) string {
	out := ""
	for i, id := range ids {
		if i > 0 {
			out += ","
		}
		out += id
	}
	return out
}
