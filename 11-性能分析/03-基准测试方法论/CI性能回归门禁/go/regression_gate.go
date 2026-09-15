// CI 性能回归门禁(Go 版) —— 自检; 核心判定在 gate.go。
//
// 断言覆盖: 纯噪声对照(朴素差分 vs step fit)、真实阶跃定位、抗单点尖峰、
// 双判据分类、CoV 门禁与噪声地板、多重比较校正确认。
package main

import (
	"fmt"
	"math"
	"math/rand"
)

func seriesWithStep(rng *rand.Rand, n int, base, noise float64, stepAt int, step float64) []float64 {
	out := make([]float64, n)
	for i := range out {
		f := 1.0
		if i >= stepAt {
			f = 1 + step
		}
		out[i] = base*f + rng.NormFloat64()*noise
	}
	return out
}

func gauss(rng *rand.Rand, m, sd float64, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = m + rng.NormFloat64()*sd
	}
	return out
}

func main() {
	rng := rand.New(rand.NewSource(20260915))

	// 1) 临界值表
	if math.Abs(tCritical(10)-2.228) > 1e-3 || math.Abs(tCritical(30)-2.042) > 1e-3 ||
		tCritical(100) != 1.96 {
		panic("t table broken")
	}
	fmt.Printf("[临界值] df=10 -> %.3f, df=30 -> %.3f, df=100 -> %.2f\n",
		tCritical(10), tCritical(30), tCritical(100))

	// 2) 纯噪声对照: 朴素差分满是假阳性, step fit 干净
	noiseOnly := seriesWithStep(rng, 60, 100, 6.0, 0, 0)
	naiveHits := 0
	for _, d := range naiveDelta(noiseOnly) {
		if math.Abs(d) >= magThreshold {
			naiveHits++
		}
	}
	steps := stepFit(noiseOnly, stepWidth, stepThreshold, stepZmin)
	if naiveHits == 0 || len(steps) != 0 {
		panic("noise comparison failed")
	}
	fmt.Printf("[对照] 纯噪声 60 个构建: 朴素差分 %d/%d 次 >=10%%; step fit 报出 %d 个阶跃\n",
		naiveHits, len(naiveDelta(noiseOnly)), len(steps))

	// 3) 真实阶跃定位
	found := stepFit(seriesWithStep(rng, 60, 100, 6.0, 35, 0.30), stepWidth, stepThreshold, stepZmin)
	if len(found) != 1 || math.Abs(float64(found[0].Index-35)) > 1 {
		panic(fmt.Sprintf("step not located: %v", found))
	}
	fmt.Printf("[检出] 第 35 个构建注入 +30%% 阶跃 -> index=%d, delta=%+.1f%%, z=%.1f\n",
		found[0].Index, found[0].Delta*100, found[0].Z)

	// 4) 单点尖峰不算回归
	spike := seriesWithStep(rng, 60, 100, 6.0, 0, 0)
	spike[30] = 180
	if got := stepFit(spike, stepWidth, stepThreshold, stepZmin); len(got) != 0 {
		panic("spike treated as regression")
	}
	fmt.Println("[抗尖峰] 单个构建抬高 80%: step fit 报出 0 个阶跃(正确忽略)")

	// 5) 双判据
	best := gauss(rng, 100, 0.05, 30)
	vNoisy := welchVerdict(best, gauss(rng, 120, 60.0, 30), magThreshold, 0.05)
	vStable := welchVerdict(best, gauss(rng, 120, 0.05, 30), magThreshold, 0.05)
	if vNoisy.Label == "REGRESSION" || vStable.Label != "REGRESSION" {
		panic(fmt.Sprintf("dual criteria broken: %v %v", vNoisy, vStable))
	}
	fmt.Printf("[双判据] +20%% 但 σ=60 -> %s(t=%.2f, 临界值 %.2f); +20%% 且 σ=0.05 -> %s(t=%.1f)\n",
		vNoisy.Label, vNoisy.T, tCritical(vNoisy.Df), vStable.Label, vStable.T)

	// 6) 幅度不足 -> acceptable; 变快 -> improvement
	if v := welchVerdict(best, gauss(rng, 100.4, 0.05, 30), magThreshold, 0.05); v.Label != "acceptable_change" {
		panic("acceptable change misclassified")
	}
	vFast := welchVerdict(best, gauss(rng, 80, 0.05, 30), magThreshold, 0.05)
	if vFast.Label != "improvement" {
		panic("improvement misclassified")
	}
	fmt.Printf("[分类] +0.4%% -> acceptable_change; %+.1f%% -> improvement(改进也要告警)\n", vFast.Delta*100)

	// 7) CoV 前置门禁 + 噪声地板
	stableReps := gauss(rng, 100, 0.5, 20)
	jumpyReps := gauss(rng, 100, 6.0, 20)
	okS, cvS := covGate(stableReps, 0.02)
	okJ, cvJ := covGate(jumpyReps, 0.02)
	if !okS || okJ {
		panic("cov gate broken")
	}
	if noiseFloor(stableReps, 3, magThreshold) != magThreshold ||
		noiseFloor(jumpyReps, 3, magThreshold) <= magThreshold {
		panic("noise floor broken")
	}
	fmt.Printf("[CoV] 稳定 CoV=%.3f%%(通过), 抖动 CoV=%.3f%%(先降噪); 噪声地板 %.1f%% vs %.1f%%\n",
		cvS*100, cvJ*100, noiseFloor(stableReps, 3, magThreshold)*100,
		noiseFloor(jumpyReps, 3, magThreshold)*100)

	// 8) 多重比较: 200 个无差异 benchmark
	m := 200
	pvals := make([]float64, m)
	for i := 0; i < m; i++ {
		a := gauss(rng, 100, 1.0, 20)
		b := gauss(rng, 100, 1.0, 20)
		t := (mean(b) - mean(a)) / math.Sqrt(variance(a)/20+variance(b)/20)
		phi := 0.5 * (1 + math.Erf(t/math.Sqrt2))
		pvals[i] = 2 * math.Min(phi, 1-phi)
	}
	raw := 0
	for _, p := range pvals {
		if p < alpha {
			raw++
		}
	}
	bonf, bh := countTrue(bonferroni(pvals, alpha)), countTrue(benjaminiHochberg(pvals, alpha))
	if raw < 2 || raw > 30 || bonf > bh || bh > raw {
		panic(fmt.Sprintf("multiple comparison broken: raw=%d bonf=%d bh=%d", raw, bonf, bh))
	}
	fmt.Printf("[多重比较] %d 个无差异 benchmark: 未校正 %d 个假阳性, Bonferroni %d 个, BH %d 个\n",
		m, raw, bonf, bh)

	fmt.Println("\nregression_gate: 全部自检通过")
}
