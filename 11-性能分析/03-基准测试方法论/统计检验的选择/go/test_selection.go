// 统计检验的选择(Go 版) —— 场景实证与断言; 统计原语在 stats_core.go。
//
// 场景(与 python/test_selection.py 一一对应):
//   A 整组平移      -> t 与 U 都显著
//   B 局部抬高 8/40 -> t 显著(均值被拉动)、U 不显著(秩几乎不变)
//   C 重尾          -> 散布次序 mean >> winsorized > trimmed > median
//   D 效率          -> U 的功效逼近 t 的 3/π(渐近相对效率), 有限样本会偏离
//   E 覆盖率        -> 强偏态下均值 t 区间失真, 中位数次序统计量区间保持有效
//   F Cauchy        -> 样本量增加并不改善均值估计
package main

import (
	"fmt"
	"math"
	"math/rand"
)

func stdev(v []float64) float64 { return math.Sqrt(variance(v)) }

// deterministicGroups: B 是等间距序列, A 与 B 相同但每 5 个样本被抬高到 140。
func deterministicGroups(n int) ([]float64, []float64) {
	b := make([]float64, n)
	for i := range b {
		b[i] = 100 + (float64(i)-float64(n-1)/2)*0.25
	}
	a := append([]float64(nil), b...)
	for i := 0; i < n; i += 5 {
		a[i] = 140
	}
	return a, b
}

func synthLognormal(rng *rand.Rand, n int, sigma float64) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = math.Exp(rng.NormFloat64() * sigma)
	}
	return out
}

func main() {
	// 1) t 分布锚点
	tc := tCritical(10, alpha)
	if !closeTo(tc, 2.228139, 1e-5) || !closeTo(tTwoSidedP(2.228139, 10), 0.05, 1e-6) {
		panic("t distribution anchors broken")
	}
	fmt.Printf("[锚点] t 分布 df=10 双尾临界值 %.6f(教科书 2.228139)\n", tc)

	// 2) NIST 7.3.1 的 Welch 算例
	m1, sd1, n1 := 36.0909, 4.9082, 11.0
	m2, sd2, n2 := 32.2222, 2.5386, 9.0
	v1, v2 := sd1*sd1/n1, sd2*sd2/n2
	t := (m1 - m2) / math.Sqrt(v1+v2)
	df := (v1 + v2) * (v1 + v2) / (v1*v1/(n1-1) + v2*v2/(n2-1))
	if !closeTo(t, 2.2694, 2e-3) || df <= 15.4 || df >= 15.7 {
		panic(fmt.Sprintf("NIST example mismatch: t=%v df=%v", t, df))
	}
	fmt.Printf("[锚点] NIST 7.3.1 算例: t=%.4f(手册 2.2694), ν=%.2f(手册 15.5)\n", t, df)

	// 3) 场景 A / B
	a, b := deterministicGroups(40)
	shifted := make([]float64, len(b))
	for i, v := range b {
		shifted[i] = v + 3
	}
	_, _, pA := welchT(b, shifted)
	_, pUA := mannWhitneyP(b, shifted)
	if pA >= 1e-3 || pUA >= 1e-3 {
		panic("scenario A: both tests should be significant")
	}
	_, _, ptB := welchT(a, b)
	_, puB := mannWhitneyP(a, b)
	if ptB >= 0.05 || puB <= 0.05 {
		panic(fmt.Sprintf("scenario B mismatch: t=%v U=%v", ptB, puB))
	}
	fmt.Printf("[场景A 整组平移] t p=%.2e U p=%.2e(都显著)\n", pA, pUA)
	fmt.Printf("[场景B 局部抬高] t p=%.4f(显著) U p=%.4f(不显著) -> 同一批数据两种结论\n", ptB, puB)

	// 4) 场景 C: 位置估计量稳定性
	rng := rand.New(rand.NewSource(7))
	estimators := map[string]func([]float64) float64{
		"mean": mean, "median": median,
		"trimmed5%":    func(v []float64) float64 { return trimmedMean(v, 0.05) },
		"winsorized5%": func(v []float64) float64 { return winsorizedMean(v, 0.05) },
	}
	spread := map[string]float64{}
	for name, fn := range estimators {
		vals := make([]float64, 300)
		for i := range vals {
			s := synthLognormal(rng, 21, 1.5)
			s[rng.Intn(21)] = s[0] * 1000 // 单个 1000 倍离群点
			vals[i] = fn(s)
		}
		spread[name] = stdev(vals)
	}
	if !(spread["mean"] > 3*spread["median"] &&
		spread["trimmed5%"] < spread["mean"] && spread["winsorized5%"] < spread["mean"]) {
		panic(fmt.Sprintf("scenario C mismatch: %v", spread))
	}
	fmt.Printf("[场景C 重尾] 散布 mean=%.3f winsorized5%%=%.3f trimmed5%%=%.3f median=%.3f\n",
		spread["mean"], spread["winsorized5%"], spread["trimmed5%"], spread["median"])

	// 5) 场景 D: U 相对 t 的效率(ARE 是渐近量, 有限样本功效比会偏离)
	for _, cfg := range []struct {
		n      int
		effect float64
		wantLo float64
	}{{10, 0.6, 0.85}, {150, 0.25, 0.93}} {
		hitT, hitU, trials := 0, 0, 2000
		for i := 0; i < trials; i++ {
			s1 := make([]float64, cfg.n)
			s2 := make([]float64, cfg.n)
			for j := 0; j < cfg.n; j++ {
				s1[j] = rng.NormFloat64()
				s2[j] = rng.NormFloat64() + cfg.effect
			}
			if _, _, p := welchT(s1, s2); p < alpha {
				hitT++
			}
			if _, p := mannWhitneyP(s1, s2); p < alpha {
				hitU++
			}
		}
		ratio := float64(hitU) / float64(hitT)
		if ratio < cfg.wantLo || ratio > 1.02 {
			panic(fmt.Sprintf("scenario D mismatch n=%d ratio=%v", cfg.n, ratio))
		}
		fmt.Printf("[场景D 效率] n=%-4d 功效 t=%.3f U=%.3f -> 比值 %.3f\n",
			cfg.n, float64(hitT)/float64(trials), float64(hitU)/float64(trials), ratio)
	}
	fmt.Printf("     ARE 理论 3/π ≈ %.3f: 有限样本偏离, 大样本才收敛\n", 3/math.Pi)

	// 6) 场景 E: 覆盖率
	covT, covMed, trials := 0, 0, 600
	trueMean, trueMed := math.Exp(1.5*1.5/2), 1.0
	for i := 0; i < trials; i++ {
		s := synthLognormal(rng, 10, 1.5)
		if lo, hi := meanCIT(s, alpha); lo <= trueMean && trueMean <= hi {
			covT++
		}
		if mlo, mhi := medianCIOrderStatistic(s, alpha); mlo <= trueMed && trueMed <= mhi {
			covMed++
		}
	}
	ct, cm := float64(covT)/float64(trials), float64(covMed)/float64(trials)
	if ct >= 0.92 || cm < 0.90 {
		panic(fmt.Sprintf("scenario E mismatch: t=%v med=%v", ct, cm))
	}
	fmt.Printf("[场景E 覆盖率] 对数正态(σ=1.5,n=10): 均值 t 区间 %.3f(名义 0.95), 中位数区间 %.3f\n", ct, cm)

	// 7) 场景 F: Cauchy
	spreads := []float64{}
	for _, n := range []int{10, 1000} {
		vals := make([]float64, 120)
		for i := range vals {
			s := make([]float64, n)
			for j := range s {
				s[j] = rng.NormFloat64() / rng.NormFloat64()
			}
			vals[i] = mean(s)
		}
		spreads = append(spreads, stdev(vals))
	}
	meds := make([]float64, 120)
	for i := range meds {
		s := make([]float64, 1000)
		for j := range s {
			s[j] = rng.NormFloat64() / rng.NormFloat64()
		}
		meds[i] = median(s)
	}
	if !(spreads[1] > 0.5*spreads[0]) || stdev(meds) >= 0.3*spreads[1] {
		panic("scenario F mismatch")
	}
	fmt.Printf("[场景F Cauchy] 均值散布 n=10 -> %.2f, n=1000 -> %.2f(不下降); 中位数(n=1000) -> %.2f\n",
		spreads[0], spreads[1], stdev(meds))

	fmt.Println("\ntest_selection: 全部自检通过")
}
