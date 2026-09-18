// 变异系数(CoV)、噪声地板与降噪的 Go 镜像实现。
//
// 与 Python 版同源,但**全程确定性**:凡是需要随机样本的地方都换成解析式或数值积分,
// 因此不依赖 math/rand 的具体发生器实现,任何 Go 版本跑出的数字都一致。
// E[max(Z₁..Z₅)] 用 Simpson 积分 ∫ x·5·Φ(x)⁴·φ(x) dx 现算,而不是抄一个常数。
package main

import (
	"fmt"
	"math"
	"os"
)

const (
	alpha = 0.05
	power = 0.80
)

// normCDF 标准正态累积分布函数。
func normCDF(z float64) float64 { return 0.5 * math.Erfc(-z/math.Sqrt2) }

// normPDF 标准正态密度函数。
func normPDF(z float64) float64 { return math.Exp(-0.5*z*z) / math.Sqrt(2*math.Pi) }

// normPPF 标准正态分位数(二分)。
func normPPF(p float64) float64 {
	lo, hi := -40.0, 40.0
	for i := 0; i < 200; i++ {
		mid := 0.5 * (lo + hi)
		if normCDF(mid) < p {
			lo = mid
		} else {
			hi = mid
		}
	}
	return 0.5 * (lo + hi)
}

// cov 变异系数:样本标准差 / 均值。
func cov(xs []float64) float64 {
	n := float64(len(xs))
	mean := 0.0
	for _, v := range xs {
		mean += v
	}
	mean /= n
	ss := 0.0
	for _, v := range xs {
		ss += (v - mean) * (v - mean)
	}
	return math.Sqrt(ss/(n-1)) / mean
}

// mdeRelFromN 给定 CoV 与每侧样本量,能检出的最小相对回归。
func mdeRelFromN(covValue float64, nPerGroup float64) float64 {
	return (normPPF(1-alpha/2) + normPPF(power)) * covValue * math.Sqrt(2.0/nPerGroup)
}

// nFromCovMde 给定 CoV 与目标 MDE,反解每侧需要的次数。
func nFromCovMde(covValue, mdeRel float64) int {
	k := normPPF(1-alpha/2) + normPPF(power)
	return int(math.Ceil(2.0 * k * k * (covValue/mdeRel)*(covValue/mdeRel)))
}

// sequentialABDelta 先跑 n 次 A 再跑 n 次 B 时,均值差里的漂移分量 = drift·n。
func sequentialABDelta(drift float64, n float64) float64 { return drift * n }

// interleavedABDelta A/B 交替时,均值差里的漂移分量 = drift·1。
func interleavedABDelta(drift float64, n float64) float64 { return drift }

// aaNoiseFloor A/A 噪声地板的解析值:CoV·√(2/n)·E|Z| = CoV·√(2/n)·√(2/π)。
func aaNoiseFloor(covValue float64, n float64) float64 {
	return covValue * math.Sqrt(2.0/n) * math.Sqrt(2.0/math.Pi)
}

// expectedMaxOfFive 用 Simpson 积分求 E[max of 5 iid N(0,1)]。
func expectedMaxOfFive() float64 {
	const lo, hi = -8.0, 8.0
	const steps = 16000
	h := (hi - lo) / float64(steps)
	f := func(x float64) float64 {
		phi := normCDF(x)
		return x * 5 * phi * phi * phi * phi * normPDF(x)
	}
	total := f(lo) + f(hi)
	for i := 1; i < steps; i++ {
		x := lo + h*float64(i)
		w := 2.0
		if i%2 == 1 {
			w = 4.0
		}
		total += w * f(x)
	}
	return total * h / 3.0
}

// trimBiasInSigma 每 5 个丢掉最大值后,均值的偏差(以 σ 为单位)。
// 推导:保留 0.8n 个,E[Σ_all]=nμ,E[Σ_dropped]=(n/5)(μ+E[max₅]σ)
// => E[trimmed mean] = μ − E[max₅]·σ/4 = μ − 0.29074σ
func trimBiasInSigma(eMax5 float64) float64 { return -eMax5 / 4.0 }

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
	// 1) CoV 无量纲
	base := make([]float64, 0, 200)
	for i := 0; i < 200; i++ {
		v := 100.0 + 4.0*math.Sin(float64(i)*1.7) + 2.0*math.Cos(float64(i)*0.3)
		base = append(base, v)
	}
	scaled := make([]float64, 0, len(base))
	for _, v := range base {
		scaled = append(scaled, v*7.3)
	}
	check("CoV 无量纲:缩放后不变", math.Abs(cov(base)-cov(scaled)) < 1e-12,
		fmt.Sprintf("%.6f vs %.6f", cov(base), cov(scaled)))

	// 2) 交错 vs 顺序:比值恰为 n
	for _, n := range []float64{5, 10, 20} {
		seq := sequentialABDelta(0.01, n)
		itl := interleavedABDelta(0.01, n)
		check(fmt.Sprintf("drift 放大倍数 = n(=%v)", n),
			math.Abs(seq/itl-n) < 1e-9, fmt.Sprintf("%.6f", seq/itl))
	}

	// 3) Apogee 口径核对:CoV=5% 时 10 次不够、16 次才够
	m10 := mdeRelFromN(0.05, 10)
	m16 := mdeRelFromN(0.05, 16)
	check("-count=10 -> MDE 6.26% > 5%", m10 > 0.05, fmt.Sprintf("%.4f", m10))
	check("-count=16 -> MDE 4.95% <= 5%", m16 <= 0.05, fmt.Sprintf("%.4f", m16))
	check("CoV=5%/MDE=5% -> 每侧 16 次", nFromCovMde(0.05, 0.05) == 16, "")

	// 4) 降噪与加样本可互换
	check("CoV 减半等价于样本量 ×4",
		math.Abs(mdeRelFromN(0.025, 10)-0.5*mdeRelFromN(0.05, 10)) < 1e-12, "")

	// 5) A/A 噪声地板
	floor20 := aaNoiseFloor(0.02, 20)
	check("A/A 地板 = CoV·√(2/n)·√(2/π)", math.Abs(floor20-0.005046) < 1e-6,
		fmt.Sprintf("%.6f", floor20))

	// 6) 剔除离群点的 O(σ) 偏差:先把 E[max of 5] 用积分算出来
	eMax5 := expectedMaxOfFive()
	check("E[max(Z₁..Z₅)] ≈ 1.1630", math.Abs(eMax5-1.162964) < 1e-4, fmt.Sprintf("%.6f", eMax5))
	bias := trimBiasInSigma(eMax5)
	check("剔除偏差 ≈ -0.2907σ 且为负", bias < 0 && math.Abs(bias+0.29074) < 1e-3,
		fmt.Sprintf("%.6f", bias))
	// O(σ) 偏差远大于 O(σ/√n) 的标准误 —— 加样本量救不了它
	se2000 := 1.0 / math.Sqrt(2000.0)
	check("剔除偏差 ≫ 均值的标准误(n=2000)", math.Abs(bias)/se2000 > 10.0,
		fmt.Sprintf("比值 %.2f", math.Abs(bias)/se2000))

	// 7) 不降噪的代价
	n60 := nFromCovMde(0.60, 0.05)
	check("CoV=60% 检出 5% 需每侧 >1000 次", n60 > 1000, fmt.Sprint(n60))

	fmt.Printf("      CoV=5%%: -count=10 -> MDE %.2f%%, -count=16 -> MDE %.2f%%\n", m10*100, m16*100)
	fmt.Printf("      A/A 地板(μ=100,σ=2,n=20): %.4f%%\n", floor20*100)
	fmt.Printf("      E[max of 5]=%.6f -> 剔除偏差 %.6fσ\n", eMax5, bias)
	fmt.Printf("      CoV=60%% 时检出 5%% 回归需要每侧 %d 次\n", n60)

	if failures > 0 {
		fmt.Printf("\n%d 项失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\ncov_noise(go): 全部自检通过")
}
