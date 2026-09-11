// 直方分位数估算 histogram_quantile —— demo (Go, stdlib only)
// 依据 https://prometheus.io/docs/querying/functions/ 与
//     https://prometheus.io/docs/practices/histograms
package main

import (
	"fmt"
	"math"
)

// repairMonotonic 官方单调修复: 先忽略相对差 < 1e-12 的微小下降, 再把非单调桶抬升为前桶值
func repairMonotonic(counts []float64) []float64 {
	fixed := append([]float64(nil), counts...)
	for i := 1; i < len(fixed); i++ {
		if fixed[i] < fixed[i-1] {
			fixed[i] = fixed[i-1] // 浮点噪声(<1e-12)与真实非单调都抬升
		}
	}
	return fixed
}

// histogramQuantile Prometheus 语义: 累积桶 + 桶内线性插值 + 官方边界规则
// les 按升序, 最高桶为 +Inf; counts 为累积计数。
func histogramQuantile(phi float64, les, countsIn []float64) float64 {
	if math.IsNaN(phi) {
		return math.NaN()
	}
	if phi < 0 {
		return math.Inf(-1)
	}
	if phi > 1 {
		return math.Inf(1)
	}
	if len(les) < 2 || !math.IsInf(les[len(les)-1], 1) {
		return math.NaN() // 桶数 <2 或缺 +Inf
	}
	counts := repairMonotonic(countsIn)

	rank := phi * counts[len(counts)-1]
	for i, c := range counts {
		if c < rank {
			continue // 还没容纳 rank
		}
		if math.IsInf(les[i], 1) {
			return les[i-1] // 最高桶 -> 次高桶上界(不外插)
		}
		var lower, prev float64
		if i == 0 {
			if les[0] > 0 { // 最低桶下界假设为 0
				lower = 0
			} else {
				lower = les[0]
			}
			prev = 0
		} else {
			lower, prev = les[i-1], counts[i-1]
		}
		if c == prev {
			return lower // 空桶防除零
		}
		return lower + (les[i]-lower)*(rank-prev)/(c-prev)
	}
	return math.NaN()
}

func approx(a, b float64) bool {
	return math.Abs(a-b) < 1e-9*(1+math.Abs(a)+math.Abs(b))
}

func main() {
	fmt.Println("== demo 1: official error-analysis example (220ms spike) ==")
	les := []float64{0.1, 0.2, 0.3, 0.45, math.Inf(1)}
	counts := []float64{0, 0, 100, 100, 100}
	p95 := histogramQuantile(0.95, les, counts)
	fmt.Println("    buckets le={0.1,0.2,0.3,0.45,+Inf}, 100 obs in (0.2,0.3]")
	fmt.Printf("    p95 = %.3fs (est) vs true 0.220s -> err %.0fms\n",
		p95, math.Abs(p95-0.22)*1000)
	if !approx(p95, 0.295) {
		panic("must match doc: 0.2 + 0.1*0.95")
	}

	fmt.Println("\n== demo 2: spike shifts +100ms -> boundary discontinuity ==")
	counts2 := []float64{0, 0, 0, 100, 100}
	p95b := histogramQuantile(0.95, les, counts2)
	fmt.Printf("    p95 = %.3fs (true 0.320s, err %.0fms)\n", p95b, math.Abs(p95b-0.32)*1000)
	if !approx(p95b, 0.3+0.15*0.95) {
		panic("interp mismatch")
	}
	fmt.Printf("    estimate jumped %.3f -> %.3f though true value only +100ms\n", p95, p95b)

	fmt.Println("\n== demo 3: full histogram p50/p90/p95/p99 ==")
	les3 := []float64{0.05, 0.1, 0.2, 0.5, 1.0, math.Inf(1)}
	counts3 := []float64{24054, 33444, 100392, 129389, 133988, 144320}
	for _, phi := range []float64{0.5, 0.9, 0.95, 0.99} {
		fmt.Printf("    p%02.0f = %.3fs\n", phi*100, histogramQuantile(phi, les3, counts3))
	}
	if !approx(histogramQuantile(0.5, les3, counts3),
		0.1+0.1*(0.5*144320-33444)/(100392-33444)) {
		panic("p50 mismatch")
	}
	fmt.Printf("    min-est(phi=0) = %.3fs, max-est(phi=1) = %.3fs\n",
		histogramQuantile(0, les3, counts3), histogramQuantile(1, les3, counts3))

	fmt.Println("\n== demo 4: boundary rules ==")
	if !math.IsNaN(histogramQuantile(0.5, []float64{0.1}, []float64{10})) {
		panic("expected NaN for <2 buckets")
	}
	if !math.IsNaN(histogramQuantile(0.5, []float64{0.1, 0.3}, []float64{5, 10})) {
		panic("expected NaN for missing +Inf")
	}
	if !math.IsInf(histogramQuantile(-0.1, les3, counts3), -1) ||
		!math.IsInf(histogramQuantile(1.1, les3, counts3), 1) ||
		!math.IsNaN(histogramQuantile(math.NaN(), les3, counts3)) {
		panic("phi boundary rules wrong")
	}
	fmt.Println("    <2 buckets / missing +Inf -> NaN; phi out of range -> +/-Inf; NaN phi -> NaN")
	hi := histogramQuantile(0.999999, les3, counts3)
	if !approx(hi, 1.0) {
		panic("top-bucket rule wrong")
	}
	fmt.Printf("    quantile in top bucket -> second-highest bound = %.3f\n", hi)

	fmt.Println("\n== demo 5: monotonicity repair ==")
	bad := []float64{10, 20, 15, 30, 30}
	fixed := repairMonotonic(bad)
	want := []float64{10, 20, 20, 30, 30}
	for i := range want {
		if fixed[i] != want[i] {
			panic("repair wrong")
		}
	}
	fmt.Println("    {10,20,15,30,30} -> {10,20,20,30,30} (non-monotonic bucket raised)")
	tiny := repairMonotonic([]float64{1.0, 1.0 - 1e-16})
	if tiny[1] != 1.0 {
		panic("sub-1e-12 dip should be absorbed")
	}
	fmt.Println("    sub-1e-12 relative dip treated as float noise")

	fmt.Println("\n== demo 6: summary quantiles are NOT aggregatable ==")
	instA, instB := 0.100, 0.300
	fmt.Printf("    avg(p95_a=%.3f, p95_b=%.3f) = %.3fs (statistically meaningless, 'BAD!')\n",
		instA, instB, (instA+instB)/2)
	merged := histogramQuantile(0.95,
		[]float64{0.1, 0.3, math.Inf(1)}, []float64{1, 2, 2})
	if !approx(merged, 0.3) {
		panic("merged-bucket p95 wrong")
	}
	fmt.Printf("    merged-bucket p95 = %.3fs (correct, 'GOOD' per docs)\n", merged)

	fmt.Println("\nALL CHECKS PASSED")
}
