// 多重比较校正的模拟与自检:一次跑 m 个 benchmark 时 FWER / FDR / 功效的实测值。
//
// 结论要点(与 Python 版一致):
//  1. 全局零效应下 V ≡ R,故 FDR **等于** FWER —— "m₀=m 时 BH 并不比 Bonferroni 松"。
//  2. 只有存在真效应时 FDR 与 FWER 才分道扬镳:BH 用更高的 FWER 换更高的功效。
//  3. benchstat 自己不做任何校正,family 的定义完全交给使用者。
package main

import (
	"fmt"
	"math"
	"os"
)

// countReject 统计被拒的个数。
func countReject(adj []float64) int {
	c := 0
	for _, p := range adj {
		if p <= alphaQ {
			c++
		}
	}
	return c
}

// simulateFamily 生成 m 个 p 值,前 mTrue 个来自真效应(非中心参数 d·√(n/2))。
func simulateFamily(m, mTrue, n int, effect float64, r *rng) []float64 {
	ncp := effect * math.Sqrt(float64(n)/2.0)
	ps := make([]float64, m)
	for i := 0; i < m; i++ {
		mu := 0.0
		if i < mTrue {
			mu = ncp
		}
		z := r.gaussian(mu, 1.0)
		ps[i] = math.Min(1.0, 2.0*normSF(math.Abs(z)))
	}
	return ps
}

type adjFn func([]float64) []float64

// evaluate 返回 (FWER, FDR, 平均真阳性比例)。
func evaluate(fn adjFn, m, mTrue, n int, effect float64, trials int, seed uint64) (float64, float64, float64) {
	r := newRNG(seed)
	fwer, fdp, tp := 0, 0.0, 0
	for t := 0; t < trials; t++ {
		ps := simulateFamily(m, mTrue, n, effect, r)
		rej := fn(ps)
		v, cnt, hit := 0, 0, 0
		for i := 0; i < m; i++ {
			if rej[i] <= alphaQ {
				cnt++
				if i >= mTrue {
					v++
				} else {
					hit++
				}
			}
		}
		if v > 0 {
			fwer++
		}
		rDen := cnt
		if rDen < 1 {
			rDen = 1 // FDR 定义里的 R∨1 = max(R,1)
		}
		fdp += float64(v) / float64(rDen)
		tp += hit
	}
	den := trials * mTrue
	if den == 0 {
		den = 1
	}
	return float64(fwer) / float64(trials), fdp / float64(trials), float64(tp) / float64(den)
}

// maxRejectedP 返回被拒假设中最大的原始 p 值(即有效阈值)。
func maxRejectedP(ps, adj []float64) float64 {
	best := 0.0
	for i := range ps {
		if adj[i] <= alphaQ && ps[i] > best {
			best = ps[i]
		}
	}
	return best
}

var failures int

func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s\n", label)
		return
	}
	fmt.Printf("  FAIL %s -- %s\n", label, detail)
	failures++
}

func main() {
	m := 50

	// 1) BY 的惩罚因子
	hm := harmonic(m)
	check("H_50 ≈ 4.499205338", math.Abs(hm-4.499205338329425) < 1e-12, fmt.Sprintf("%.12f", hm))
	check("H_m ≈ ln m + γ", math.Abs(hm-(math.Log(float64(m))+0.5772156649)) < 0.02, "")

	// 2) Šidák 略宽于 Bonferroni
	ab := alphaQ / float64(m)
	as := sidakAlpha(alphaQ, m)
	check("α/m < Šidák α_c < α", ab < as && as < alphaQ, fmt.Sprintf("%.6f vs %.6f", ab, as))
	check("Šidák α_c ≈ 0.0010248", math.Abs(as-0.0010248) < 1e-6, fmt.Sprintf("%.7f", as))

	// 3) Holm ⊇ Bonferroni
	r0 := newRNG(1)
	ok := true
	for t := 0; t < 300; t++ {
		ps := make([]float64, 12)
		for i := range ps {
			ps[i] = r0.uniform()
		}
		rb, rh := bonferroniAdjusted(ps), holmAdjusted(ps)
		for i := range ps {
			if rb[i] <= alphaQ && rh[i] > alphaQ {
				ok = false
			}
		}
	}
	check("Holm ⊇ Bonferroni(300 组随机向量)", ok, "")

	// 4) 全局零效应:V ≡ R,故 FDR == FWER
	idFn := func(ps []float64) []float64 { return ps }
	fNone, dNone, _ := evaluate(idFn, m, 0, 16, 0.0, 3000, 20260919)
	fBonf, _, _ := evaluate(bonferroniAdjusted, m, 0, 16, 0.0, 3000, 20260919)
	fHolm, _, _ := evaluate(holmAdjusted, m, 0, 16, 0.0, 3000, 20260919)
	fBH, dBH, _ := evaluate(bhAdjusted, m, 0, 16, 0.0, 3000, 20260919)
	expect := 1 - math.Pow(0.95, float64(m))
	check("未校正 FWER ≈ 1-0.95^m", math.Abs(fNone-expect) < 0.05, fmt.Sprintf("%.3f", fNone))
	check("全局零效应下 FDR == FWER", math.Abs(dNone-fNone) < 0.02,
		fmt.Sprintf("%.3f vs %.3f", dNone, fNone))
	check("Bonferroni/Holm/BH 的 FWER ≤ 0.06", fBonf <= 0.06 && fHolm <= 0.06 && fBH <= 0.06,
		fmt.Sprintf("%.3f/%.3f/%.3f", fBonf, fHolm, fBH))

	// 5) benchstat 那句话:期望 5% 的 benchmark 报显著
	r1 := newRNG(77)
	total := 0
	for t := 0; t < 3000; t++ {
		for _, p := range simulateFamily(m, 0, 16, 0.0, r1) {
			if p < alphaQ {
				total++
			}
		}
	}
	meanCnt := float64(total) / 3000.0
	check("平均报出 ≈2.5 个显著", math.Abs(meanCnt-2.5) < 0.3, fmt.Sprintf("%.2f", meanCnt))

	// 6) 有真效应时 FWER 与 FDR 才分道扬镳
	mTrue, eff := 10, 1.0
	fBH2, dBH2, pwBH := evaluate(bhAdjusted, m, mTrue, 16, eff, 3000, 555)
	fBo2, _, pwBo := evaluate(bonferroniAdjusted, m, mTrue, 16, eff, 3000, 555)
	_, dBY2, pwBY := evaluate(byAdjusted, m, mTrue, 16, eff, 3000, 555)
	check("BH 守住 FDR ≤ 0.05", dBH2 <= alphaQ+0.02, fmt.Sprintf("%.3f", dBH2))
	check("BH 的 FWER 明显高于 Bonferroni", fBH2 > fBo2+0.05,
		fmt.Sprintf("%.3f vs %.3f", fBH2, fBo2))
	check("BH 功效高于 Bonferroni", pwBH > pwBo, fmt.Sprintf("%.3f vs %.3f", pwBH, pwBo))
	check("BY 比 BH 更保守且 FDR 不更差", pwBY <= pwBH+1e-12 && dBY2 <= dBH2+1e-12,
		fmt.Sprintf("%.3f vs %.3f", pwBY, pwBH))

	// 7) BH 的有效阈值随真效应数量自适应
	r3 := newRNG(3)
	psDense := simulateFamily(m, mTrue, 16, eff, r3)
	r3b := newRNG(3)
	psSparse := simulateFamily(m, 1, 16, eff, r3b)
	check("真效应多时 BH 拒绝更多",
		countReject(bhAdjusted(psDense)) > countReject(bhAdjusted(psSparse)), "")
	check("Bonferroni 的判据恒为 p ≤ α/m",
		maxRejectedP(psDense, bonferroniAdjusted(psDense)) <= ab+1e-15 &&
			maxRejectedP(psSparse, bonferroniAdjusted(psSparse)) <= ab+1e-15, "")
	check("BH 的有效阈值宽于 α/m", maxRejectedP(psDense, bhAdjusted(psDense)) > ab, "")

	// 8) BY = BH × H_m
	r9 := newRNG(9)
	ps := simulateFamily(m, mTrue, 16, eff, r9)
	aBH, aBY := bhAdjusted(ps), byAdjusted(ps)
	okBy := true
	for i := range ps {
		if math.Abs(aBY[i]-math.Min(1.0, aBH[i]*hm)) > 1e-12 {
			okBy = false
		}
	}
	check("BY 校正 p = BH 校正 p × H_m", okBy, "")

	// 9) 未校正假阳性数 ≈ 0.05·m
	r31 := newRNG(31)
	ok9 := true
	for _, mm := range []int{10, 50, 200} {
		tot := 0
		for t := 0; t < 500; t++ {
			for _, p := range simulateFamily(mm, 0, 16, 0.0, r31) {
				if p < alphaQ {
					tot++
				}
			}
		}
		if math.Abs(float64(tot)/500.0-alphaQ*float64(mm)) > 0.6 {
			ok9 = false
		}
	}
	check("假阳性数 ≈ 0.05·m(m=10/50/200)", ok9, "")

	fmt.Printf("      全零效应 m=50:未校正 FWER=%.3f / BH FDR=%.3f\n", fNone, dBH)
	fmt.Printf("      10 个真效应:BH FDR=%.3f FWER=%.3f 功效=%.3f | Bonferroni 功效=%.3f\n",
		dBH2, fBH2, pwBH, pwBo)
	fmt.Printf("      BY 的代价系数 H_50=%.3f\n", hm)

	if failures > 0 {
		fmt.Printf("\n%d 项失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\nmultitest(go): 全部自检通过")
}
