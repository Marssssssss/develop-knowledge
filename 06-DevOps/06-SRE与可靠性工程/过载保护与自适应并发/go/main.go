package main

// demo 521 的 Go 实验台,与 python/main.py 的 E1/E4/E8/E10 同构。
// 运行:`go run .`

import (
	"fmt"
	"math/rand"
)

const (
	minRtt = 50.0
	buffer = 10.0
)

func f4(v float64) string { return fmt.Sprintf("%10.4f", v) }

func main() {
	// E1 梯度
	b := BufferValue(minRtt, buffer)
	fmt.Println("E1 buffer B =", f4(b))
	for _, srtt := range []float64{25, 50, 55, 60, 100, 200} {
		g, _ := Gradient(minRtt, buffer, srtt)
		fmt.Printf("  sampleRTT=%-7.1f gradient=%s\n", srtt, f4(g))
	}

	// E4 稳态:闭式 vs 迭代
	fmt.Println("\nE4 fixed point (closed form vs 400-step iteration):")
	for _, srtt := range []float64{60, 80, 100} {
		g, _ := Gradient(minRtt, buffer, srtt)
		fp, okFp := FixedPoint(g)
		traj, _ := Iterate(25.0, minRtt, buffer,
			func(_ int, _ float64) float64 { return srtt }, 1.0, 400)
		if !okFp {
			fmt.Printf("  sampleRTT=%.0f g=%.6f -> no fixed point\n", srtt, g)
			continue
		}
		fmt.Printf("  sampleRTT=%-6.0f g=%-10s closed=%-12s iter=%-12s diff=%.2e\n",
			srtt, f4(g), f4(fp), f4(traj[len(traj)-1]), mathAbs(fp-traj[len(traj)-1]))
	}

	// E8 两种 trigger
	fmt.Println("\nE8 pressure -> threshold(.7) / scaled(.5,.9)")
	for _, p := range []float64{0.0, 0.5, 0.6, 0.7, 0.71, 0.9} {
		s, _ := ScaledTrigger(p, 0.5, 0.9)
		fmt.Printf("  p=%-6.2f threshold=%-6.1f scaled=%s\n",
			p, ThresholdTrigger(p, 0.7), f4(s))
	}

	// E10 端到端平衡(含过冲)
	fn := func(_ int, limit float64) float64 {
		return minRtt + mathMax(0.0, limit-120.0)*0.5
	}
	traj, _ := Iterate(25.0, minRtt, buffer, fn, 1.0, 60)
	last := traj[len(traj)-1]
	peak, peakIdx := 0.0, 0
	for i, v := range traj {
		if v > peak {
			peak, peakIdx = v, i
		}
	}
	fmt.Printf("\nE10 converged=%s peak=%s at step %d (overshoot %.4f)\n",
		f4(last), f4(peak), peakIdx, peak-last)

	// minRTT 重算触发
	c := NewMinRttController()
	fmt.Println("\nE6 minRTT retrigger on 5 consecutive windows at min:")
	for i := 0; i < 6; i++ {
		fmt.Printf("  window %d limit=3 -> %v\n", i, c.Observe(3))
	}

	// 异常路径
	if _, err := Gradient(minRtt, buffer, 0); err != nil {
		fmt.Println("\nX sampleRTT=0 rejected:", err)
	}
	if _, err := ScaledTrigger(0.5, 0.9, 0.5); err != nil {
		fmt.Println("X saturation<=scaling rejected:", err)
	}
	fmt.Println("X memory pressure with no limit:", MemoryPressure(8, -1))
	_ = rand.New(rand.NewSource(1))
}

func mathAbs(v float64) float64 {
	if v < 0 {
		return -v
	}
	return v
}

func mathMax(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
