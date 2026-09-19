package main

import (
	"sort"
	"strings"
)

// ------------------------------------------------------------ 示例插件

func nodeResourcesFit() *Plugin {
	return &Plugin{Name: "NodeResourcesFit", Weight: 1,
		FilterFn: func(p *Pod, n *Node) string {
			if n.AllocCPU-n.used() < p.CPU {
				return "Insufficient cpu"
			}
			return ""
		}}
}

func nodeUnschedulable() *Plugin {
	return &Plugin{Name: "NodeUnschedulable", Weight: 1,
		FilterFn: func(p *Pod, n *Node) string {
			if strings.HasPrefix(n.Name, "taint-") {
				return "untolerated taint"
			}
			return ""
		}}
}

func leastAllocated() *Plugin {
	return &Plugin{
		Name: "LeastAllocated", Weight: 1,
		ScoreFn: func(p *Pod, n *Node) int { return (n.AllocCPU - n.used()) * 100 / n.AllocCPU },
		NormalizeFn: func(m map[string]int) map[string]int {
			highest := 0
			for _, v := range m {
				if v > highest {
					highest = v
				}
			}
			if highest == 0 {
				highest = 1
			}
			out := map[string]int{}
			for k, v := range m {
				out[k] = v * nodeScoreMax / highest
			}
			return out
		},
	}
}

func defaultPreemption(cluster map[string]*Node) *Plugin {
	return &Plugin{Name: "DefaultPreemption", Weight: 1,
		PostFilterFn: func(p *Pod, status map[string]string) string {
			names := []string{}
			for k := range status {
				names = append(names, k)
			}
			sort.Strings(names)
			for _, name := range names {
				n := cluster[name]
				freed := 0
				for _, v := range n.Pods {
					if v.Prio < p.Prio {
						freed += v.CPU
					}
				}
				if n.AllocCPU-(n.used()-freed) >= p.CPU {
					return name
				}
			}
			return ""
		}}
}
