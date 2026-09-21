package main

// demo 519 的 Go 实验台,与 python/main.py 的 E1/E3/E6 同构。
// 运行:`go run .`

import "fmt"

func f6(v float64) string { return fmt.Sprintf("%10.6f", v) }

func main() {
	slo := 0.999
	period := 30 * daySeconds

	// E1 四档阈值
	th, err := Thresholds(slo, period, Google30D)
	if err != nil {
		fmt.Println("error:", err)
		return
	}
	fmt.Println("E1 thresholds (SLO 99.9%, 30d):")
	for _, name := range []string{"page_quick", "page_slow", "ticket_quick", "ticket_slow"} {
		s := th[name]
		fmt.Printf("  %-14s burn=%-10s threshold=%s\n", name, f6(s.Burn), f6(s.Threshold))
	}

	// E3 短窗口叫停:故障 10 分钟后恢复
	timeline := append(repeat(1.0, 10), repeat(0.0, 170)...)
	q := th["page_quick"]
	mwmbLast, staticLast := -1, -1
	for i := range timeline {
		now := float64(i) * minSeconds
		rs, okS := TrailingAvg(timeline, now, q.Short, minSeconds)
		rl, okL := TrailingAvg(timeline, now, q.Long, minSeconds)
		if !okS || !okL {
			continue
		}
		if Fires(rs, rl, q.Threshold, false) {
			mwmbLast = i
		}
		if rl > q.Threshold {
			staticLast = i
		}
	}
	fmt.Printf("\nE3 MWMB stops at minute %d, static 1h stops at %d (diff %d)\n",
		mwmbLast, staticLast, staticLast-mwmbLast)

	// E6 and 语义
	fmt.Println("\nE6 and-semantics:")
	for _, c := range []struct {
		rs, rl float64
	}{
		{0.05, 0.05}, {0.05, 0.001}, {0.0, 0.05}, {0.0, 0.0},
	} {
		fmt.Printf("  short=%-8s long=%-8s -> %v\n",
			f6(c.rs), f6(c.rl), Fires(c.rs, c.rl, q.Threshold, false))
	}
	fmt.Printf("  equal-threshold gt=%v gte=%v\n",
		Fires(q.Threshold, q.Threshold, q.Threshold, false),
		Fires(q.Threshold, q.Threshold, q.Threshold, true))

	// 异常路径
	if _, err := Thresholds(1.0, period, Google30D); err != nil {
		fmt.Println("\nX invalid SLO rejected:", err)
	}
	if _, ok := TrailingAvg([]float64{}, 60, 60, minSeconds); !ok {
		fmt.Println("X empty window -> no data (not zero)")
	}
}

func repeat(v float64, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}
