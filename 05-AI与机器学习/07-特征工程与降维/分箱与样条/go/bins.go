package main

// KBinsDiscretizer 的纯标准库 Go 实现,与 python/bins_core.py 同口径。
// 对齐对象:scikit-learn 1.9.1 sklearn/preprocessing/_discretization.py。

import (
	"math"
	"sort"
)

// ---------------------------------------------------------------- 分位数口径

// neighbors 返回 (prev, next, gammaRaw),越界处理同 numpy _get_indexes。
func neighbors(idx float64, n int) (int, int, float64) {
	if idx < 0 {
		return 0, 0, 0.0
	}
	if idx >= float64(n-1) {
		return n - 1, n - 1, 0.0
	}
	prev := int(math.Floor(idx))
	return prev, prev + 1, idx - float64(prev)
}

// PercentileLinear 是 numpy 默认口径(H&F type 7):idx = (n-1)*q。
func PercentileLinear(x []float64, q float64) float64 {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	prev, next, g := neighbors(float64(len(xs)-1)*q, len(xs))
	return (1.0-g)*xs[prev] + g*xs[next]
}

// PercentileInvertedCDF 是 H&F type 1:idx = n*q - 1。
func PercentileInvertedCDF(x []float64, q float64) float64 {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	idx := float64(len(xs))*q - 1.0
	prev := int(math.Floor(idx))
	res := prev
	if idx-float64(prev) != 0 {
		res = prev + 1
	}
	if res < 0 {
		res = 0
	}
	return xs[res]
}

// PercentileAvgInvertedCDF 是 H&F type 2:gamma 被强制成 0.5 或 1.0。
func PercentileAvgInvertedCDF(x []float64, q float64) float64 {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	prev, next, g := neighbors(float64(len(xs))*q-1.0, len(xs))
	if g == 0 {
		g = 0.5
	} else {
		g = 1.0
	}
	return (1.0-g)*xs[prev] + g*xs[next]
}

// Percentiles 按 method 批量取分位数,levels 是百分位(0..100)。
func Percentiles(x []float64, levels []float64, method string) []float64 {
	out := make([]float64, 0, len(levels))
	for _, lv := range levels {
		switch method {
		case "inverted_cdf":
			out = append(out, PercentileInvertedCDF(x, lv/100.0))
		case "averaged_inverted_cdf":
			out = append(out, PercentileAvgInvertedCDF(x, lv/100.0))
		default:
			out = append(out, PercentileLinear(x, lv/100.0))
		}
	}
	return out
}

// Linspace 与 numpy.linspace(endpoint=True) 同口径。
func Linspace(a, b float64, num int) []float64 {
	if num == 1 {
		return []float64{a}
	}
	step := (b - a) / float64(num-1)
	out := make([]float64, num)
	for i := range out {
		out[i] = a + step*float64(i)
	}
	return out
}

// ---------------------------------------------------------------- 箱边界

func BinEdgesUniform(x []float64, nBins int) []float64 {
	lo, hi := minMax(x)
	return Linspace(lo, hi, nBins+1)
}

func BinEdgesQuantile(x []float64, nBins int, method string) []float64 {
	return Percentiles(x, Linspace(0, 100, nBins+1), method)
}

// BinEdgesKmeans 是 1D k-means:确定性初始化(均匀箱中点)+ Lloyd 迭代,
// 收敛判据 (center_shift^2).sum() <= tol 与 sklearn 的 KMeans 一致。
func BinEdgesKmeans(x []float64, nBins int) []float64 {
	const tol, maxIter = 1e-4, 300
	lo, hi := minMax(x)
	edges := Linspace(lo, hi, nBins+1)
	centers := make([]float64, nBins)
	for i := range centers {
		centers[i] = (edges[i] + edges[i+1]) * 0.5
	}
	for it := 0; it < maxIter; it++ {
		sums := make([]float64, nBins)
		cnts := make([]int, nBins)
		for _, v := range x {
			best, bestD := 0, math.Inf(1)
			for i, c := range centers {
				d := (v - c) * (v - c)
				if d < bestD {
					best, bestD = i, d
				}
			}
			sums[best] += v
			cnts[best]++
		}
		shift := 0.0
		for i := range centers {
			if cnts[i] == 0 {
				continue
			}
			nc := sums[i] / float64(cnts[i])
			shift += (nc - centers[i]) * (nc - centers[i])
			centers[i] = nc
		}
		sort.Float64s(centers)
		if shift <= tol {
			break
		}
	}
	out := []float64{lo}
	for i := 0; i+1 < len(centers); i++ {
		out = append(out, (centers[i]+centers[i+1])*0.5)
	}
	return append(out, hi)
}

// DropNarrowEdges 丢弃宽度 <= 1e-8 的箱;首元素永远保留。
func DropNarrowEdges(edges []float64) ([]float64, bool) {
	kept := []float64{edges[0]}
	for i := 1; i < len(edges); i++ {
		if edges[i]-edges[i-1] > 1e-8 {
			kept = append(kept, edges[i])
		}
	}
	return kept, len(kept) != len(edges)
}

// ---------------------------------------------------------------- 分箱器

type KBins struct {
	NBins          int
	Encode         string
	Strategy       string
	QuantileMethod string
	BinEdges       [][]float64
	NBinsPer       []int
}

func NewKBins(nBins int, encode, strategy, quantileMethod string) *KBins {
	return &KBins{NBins: nBins, Encode: encode, Strategy: strategy,
		QuantileMethod: quantileMethod}
}

// Fit 逐列独立求箱边界;常量列退化成单箱 [-inf, +inf]。
func (k *KBins) Fit(X [][]float64) *KBins {
	nFeat := len(X[0])
	k.BinEdges = k.BinEdges[:0]
	k.NBinsPer = k.NBinsPer[:0]
	for j := 0; j < nFeat; j++ {
		col := make([]float64, len(X))
		for i := range X {
			col[i] = X[i][j]
		}
		lo, hi := minMax(col)
		if lo == hi {
			k.BinEdges = append(k.BinEdges, []float64{math.Inf(-1), math.Inf(1)})
			k.NBinsPer = append(k.NBinsPer, 1)
			continue
		}
		var edges []float64
		switch k.Strategy {
		case "uniform":
			edges = BinEdgesUniform(col, k.NBins)
		case "kmeans":
			edges = BinEdgesKmeans(col, k.NBins)
		default:
			edges = BinEdgesQuantile(col, k.NBins, k.QuantileMethod)
		}
		if k.Strategy == "quantile" || k.Strategy == "kmeans" {
			edges, _ = DropNarrowEdges(edges)
		}
		k.BinEdges = append(k.BinEdges, edges)
		k.NBinsPer = append(k.NBinsPer, len(edges)-1)
	}
	return k
}

// codeAt 用 bisect_right(edges[1:-1], v) 定箱 —— 恰在内部边界上的点归上一箱。
func (k *KBins) codeAt(j int, v float64) int {
	edges := k.BinEdges[j]
	inner := edges[1 : len(edges)-1]
	return sort.Search(len(inner), func(i int) bool { return inner[i] > v })
}

func (k *KBins) Transform(X [][]float64) [][]float64 {
	codes := make([][]int, len(X))
	for i, row := range X {
		codes[i] = make([]int, len(row))
		for j, v := range row {
			codes[i][j] = k.codeAt(j, v)
		}
	}
	if k.Encode == "ordinal" {
		out := make([][]float64, len(X))
		for i, c := range codes {
			out[i] = make([]float64, len(c))
			for j, ci := range c {
				out[i][j] = float64(ci)
			}
		}
		return out
	}
	out := make([][]float64, len(X))
	for i, c := range codes {
		enc := make([]float64, 0, 8)
		for j, ci := range c {
			for b := 0; b < k.NBinsPer[j]; b++ {
				if b == ci {
					enc = append(enc, 1.0)
				} else {
					enc = append(enc, 0.0)
				}
			}
		}
		out[i] = enc
	}
	return out
}

// InverseTransform 用箱中心还原;常量列的 [-inf, +inf] 中心是 NaN。
func (k *KBins) InverseTransform(Xt [][]float64) [][]float64 {
	var codes [][]int
	if len(k.Encode) >= 6 && k.Encode[:6] == "onehot" {
		for _, row := range Xt {
			pos, acc := make([]int, 0, len(k.NBinsPer)), 0
			for _, nb := range k.NBinsPer {
				best, bestV := 0, math.Inf(-1)
				for b := 0; b < nb; b++ {
					if row[acc+b] > bestV {
						best, bestV = b, row[acc+b]
					}
				}
				pos = append(pos, best)
				acc += nb
			}
			codes = append(codes, pos)
		}
	} else {
		for _, row := range Xt {
			c := make([]int, len(row))
			for j, v := range row {
				c[j] = int(v)
			}
			codes = append(codes, c)
		}
	}
	out := make([][]float64, len(codes))
	for i, c := range codes {
		vals := make([]float64, len(c))
		for j, ci := range c {
			edges := k.BinEdges[j]
			vals[j] = (edges[ci] + edges[ci+1]) * 0.5
		}
		out[i] = vals
	}
	return out
}

func minMax(x []float64) (float64, float64) {
	lo, hi := x[0], x[0]
	for _, v := range x[1:] {
		if v < lo {
			lo = v
		}
		if v > hi {
			hi = v
		}
	}
	return lo, hi
}
