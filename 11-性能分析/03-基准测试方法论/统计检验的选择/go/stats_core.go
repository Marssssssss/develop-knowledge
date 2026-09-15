// 统计检验原语(Go 版): t 分布 / Welch t / Mann-Whitney U / 位置估计量 / 置信区间。
//
// 被 test_selection.go 复用; 锚点与 python/stats_core.py 一致:
//   * df=10 的双尾 0.05 临界值 = 2.228139(教科书值)
//   * NIST/SEMATECH 手册 7.3.1 的 Welch 算例: t=2.2694, ν≈15.5
package main

import (
	"math"
	"sort"
)

const alpha = 0.05

func betacf(a, b, x float64) float64 {
	const itmax, eps = 200, 3e-16
	qab, qap, qam := a+b, a+1, a-1
	c, d := 1.0, 1.0-qab*x/qap
	if math.Abs(d) < 1e-30 {
		d = 1e-30
	}
	d = 1 / d
	h := d
	for m := 1; m <= itmax; m++ {
		m2 := float64(2 * m)
		aa := float64(m) * (b - float64(m)) * x / ((qam + m2) * (a + m2))
		d = 1 + aa*d
		if math.Abs(d) < 1e-30 {
			d = 1e-30
		}
		c = 1 + aa/c
		if math.Abs(c) < 1e-30 {
			c = 1e-30
		}
		d = 1 / d
		h *= d * c
		aa = -(a + float64(m)) * (qab + float64(m)) * x / ((a + m2) * (qap + m2))
		d = 1 + aa*d
		if math.Abs(d) < 1e-30 {
			d = 1e-30
		}
		c = 1 + aa/c
		if math.Abs(c) < 1e-30 {
			c = 1e-30
		}
		d = 1 / d
		delta := d * c
		h *= delta
		if math.Abs(delta-1) < eps {
			break
		}
	}
	return h
}

func betaincReg(a, b, x float64) float64 {
	if x <= 0 {
		return 0
	}
	if x >= 1 {
		return 1
	}
	lbeta, _ := math.Lgamma(a + b)
	la, _ := math.Lgamma(a)
	lb, _ := math.Lgamma(b)
	front := math.Exp(lbeta - la - lb + a*math.Log(x) + b*math.Log(1-x))
	if x < (a+1)/(a+b+2) {
		return front * betacf(a, b, x) / a
	}
	return 1 - front*betacf(b, a, 1-x)/b
}

// tTwoSidedP: P(|T| > |t|) = I_{df/(df+t^2)}(df/2, 1/2)
func tTwoSidedP(t, df float64) float64 { return betaincReg(df/2, 0.5, df/(df+t*t)) }

func tCritical(df, a float64) float64 {
	lo, hi := 0.0, 1000.0
	for i := 0; i < 200; i++ {
		mid := (lo + hi) / 2
		if tTwoSidedP(mid, df) > a {
			lo = mid
		} else {
			hi = mid
		}
	}
	return (lo + hi) / 2
}

func mean(x []float64) float64 {
	s := 0.0
	for _, v := range x {
		s += v
	}
	return s / float64(len(x))
}

func variance(x []float64) float64 {
	m := mean(x)
	s := 0.0
	for _, v := range x {
		s += (v - m) * (v - m)
	}
	return s / float64(len(x)-1)
}

// welchT -> (t, df, p); df 用 Welch-Satterthwaite 近似。
func welchT(x, y []float64) (float64, float64, float64) {
	n1, n2 := float64(len(x)), float64(len(y))
	v1, v2 := variance(x)/n1, variance(y)/n2
	t := (mean(x) - mean(y)) / math.Sqrt(v1+v2)
	df := (v1 + v2) * (v1 + v2) / (v1*v1/(n1-1) + v2*v2/(n2-1))
	return t, df, tTwoSidedP(t, df)
}

// mannWhitneyP -> (U1, p); 正态近似 + 并列秩校正 + 连续性校正。
// H0 是"两组同分布 / 无随机优势", 不是"均值相等"。
func mannWhitneyP(x, y []float64) (float64, float64) {
	n1, n2 := len(x), len(y)
	type item struct {
		v float64
		g int
	}
	merged := make([]item, 0, n1+n2)
	for _, v := range x {
		merged = append(merged, item{v, 1})
	}
	for _, v := range y {
		merged = append(merged, item{v, 2})
	}
	sort.Slice(merged, func(i, j int) bool { return merged[i].v < merged[j].v })
	r1 := 0.0
	ties := []int{}
	for i := 0; i < len(merged); {
		j := i
		for j+1 < len(merged) && merged[j+1].v == merged[i].v {
			j++
		}
		avg := float64(i+1+j+1) / 2
		for k := i; k <= j; k++ {
			if merged[k].g == 1 {
				r1 += avg
			}
		}
		ties = append(ties, j-i+1)
		i = j + 1
	}
	u1 := r1 - float64(n1*(n1+1))/2
	n := float64(n1 + n2)
	tieCorr := 0.0
	for _, ts := range ties {
		tieCorr += float64(ts*ts*ts - ts)
	}
	sigma2 := float64(n1*n2) * ((n + 1) - tieCorr/(n*(n-1))) / 12
	if sigma2 <= 0 {
		return u1, 1
	}
	numer := u1 - float64(n1*n2)/2
	if numer > 0 {
		numer -= 0.5
	} else if numer < 0 {
		numer += 0.5
	}
	z := numer / math.Sqrt(sigma2)
	phi := 0.5 * (1 + math.Erf(z/math.Sqrt2))
	return u1, math.Min(1, 2*math.Min(phi, 1-phi))
}

// median / trimmedMean / winsorizedMean: NIST 定义的位置估计量。
func median(x []float64) float64 {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	n := len(xs)
	if n%2 == 1 {
		return xs[n/2]
	}
	return (xs[n/2-1] + xs[n/2]) / 2
}

func trimmedMean(x []float64, prop float64) float64 {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	k := int(float64(len(xs)) * prop)
	return mean(xs[k : len(xs)-k])
}

func winsorizedMean(x []float64, prop float64) float64 {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	k := int(float64(len(xs)) * prop)
	lo, hi := xs[k], xs[len(xs)-1-k]
	out := make([]float64, len(xs))
	for i, v := range xs {
		out[i] = math.Min(math.Max(v, lo), hi)
	}
	return mean(out)
}

// medianCIOrderStatistic 分布无关的中位数置信区间。
func medianCIOrderStatistic(x []float64, a float64) (float64, float64) {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	n := len(xs)
	k := 1
	for cand := 1; cand <= n/2; cand++ {
		acc := 0.0
		for i := 0; i < cand; i++ {
			acc += float64(binom(n, i))
		}
		if acc/math.Pow(2, float64(n)) <= a/2 {
			k = cand
		} else {
			break
		}
	}
	return xs[k-1], xs[n-k]
}

// meanCIT 均值的 t 置信区间(前提: 近似正态 / 小样本无重尾)。
func meanCIT(x []float64, a float64) (float64, float64) {
	n := float64(len(x))
	half := tCritical(n-1, a) * math.Sqrt(variance(x)) / math.Sqrt(n)
	m := mean(x)
	return m - half, m + half
}

func binom(n, k int) int64 {
	if k < 0 || k > n {
		return 0
	}
	res := int64(1)
	for i := 0; i < k; i++ {
		res = res * int64(n-i) / int64(i+1)
	}
	return res
}

func closeTo(a, b, tol float64) bool { return math.Abs(a-b) <= tol }
