// CFS 完全公平调度器:最小模拟(Go 实现)。
//
// 依据 docs.kernel.org/scheduler/sched-design-CFS.html:
// vruntime += delta * NICE_0 / weight;永远挑 vruntime 最小(最左);
// min_vruntime 单调递增,用于放置新激活实体。
// 断言:等权重平分、权重比即 CPU 份额比、min_vruntime 单调。
package main

import (
	"fmt"
	"os"
)

const (
	nice0   = 1024
	gran    = 8
	totalMS = 1000
)

type task struct {
	name     string
	weight   int64
	vruntime int64
	runtime  int64
}

func leftmost(ts []*task) *task {
	best := ts[0]
	for _, t := range ts[1:] {
		if t.vruntime < best.vruntime {
			best = t // 平手保留表序最前(先入队优先)
		}
	}
	return best
}

func runMeasure(ts []*task, total int64, hist *[]int64) {
	var minVruntime int64
	var ran int64
	for ran < total {
		cur := leftmost(ts)
		cur.vruntime += gran * nice0 / cur.weight // 记账
		cur.runtime += gran
		ran += gran

		m := ts[0].vruntime
		for _, t := range ts[1:] {
			if t.vruntime < m {
				m = t.vruntime
			}
		}
		if m > minVruntime {
			minVruntime = m
		}
		if hist != nil && len(*hist) < 4096 {
			*hist = append(*hist, minVruntime)
		}
	}
}

func monotone(h []int64) bool {
	for i := 1; i < len(h); i++ {
		if h[i] < h[i-1] {
			return false
		}
	}
	return true
}

func check(cond bool, label string) {
	if cond {
		fmt.Println("PASS:", label)
	} else {
		fmt.Println("FAIL:", label)
		os.Exit(1)
	}
}

func absDiff(a, b int64) int64 {
	if a > b {
		return a - b
	}
	return b - a
}

func main() {
	// 场景 1:等权重 -> 平分
	a := &task{"A", 1024, 0, 0}
	b := &task{"B", 1024, 0, 0}
	var hist []int64
	runMeasure([]*task{a, b}, totalMS, &hist)
	check(a.runtime+b.runtime == totalMS, "equal weights: total preserved")
	check(absDiff(a.runtime, b.runtime) <= gran,
		"equal weights: shares within one granularity")
	check(monotone(hist), "min_vruntime is monotone")

	// 场景 2:权重比即份额比(1024 : 512 ~= 2 : 1)
	heavy := &task{"heavy", 1024, 0, 0}
	light := &task{"light", 512, 0, 0}
	runMeasure([]*task{heavy, light}, totalMS, nil)
	check(heavy.runtime+light.runtime == totalMS, "ratio: total preserved")
	check(absDiff(heavy.runtime, 2*light.runtime) <= 2*gran,
		"weight ratio 2:1 is the CPU share")
	check(absDiff(heavy.vruntime, light.vruntime) <= 2*(2*nice0*gran/512),
		"vruntime stays balanced")

	// 场景 3:迟到者以 min_vruntime 放置后立即公平
	early := &task{"E", 1024, 0, 0}
	late := &task{"L", 1024, 0, 0}
	runMeasure([]*task{early}, 500, nil) // E 独跑 500ms
	if late.vruntime < early.vruntime {
		late.vruntime = early.vruntime // clamp 放置
	}
	check(late.vruntime == early.vruntime, "late joiner placed at min_vruntime")
	runMeasure([]*task{early, late}, 500, nil)
	check(absDiff(early.runtime-late.runtime, 500) <= 2*gran,
		"late joiner then fair share (E = L + 500ms)")

	fmt.Println("CFS simulation (Go) passed")
}
