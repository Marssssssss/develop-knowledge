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
	"strings"
)

var checks int

func ck(cond bool, msg string) {
	if !cond {
		panic("assert failed: " + msg)
	}
	checks++
}

func pointsOf(trace []string) []string {
	out := []string{}
	for _, t := range trace {
		p := strings.SplitN(t, ":", 2)[0]
		if len(out) == 0 || out[len(out)-1] != p {
			out = append(out, p)
		}
	}
	return out
}

func main() {
	nodes := []*Node{
		{Name: "n1", AllocCPU: 8},
		{Name: "n2", AllocCPU: 4, Pods: []Pod{{Name: "a", CPU: 3}}},
	}
	fw := NewFramework([]*Plugin{nodeResourcesFit(), nodeUnschedulable()})
	r := fw.Schedule(&Pod{Name: "p", CPU: 2}, nodes)

	got := pointsOf(fw.Trace)
	want := []string{"QueueSort", "PreFilter", "Filter", "PreScore", "Score",
		"NormalizeScore", "Reserve", "Permit", "PreBind", "Bind", "PostBind"}
	ck(len(got) == len(want), fmt.Sprintf("扩展点顺序 %v", got))
	for i := range want {
		ck(got[i] == want[i], fmt.Sprintf("第 %d 个扩展点应为 %s, 实得 %s", i, want[i], got[i]))
	}
	ck(r.Phase == phaseBound, "应绑定成功, 实得 "+r.Phase)

	// Filter node 内短路
	n2 := []string{}
	for _, t := range fw.Trace {
		if strings.HasPrefix(t, "Filter:") && strings.HasSuffix(t, ":n2") {
			n2 = append(n2, strings.Split(t, ":")[1])
		}
	}
	ck(len(n2) == 1 && n2[0] == "NodeResourcesFit", fmt.Sprintf("n2 应短路: %v", n2))

	// NormalizeScore
	fw2 := NewFramework([]*Plugin{nodeResourcesFit(), leastAllocated()})
	r2 := fw2.Schedule(&Pod{Name: "p", CPU: 1}, nodes)
	nm := r2.Norm["LeastAllocated"]
	ck(nm["n1"] == nodeScoreMax, fmt.Sprintf("归一化最高分应为 %d, 实得 %v", nodeScoreMax, nm))
	ck(nm["n2"] == 25, fmt.Sprintf("n2 归一化期望 25, 实得 %d", nm["n2"]))
	ck(almost(r2.FinalScores["n1"], 50.0), fmt.Sprintf("n1 最终分期望 50, 实得 %v", r2.FinalScores))
	ck(r2.Node == "n1", "应选 n1, 实得 "+r2.Node)

	// 抢占
	cluster := map[string]*Node{
		"taint-1": {Name: "taint-1", AllocCPU: 4, Pods: []Pod{{Name: "lo", CPU: 4, Prio: 0}}},
		"taint-2": {Name: "taint-2", AllocCPU: 4, Pods: []Pod{{Name: "lo2", CPU: 4, Prio: 0}}},
	}
	list := []*Node{cluster["taint-1"], cluster["taint-2"]}
	fw3 := NewFramework([]*Plugin{nodeResourcesFit(), nodeUnschedulable(), defaultPreemption(cluster)})
	r3 := fw3.Schedule(&Pod{Name: "hi", CPU: 2, Prio: 100}, list)
	ck(containsPoint(fw3.Trace, "PostFilter"), "无可行 node 必须调 PostFilter")
	ck(r3.Nominated == "taint-1", "应提名 taint-1, 实得 "+r3.Nominated)
	ck(r3.Node == "", "PostFilter 只提名不绑定")

	r5 := fw3.Schedule(&Pod{Name: "lo3", CPU: 2, Prio: 0}, list)
	ck(r5.Nominated == "", "优先级不高于 victim 不应抢占")

	// Reserve 失败逆序回滚
	r1 := &Plugin{Name: "R1", Weight: 1,
		ReserveFn:   func(string) bool { return true },
		UnreserveFn: func(string) {}}
	r2p := &Plugin{Name: "R2", Weight: 1,
		ReserveFn:   func(string) bool { return false },
		UnreserveFn: func(string) {}}
	fw7 := NewFramework([]*Plugin{r1, r2p})
	r7 := fw7.Schedule(&Pod{Name: "p", CPU: 1}, []*Node{{Name: "n1", AllocCPU: 4}})
	ck(r7.Phase == phaseUnschedulable, "Reserve 失败不应绑定")
	ck(strings.Join(fw7.UnreserveCalls, ",") == "R1", "只回滚已成功的 R1, 实得 "+strings.Join(fw7.UnreserveCalls, ","))

	// Permit deny
	fw8 := NewFramework([]*Plugin{r1, {Name: "DenyAll", Weight: 1, PermitFn: func() string { return "deny" }}})
	r8 := fw8.Schedule(&Pod{Name: "p", CPU: 1}, []*Node{{Name: "n1", AllocCPU: 4}})
	ck(r8.Phase == phaseUnschedulable, "Permit deny 不应绑定")
	ck(strings.Join(fw8.UnreserveCalls, ",") == "DenyAll,R1", "逆序 Unreserve: "+strings.Join(fw8.UnreserveCalls, ","))

	// Bind 短路
	never := false
	fw10 := NewFramework([]*Plugin{
		{Name: "VolumeBinder", Weight: 1, BindFn: func(string) bool { return true }},
		{Name: "NeverBind", Weight: 1, BindFn: func(string) bool { never = true; return true }},
	})
	r10 := fw10.Schedule(&Pod{Name: "p", CPU: 1}, []*Node{{Name: "n1", AllocCPU: 4}})
	ck(r10.Phase == phaseBound, "应绑定成功")
	ck(!never, "首个接管的 Bind plugin 之后应全部跳过")

	fmt.Printf("scheduler_framework(go): %d assertions passed\n", checks)
}

func almost(a, b float64) bool { return a-b < 1e-9 && b-a < 1e-9 }

func containsPoint(trace []string, p string) bool {
	for _, t := range trace {
		if strings.HasPrefix(t, p+":") {
			return true
		}
	}
	return false
}
