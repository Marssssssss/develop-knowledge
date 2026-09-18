// 最小可检测效应(MDE)与样本量估算的 Go 镜像实现。
//
// 与 Python 版逐函数对应;功效部分改用**解析式**而不是蒙特卡洛,避免依赖
// math/rand 的具体实现(不同 Go 版本的默认发生器序列不保证一致):
//   power = Φ(d·√(n/2) − z_{1−α/2}) + Φ(−d·√(n/2) − z_{1−α/2})
// 其中 d = δ/σ 是标准化效应,√(n/2) 来自两组均值差的标准误 σ√(2/n)。
//
// 权威口径:NIST/SEMATECH e-Handbook §7.2.2.2(Sample sizes required)与
// §1.3.5.3(Two-Sample t-Test for Equal Means,含 Welch-Satterthwaite 自由度)。
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

// normPPF 标准正态分位数,用二分求解(单调,100 次足够到 1e-14)。
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

// betacf 连分式展开(Lentz 算法)。
func betacf(a, b, x float64) float64 {
	const fpmin = 1e-300
	const eps = 3e-16
	qab, qap, qam := a+b, a+1.0, a-1.0
	c := 1.0
	d := 1.0 - qab*x/qap
	if math.Abs(d) < fpmin {
		d = fpmin
	}
	d = 1.0 / d
	h := d
	for m := 1; m <= 300; m++ {
		m2 := float64(2 * m)
		aa := float64(m) * (b - float64(m)) * x / ((qam+m2)*(a+m2))
		d = 1.0 + aa*d
		if math.Abs(d) < fpmin {
			d = fpmin
		}
		c = 1.0 + aa/c
		if math.Abs(c) < fpmin {
			c = fpmin
		}
		d = 1.0 / d
		h *= d * c
		aa = -(a + float64(m)) * (qab+float64(m)) * x / ((a+m2)*(qap+m2))
		d = 1.0 + aa*d
		if math.Abs(d) < fpmin {
			d = fpmin
		}
		c = 1.0 + aa/c
		if math.Abs(c) < fpmin {
			c = fpmin
		}
		d = 1.0 / d
		delta := d * c
		h *= delta
		if math.Abs(delta-1.0) < eps {
			break
		}
	}
	return h
}

// betai 正则化不完全 Beta 函数 I_x(a,b)。
func betai(a, b, x float64) float64 {
	if x <= 0 {
		return 0
	}
	if x >= 1 {
		return 1
	}
	lg1, _ := math.Lgamma(a + b)
	lg2, _ := math.Lgamma(a)
	lg3, _ := math.Lgamma(b)
	front := math.Exp(lg1 - lg2 - lg3 + a*math.Log(x) + b*math.Log1p(-x))
	if x < (a+1.0)/(a+b+2.0) {
		return front * betacf(a, b, x) / a
	}
	return 1.0 - math.Exp(lg1-lg2-lg3+b*math.Log1p(-x)+a*math.Log(x))*betacf(b, a, 1.0-x)/b
}

// tSFTwoSided 返回 P(|T_df| > |t|);df<=0 时返回 NaN(自由度无定义)。
func tSFTwoSided(t, df float64) float64 {
	if df <= 0 {
		return math.NaN()
	}
	x := df / (df + t*t)
	return betai(df/2.0, 0.5, x)
}

// tCDF t 分布的累积分布函数。
func tCDF(t, df float64) float64 {
	if df <= 0 {
		return math.NaN()
	}
	if t >= 0 {
		return 1.0 - 0.5*tSFTwoSided(t, df)
	}
	return 0.5 * tSFTwoSided(t, df)
}

// tPPF t 分位数:二分求解(NIST 要求"iterate",这里真的迭代)。
func tPPF(p, df float64) (float64, error) {
	if df <= 0 {
		return 0, fmt.Errorf("t 分布要求自由度 > 0(n=1 时 df=0,无定义)")
	}
	if p <= 0 || p >= 1 {
		return 0, fmt.Errorf("p 必须落在 (0,1)")
	}
	lo, hi := -1.0e4, 1.0e4
	for i := 0; i < 200; i++ {
		mid := 0.5 * (lo + hi)
		if tCDF(mid, df) < p {
			lo = mid
		} else {
			hi = mid
		}
	}
	return 0.5 * (lo + hi), nil
}

// nOneSampleZ NIST §7.2.2.2 的 σ 已知公式(原始形态,不取整)。
func nOneSampleZ(a, pw, sigmaOverDelta float64, twoSided bool) float64 {
	za := normPPF(1 - a/2)
	if !twoSided {
		za = normPPF(1 - a)
	}
	zb := normPPF(pw)
	return (za + zb) * (za + zb) * sigmaOverDelta * sigmaOverDelta
}

// nOneSampleTIterative σ 未知时的迭代解:先按正态估一版,再用 df=N-1 的 t 临界值代入。
func nOneSampleTIterative(a, pw, sigmaOverDelta float64, twoSided bool) int {
	cur := int(math.Ceil(nOneSampleZ(a, pw, sigmaOverDelta, twoSided)))
	if cur < 2 {
		cur = 2
	}
	for i := 0; i < 20; i++ {
		df := float64(cur - 1)
		ta, err1 := tPPF(1-a/2, df)
		if !twoSided {
			ta, err1 = tPPF(1-a, df)
		}
		tb, err2 := tPPF(pw, df)
		if err1 != nil || err2 != nil {
			return cur
		}
		next := int(math.Ceil((ta + tb) * (ta + tb) * sigmaOverDelta * sigmaOverDelta))
		if next < 2 {
			next = 2
		}
		if next == cur {
			return cur
		}
		cur = next
	}
	return cur
}

// nTwoSampleZ 等样本量两组的每侧 n:由非中心参数相等推出 n_two = 2·n_one。
func nTwoSampleZ(a, pw, sigmaOverDelta float64, twoSided bool) float64 {
	return 2.0 * nOneSampleZ(a, pw, sigmaOverDelta, twoSided)
}

// mdeRelFromN 给定每侧 n 与变异系数 CoV,反解能检出的最小相对回归。
func mdeRelFromN(a, pw, cov float64, nPerGroup float64) float64 {
	za := normPPF(1 - a/2)
	zb := normPPF(pw)
	return (za + zb) * cov * math.Sqrt(2.0/nPerGroup)
}

// nFromCovMde 给定 CoV 与目标 MDE,反解每侧需要几次 -count。
func nFromCovMde(a, pw, cov, mdeRel float64) int {
	za := normPPF(1 - a/2)
	zb := normPPF(pw)
	return int(math.Ceil(2.0 * (za + zb) * (za + zb) * (cov/mdeRel)*(cov/mdeRel)))
}

// analyticPowerTwoSample 两组 z 检验的解析功效(避免随机数发生器实现差异)。
func analyticPowerTwoSample(n int, deltaOverSigma, a float64) float64 {
	ncp := deltaOverSigma * math.Sqrt(float64(n)/2.0)
	zc := normPPF(1 - a/2)
	return normCDF(ncp-zc) + normCDF(-ncp-zc)
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
	// 1) NIST 单侧原例:α=0.05、β=0.10、δ=σ、σ 已知 -> 8.567 -> 9
	nistZ := nOneSampleZ(0.05, 0.90, 1.0, false)
	check("NIST 单侧原例 ≈8.56 且 ceil=9", nistZ > 8.55 && nistZ < 8.58 && int(math.Ceil(nistZ)) == 9,
		fmt.Sprintf("%.4f", nistZ))

	// 2) σ 未知:迭代到 N=11
	nistT := nOneSampleTIterative(0.05, 0.90, 1.0, false)
	ta, _ := tPPF(0.95, 8)
	tb, _ := tPPF(0.90, 8)
	sq := (ta + tb) * (ta + tb)
	check("NIST 迭代 -> N=11 且 df=8 时 (t+t)²≈10.6", nistT == 11 && sq > 10.55 && sq < 10.65,
		fmt.Sprintf("N=%d (t+t)^2=%.4f", nistT, sq))

	// 3) 单样本 -> 两组的 ×2 关系 + 解析功效
	n1 := nOneSampleZ(alpha, power, 1.0, true)
	n2 := nTwoSampleZ(alpha, power, 1.0, true)
	check("两组每侧 n 恰为单样本的 2 倍", math.Abs(n2-2*n1) < 1e-9, fmt.Sprintf("%.4f vs %.4f", n1, n2))
	check("两组 n 取整 = 16", int(math.Ceil(n2)) == 16, fmt.Sprintf("%.4f", n2))
	pw16 := analyticPowerTwoSample(16, 1.0, alpha)
	check("n=16 的解析功效 ≈0.807", math.Abs(pw16-0.8074) < 0.002, fmt.Sprintf("%.4f", pw16))

	// 4) MDE 与 n 互为逆运算(取整后 MDE 只会优于目标)
	cov, mde := 0.01, 0.005
	exact := 2.0 * math.Pow(normPPF(1-alpha/2)+normPPF(power), 2) * math.Pow(cov/mde, 2)
	nNeed := nFromCovMde(alpha, power, cov, mde)
	back := mdeRelFromN(alpha, power, cov, float64(nNeed))
	check("CoV=1%/MDE=0.5% -> 每侧 63 次", nNeed == 63, fmt.Sprint(nNeed))
	check("取整后反解 MDE ≤ 目标", back <= mde+1e-12, fmt.Sprintf("%.6f", back))
	check("未取整时精确闭合", math.Abs(mdeRelFromN(alpha, power, cov, exact)-mde) < 1e-9, "")

	// 5) 效应减半 -> 样本量 ×4
	ratio := nTwoSampleZ(alpha, power, 2.0, true) / nTwoSampleZ(alpha, power, 1.0, true)
	check("目标效应减半 -> 样本量 ×4", math.Abs(ratio-4.0) < 1e-9, fmt.Sprintf("%.4f", ratio))

	// 6) benchstat 的 10 / 20 次建议对应多大 MDE
	m10 := mdeRelFromN(alpha, power, 0.01, 10)
	m20 := mdeRelFromN(alpha, power, 0.01, 20)
	check("-count=10 -> MDE≈1.253%", math.Abs(m10-0.012533) < 1e-5, fmt.Sprintf("%.6f", m10))
	check("-count=20 -> MDE≈0.886%", math.Abs(m20-0.008862) < 1e-5, fmt.Sprintf("%.6f", m20))
	check("MDE(10)/MDE(20)=√2", math.Abs(m10/m20-math.Sqrt2) < 1e-9, "")

	// 7) MDE 与 CoV 成正比:-count=20 时系数恒为 0.886
	ok := true
	for _, c := range []float64{0.005, 0.01, 0.02, 0.05} {
		if math.Abs(mdeRelFromN(alpha, power, c, 20)/c-0.8862) > 1e-3 {
			ok = false
		}
	}
	check("-count=20 时 MDE ≈ 0.886×CoV", ok, "")

	// 8) n=1 的死穴:自由度 0
	_, err := tPPF(0.975, 0)
	check("df=0 时 t 分位数无定义", err != nil, "应当报错")

	fmt.Printf("      -count=10 的解析功效(d=1): %.3f\n", analyticPowerTwoSample(10, 1.0, alpha))
	fmt.Printf("      -count=20 的解析功效(d=1): %.3f\n", analyticPowerTwoSample(20, 1.0, alpha))

	if failures > 0 {
		fmt.Printf("\n%d 项失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\nmde_sample(go): 全部自检通过")
}
