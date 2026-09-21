// Package main 的构件层(二):百分位口径与分位数变换。
//
// 口径来自 numpy.percentile 的 method 参数与 sklearn.preprocessing 的实现。
package main

import "math"

// PercentileLinear 是 numpy 默认口径(Hyndman-Fan type 7):
// h = (n−1)q/100,取 x[⌊h⌋] + (h−⌊h⌋)(x[⌊h⌋+1] − x[⌊h⌋])。
func PercentileLinear(sortedX []float64, q float64) float64 {
	n := len(sortedX)
	if n == 1 {
		return sortedX[0]
	}
	h := float64(n-1) * q / 100
	lo := int(math.Floor(h))
	if lo >= n-1 {
		return sortedX[n-1]
	}
	return sortedX[lo] + (h-float64(lo))*(sortedX[lo+1]-sortedX[lo])
}

// PercentileAvgInvertedCDF 是 "averaged_inverted_cdf" 口径(主干版 sklearn 用它)。
func PercentileAvgInvertedCDF(sortedX []float64, q float64) float64 {
	n := len(sortedX)
	if q <= 0 {
		return sortedX[0]
	}
	if q >= 100 {
		return sortedX[n-1]
	}
	pos := float64(n) * q / 100
	lo := int(math.Floor(pos))
	if pos-float64(lo) == 0 {
		if lo == 0 {
			return sortedX[0]
		}
		return (sortedX[lo-1] + sortedX[lo]) / 2
	}
	if lo > n-1 {
		lo = n - 1
	}
	return sortedX[lo]
}

// QuantileTransformer 对齐 sklearn.preprocessing.QuantileTransformer 的正变换。
type QuantileTransformer struct {
	NQuantiles    int
	OutputDist    string
	QuantileMode  string // "linear"(1.9 行为,默认)或 "averaged_inverted_cdf"
	NQuantilesFit int
	References    []float64
	Quantiles     []float64
}

// Fit 计算地标:references = linspace(0,1,n_quantiles) 端点含,取值按其 100 倍百分位。
func (t *QuantileTransformer) Fit(col []float64) {
	n := len(col)
	t.NQuantilesFit = t.NQuantiles
	if t.NQuantilesFit > n {
		t.NQuantilesFit = n // 1.9 及以前按样本数封顶(1.10 起不再封顶)
	}
	t.References = linspace(0, 1, t.NQuantilesFit)
	sorted := append([]float64{}, col...)
	sortFloats(sorted)
	t.Quantiles = make([]float64, len(t.References))
	for i, r := range t.References {
		if t.QuantileMode == "averaged_inverted_cdf" {
			t.Quantiles[i] = PercentileAvgInvertedCDF(sorted, r*100)
		} else {
			t.Quantiles[i] = PercentileLinear(sorted, r*100)
		}
	}
}

// TransformCol 变换单列:x 低于/高于拟合范围时映射到输出分布的端点。
func (t *QuantileTransformer) TransformCol(col []float64) []float64 {
	refs := t.References
	out := make([]float64, len(col))
	for i, x := range col {
		var loIdx, hiIdx bool
		if t.OutputDist == "normal" {
			loIdx = x-BoundsThreshold < t.Quantiles[0]
			hiIdx = x+BoundsThreshold > t.Quantiles[len(t.Quantiles)-1]
		} else {
			loIdx = x == t.Quantiles[0]
			hiIdx = x == t.Quantiles[len(t.Quantiles)-1]
		}
		// 两个方向各插值一次再平均:重复值(平台)才不会只取到上/下沿
		fwd := interp(x, t.Quantiles, refs)
		rev := interp(-x, negReverse(t.Quantiles), negReverse(refs))
		v := 0.5 * (fwd - rev)
		if hiIdx {
			v = 1
		}
		if loIdx {
			v = 0
		}
		if t.OutputDist == "normal" {
			v = NormalPpf(v)
			lo := NormalPpf(BoundsThreshold - Spacing1)
			hi := NormalPpf(1 - (BoundsThreshold - Spacing1))
			v = math.Max(math.Min(v, hi), lo)
		}
		out[i] = v
	}
	return out
}

func negReverse(v []float64) []float64 {
	out := make([]float64, len(v))
	for i := range v {
		out[i] = -v[len(v)-1-i]
	}
	return out
}

// interp 是 np.interp:xp 升序,区间外夹取端点值。
func interp(x float64, xp, fp []float64) float64 {
	if x <= xp[0] {
		return fp[0]
	}
	if x >= xp[len(xp)-1] {
		return fp[len(fp)-1]
	}
	i := searchSortedRight(xp, x) - 1
	if i >= len(xp)-1 {
		return fp[len(fp)-1]
	}
	if xp[i+1] == xp[i] {
		return fp[i]
	}
	w := (x - xp[i]) / (xp[i+1] - xp[i])
	return fp[i]*(1-w) + fp[i+1]*w
}

func searchSortedRight(a []float64, x float64) int {
	lo, hi := 0, len(a)
	for lo < hi {
		mid := (lo + hi) / 2
		if x < a[mid] {
			hi = mid
		} else {
			lo = mid + 1
		}
	}
	return lo
}

func linspace(start, stop float64, num int) []float64 {
	out := make([]float64, num)
	if num == 1 {
		out[0] = start
		return out
	}
	step := (stop - start) / float64(num-1)
	for i := range out {
		out[i] = start + float64(i)*step
	}
	return out
}

func sortFloats(a []float64) {
	for i := 1; i < len(a); i++ {
		v := a[i]
		j := i - 1
		for j >= 0 && a[j] > v {
			a[j+1] = a[j]
			j--
		}
		a[j+1] = v
	}
}
