// kube-scheduler Scheduling Framework 最小实现 (Go)。
//
// 权威来源(实际读过):
//   1. https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
//   2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/scheduler/framework/interface.go
//
// 建模要点(见 python 版同 README):调度周期 + 绑定周期;Filter 在 node 内短路;
// PostFilter 仅在无可行 node 时触发(抢占);NormalizeScore 把最高分拉到 NodeScoreMax;
// Reserve 失败 → 全部 Reserve plugin 逆序 Unreserve;Bind 首个接管者短路。
package main

import (
	"sort"
	"strings"
)

// kube-scheduler Scheduling Framework 最小实现 (Go)。
//
// 权威来源(实际读过):
//   1. https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
//   2. https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/scheduler/framework/interface.go
//
// 建模要点(见 python 版同 README):调度周期 + 绑定周期;Filter 在 node 内短路;
// PostFilter 仅在无可行 node 时触发(抢占);NormalizeScore 把最高分拉到 NodeScoreMax;
// Reserve 失败 → 全部 Reserve plugin 逆序 Unreserve;Bind 首个接管者短路。
package main

import (
	"fmt"
	"sort"
	"strings"
)

const nodeScoreMax = 100

type Pod struct {
	Name string
	CPU  int
	Prio int
}

type Node struct {
	Name     string
	AllocCPU int
	Pods     []Pod
}

func (n *Node) used() int {
	s := 0
	for _, p := range n.Pods {
		s += p.CPU
	}
	return s
}

// Plugin 用函数字段表达扩展点;nil 表示"未注册该扩展点"(等价于 no-op/放行)。
type Plugin struct {
	Name          string
	Weight        int
	PreFilterFn   func(p *Pod) string
	FilterFn      func(p *Pod, n *Node) string
	PostFilterFn  func(p *Pod, status map[string]string) string
	ScoreFn       func(p *Pod, n *Node) int
	NormalizeFn   func(map[string]int) map[string]int
	ReserveFn     func(node string) bool
	UnreserveFn   func(node string)
	PermitFn      func() string
	BindFn        func(node string) bool
	PreBindFn     func(node string) bool
	PostBindFn    func(node string)
}

type Framework struct {
	Plugins        []*Plugin
	Trace          []string
	State          map[string][]string
	UnreserveCalls []string
}

func NewFramework(ps []*Plugin) *Framework {
	return &Framework{Plugins: ps, State: map[string][]string{}}
}

const (
	phaseUnschedulable = "unschedulable"
	phaseBound         = "bound"
	phaseWaiting       = "waiting"
	phaseNominated     = "nominated"
)

type Result struct {
	Phase       string
	Node        string
	Nominated   string
	FinalScores map[string]float64
	Norm        map[string]map[string]int
}

func (fw *Framework) record(s string) { fw.Trace = append(fw.Trace, s) }

func (fw *Framework) unreserveAll(node string, reserved []*Plugin) {
	for i := len(reserved) - 1; i >= 0; i-- {
		p := reserved[i]
		fw.record("Unreserve:" + p.Name)
		fw.UnreserveCalls = append(fw.UnreserveCalls, p.Name)
		if p.UnreserveFn != nil {
			p.UnreserveFn(node)
		}
	}
}

func (fw *Framework) Schedule(pod *Pod, nodes []*Node) Result {
	fw.Trace = nil
	fw.UnreserveCalls = nil
	out := Result{Phase: phaseUnschedulable, FinalScores: map[string]float64{},
		Norm: map[string]map[string]int{}}
	fw.record("QueueSort:-")

	// PreFilter
	for _, p := range fw.Plugins {
		fw.record("PreFilter:" + p.Name)
		if p.PreFilterFn != nil {
			if err := p.PreFilterFn(pod); err != "" {
				out.Phase = "prefilter_abort"
				return out
			}
		}
	}

	// Filter:node 内短路
	feasible := []*Node{}
	status := map[string]string{}
	for _, n := range nodes {
		rejected := ""
		for _, p := range fw.Plugins {
			fw.record("Filter:" + p.Name + ":" + n.Name)
			if p.FilterFn != nil {
				if r := p.FilterFn(pod, n); r != "" {
					rejected = r
					status[n.Name] = r
					break
				}
			}
		}
		if rejected == "" {
			feasible = append(feasible, n)
		}
	}

	// PostFilter:仅无可行 node
	if len(feasible) == 0 {
		for _, p := range fw.Plugins {
			fw.record("PostFilter:" + p.Name)
			if p.PostFilterFn != nil {
				if nn := p.PostFilterFn(pod, status); nn != "" {
					out.Nominated = nn
					out.Phase = phaseNominated
					return out
				}
			}
		}
		return out
	}

	// PreScore → Score → NormalizeScore
	raw := map[string]map[string]int{}
	for _, p := range fw.Plugins {
		fw.record("PreScore:" + p.Name)
	}
	for _, p := range fw.Plugins {
		fw.record("Score:" + p.Name)
		m := map[string]int{}
		for _, n := range feasible {
			if p.ScoreFn != nil {
				m[n.Name] = p.ScoreFn(pod, n)
			}
		}
		raw[p.Name] = m
	}
	for _, p := range fw.Plugins {
		fw.record("NormalizeScore:" + p.Name)
		if p.NormalizeFn != nil {
			out.Norm[p.Name] = p.NormalizeFn(raw[p.Name])
		} else {
			out.Norm[p.Name] = raw[p.Name]
		}
	}
	totalW := 0
	for _, p := range fw.Plugins {
		totalW += p.Weight
	}
	if totalW == 0 {
		totalW = 1
	}
	for _, p := range fw.Plugins {
		for _, n := range feasible {
			out.FinalScores[n.Name] += float64(p.Weight) * float64(out.Norm[p.Name][n.Name]) / float64(totalW)
		}
	}
	sort.Slice(feasible, func(i, j int) bool {
		if out.FinalScores[feasible[i].Name] != out.FinalScores[feasible[j].Name] {
			return out.FinalScores[feasible[i].Name] > out.FinalScores[feasible[j].Name]
		}
		return feasible[i].Name < feasible[j].Name
	})
	best := feasible[0]
	out.Node = best.Name

	// Reserve
	reserved := []*Plugin{}
	for _, p := range fw.Plugins {
		fw.record("Reserve:" + p.Name)
		if p.ReserveFn != nil && !p.ReserveFn(best.Name) {
			fw.unreserveAll(best.Name, reserved)
			return out
		}
		reserved = append(reserved, p)
	}

	// Permit
	for _, p := range fw.Plugins {
		fw.record("Permit:" + p.Name)
		if p.PermitFn != nil {
			switch p.PermitFn() {
			case "deny":
				fw.unreserveAll(best.Name, reserved)
				return out
			case "wait":
				fw.unreserveAll(best.Name, reserved)
				out.Phase = phaseWaiting
				return out
			}
		}
	}

	for _, p := range fw.Plugins {
		fw.record("PreBind:" + p.Name)
		if p.PreBindFn != nil && !p.PreBindFn(best.Name) {
			fw.unreserveAll(best.Name, reserved)
			return out
		}
	}
	for _, p := range fw.Plugins {
		fw.record("Bind:" + p.Name)
		if p.BindFn != nil && p.BindFn(best.Name) {
			break // 首个接管者之后全部跳过
		}
	}
	for _, p := range fw.Plugins {
		fw.record("PostBind:" + p.Name)
		if p.PostBindFn != nil {
			p.PostBindFn(best.Name)
		}
	}
	out.Phase = phaseBound
	return out
}

// ------------------------------------------------------------ 自检
