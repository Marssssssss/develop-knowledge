// Package main 的构件层:幂变换、剖面对数似然、百分位口径与分位数变换。
//
// 公式与常量对齐 scipy.stats.boxcox/_llf/normmax 与 sklearn.preprocessing
// (BOUNDS_THRESHOLD、ppf 夹取范围、n_quantiles 封顶)。
package main

import (
	"fmt"
	"math"
)

// BoundsThreshold 是 sklearn.preprocessing._data 里的常量。
const BoundsThreshold = 1e-7

// Spacing1 是 numpy.spacing(1)。
const Spacing1 = 2.220446049250313e-16

// NormalPpf 是标准正态分位函数:Φ⁻¹(p) = √2·erfinv(2p−1),
// erfinv 直接用标准库 math.Erfinv(Go 1.10 起),不要手抄有理逼近系数。
func NormalPpf(p float64) float64 {
	if p <= 0 {
		return math.Inf(-1)
	}
	if p >= 1 {
		return math.Inf(1)
	}
	return math.Sqrt2 * math.Erfinv(2*p-1)
}

// BoxCox 是 (x^λ − 1)/λ(λ≠0)与 log x(λ=0);要求 x > 0。
func BoxCox(x, lam float64) float64 {
	if x <= 0 {
		panic(fmt.Sprintf("Box-Cox 只接受严格正数,实得 %v", x))
	}
	if lam == 0 {
		return math.Log(x)
	}
	return (math.Pow(x, lam) - 1) / lam
}

// YeoJohnson 的四分支:正半轴用 λ,负半轴用 2−λ 的镜像。
func YeoJohnson(x, lam float64) float64 {
	if x >= 0 {
		if lam == 0 {
			return math.Log(x + 1)
		}
		return (math.Pow(x+1, lam) - 1) / lam
	}
	if lam == 2 {
		return -math.Log(-x + 1)
	}
	return -((math.Pow(-x+1, 2-lam) - 1) / (2 - lam))
}

// BoxCoxLLF 是剖面对数似然:l = (λ−1)Σlog(x) − N/2·log(Σ(y−ȳ)²/N)。
func BoxCoxLLF(lam float64, x []float64) float64 {
	n := float64(len(x))
	y := make([]float64, len(x))
	slog := 0.0
	for i, v := range x {
		y[i] = BoxCox(v, lam)
		slog += math.Log(v)
	}
	var1 := variance(y)
	if var1 <= 0 {
		return math.Inf(-1)
	}
	return (lam-1)*slog - n/2*math.Log(var1)
}

// YeoJohnsonLLF 是 l = −N/2·log(σ̂²) + (λ−1)Σ sign(x)·log(|x|+1)。
func YeoJohnsonLLF(lam float64, x []float64) float64 {
	n := float64(len(x))
	y := make([]float64, len(x))
	s := 0.0
	for i, v := range x {
		y[i] = YeoJohnson(v, lam)
		l := math.Log(math.Abs(v) + 1)
		if v < 0 {
			l = -l
		}
		s += l
	}
	var1 := variance(y)
	if var1 <= 0 {
		return math.Inf(-1)
	}
	return -n/2*math.Log(var1) + (lam-1)*s
}

func variance(x []float64) float64 {
	m := Mean(x)
	s := 0.0
	for _, v := range x {
		s += (v - m) * (v - m)
	}
	return s / float64(len(x))
}

// Mean 是算术平均。
func Mean(x []float64) float64 {
	s := 0.0
	for _, v := range x {
		s += v
	}
	return s / float64(len(x))
}

// Skewness 是总体偏度 g1 = m3 / m2^1.5。
func Skewness(x []float64) float64 {
	m := Mean(x)
	m2, m3 := 0.0, 0.0
	for _, v := range x {
		d := v - m
		m2 += d * d
		m3 += d * d * d
	}
	m2 /= float64(len(x))
	m3 /= float64(len(x))
	return m3 / math.Pow(m2, 1.5)
}

// Pearson 是皮尔逊相关系数。
func Pearson(a, b []float64) float64 {
	ma, mb := Mean(a), Mean(b)
	num, da, db := 0.0, 0.0, 0.0
	for i := range a {
		num += (a[i] - ma) * (b[i] - mb)
		da += (a[i] - ma) * (a[i] - ma)
		db += (b[i] - mb) * (b[i] - mb)
	}
	return num / (math.Sqrt(da) * math.Sqrt(db))
}

// ArgmaxLLF 用黄金分割求 λ 的极点(剖面似然关于 λ 单峰)。
func ArgmaxLLF(x []float64, llf func(float64, []float64) float64, lo, hi float64) float64 {
	const invphi = 0.6180339887498949
	a, b := lo, hi
	c, d := b-invphi*(b-a), a+invphi*(b-a)
	fc, fd := llf(c, x), llf(d, x)
	for i := 0; i < 200 && b-a > 1e-12; i++ {
		if fc > fd {
			b, d, fd = d, c, fc
			c = b - invphi*(b-a)
			fc = llf(c, x)
		} else {
			a, c, fc = c, d, fd
			d = a + invphi*(b-a)
			fd = llf(d, x)
		}
	}
	return (a + b) / 2
}
