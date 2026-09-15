// benchstat 核心统计的自检(Go 版); 统计实现在 benchstat_core.go。
//
// 锚点断言:
//   * 精确 U 分布的总计数 = C(n1+n2, n1), 取值范围 [0, n1*n2]
//   * 完全分离的两组: U1 = 0, p = 2/C(8,4) = 2/70
//   * 镜像性: U1 + U2 = n1*n2
//   * alpha 标定: 同分布两组互比, U 检验假阳性率接近 0.05
//   * geomean 比例语义: 8 个 benchmark 里一个变 2 倍 -> geomean = 2^(1/8)
//   * 中位数次序统计量 CI 在偏态分布上覆盖率不低于名义值
package main

import (
	"fmt"
	"math"
	"math/rand"
	"strings"
)

func synth(kind string, n int, rng *rand.Rand) []float64 {
	out := make([]float64, n)
	for i := range out {
		switch kind {
		case "normal":
			out[i] = rng.NormFloat64()*8 + 100
		case "lognormal":
			out[i] = math.Exp(rng.NormFloat64()*0.35 + 4.6)
		case "exponential":
			out[i] = rng.ExpFloat64() * 100
		}
	}
	return out
}

func main() {
	rng := rand.New(rand.NewSource(42))

	// 1) 精确 U 分布: 总计数 = 组合数, 取值范围 [0, n1*n2]
	counts := uDistributionExact(4, 4)
	var total int64
	for _, c := range counts {
		total += c
	}
	if total != binom(8, 4) {
		panic("exact distribution total mismatch")
	}
	fmt.Printf("[exact] C(8,4)=%d, U 取值范围 [%d,%d]\n", total, 0, 16)

	// 2) 完全分离: U1=0, p=2/70; 镜像 U1+U2=n1*n2
	a := []float64{1, 2, 3, 4}
	b := []float64{10, 20, 30, 40}
	u1, p, method := mannWhitneyU(a, b, alphaBW)
	if u1 != 0 || math.Abs(p-2.0/70.0) > 1e-12 {
		panic(fmt.Sprintf("separated samples: U1=%v p=%v", u1, p))
	}
	u1r, _, _ := mannWhitneyU(b, a)
	if u1r != 16 {
		panic("mirror U mismatch")
	}
	fmt.Printf("[U] 完全分离: U1=%.0f p=%.6f(%s), 镜像 U=%.0f\n", u1, p, method, u1r)

	// 3) alpha 标定: 同分布两组互比
	trials, hits := 400, 0
	for i := 0; i < trials; i++ {
		s1 := synth("lognormal", 8, rng)
		s2 := synth("lognormal", 8, rng)
		if _, pp, _ := mannWhitneyU(s1, s2, alphaBW); pp < alphaBW {
			hits++
		}
	}
	fp := float64(hits) / float64(trials)
	if fp < 0.01 || fp > 0.10 {
		panic(fmt.Sprintf("false positive rate out of band: %v", fp))
	}
	fmt.Printf("[alpha] %d 组同分布对比的假阳性率 %.3f(名义 0.05)\n", trials, fp)

	// 4) 真差异必须检出
	base := synth("normal", 20, rng)
	slow := make([]float64, len(base))
	for i, v := range base {
		slow[i] = v * 1.25
	}
	if _, pd, _ := mannWhitneyU(base, slow, alphaBW); !(pd < 1e-4) {
		panic("true difference not detected")
	}

	// 5) geomean 比例语义
	before := []float64{1, 1, 1, 1, 1, 1, 1, 1}
	after := []float64{2, 1, 1, 1, 1, 1, 1, 1}
	got, want := geomeanRatio(after, before), math.Pow(2, 1.0/8)
	if math.Abs(got-want) > 1e-12 {
		panic("geomean ratio semantics broken")
	}
	fmt.Printf("[geomean] 1/8 个 benchmark 变 2 倍 -> geomean 比值 %.6f = 2^(1/8) %.6f\n", got, want)

	// 6) 中位数 CI 覆盖率(指数分布, n=21)
	covHits := 0
	for i := 0; i < 120; i++ {
		s := synth("exponential", 21, rng)
		lo, hi, _ := medianCIOrderStatistic(s, alphaBW)
		if lo <= math.Log(2)*100 && math.Log(2)*100 <= hi {
			covHits++
		}
	}
	cov := float64(covHits) / 120
	if cov < 0.85 {
		panic(fmt.Sprintf("median CI coverage too low: %v", cov))
	}
	fmt.Printf("[CI] 次序统计量法覆盖率 %.3f(指数分布, n=21, 保守)\n", cov)

	// 7) 表格: 无差异显示 '~'
	nb := synth("normal", 10, rng)
	ns := synth("normal", 10, rng)
	lines := benchstatTable([]BenchRow{
		{"Bench-8", nb, nb},
		{"Noise-8", nb, ns},
	}, alphaBW)
	hasTilde := false
	for _, l := range lines {
		fmt.Println(l)
		if strings.Contains(l, "~ (p=") {
			hasTilde = true
		}
	}
	if !hasTilde {
		panic("benchstatTable should mark non-significant rows with '~'")
	}

	fmt.Println("\nbenchstat_lite: 全部自检通过")
}
