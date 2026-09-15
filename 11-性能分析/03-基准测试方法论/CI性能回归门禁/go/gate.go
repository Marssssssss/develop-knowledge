// CI 性能回归门禁的核心判定(Go 版) —— 被 regression_gate.go 复用。
//
//  1. step fit(Skia/Jetpack): 窗口前后均值/方差比较, 要求 |delta| >= THRESHOLD 且 |z| > 2;
//     单点尖峰不算回归; 相邻候选合并
//  2. 双判据(Picasso 风格): 幅度阈值 10% + Welch t 检验; df > 30 用 z=1.96, 小样本查表插值;
//     缺失标准差按 5% CoV 兜底
//  3. 降噪优先(Apogee): 同码重跑的 CoV 门禁 + 3σ 历史噪声地板
package main

import (
	"math"
	"sort"
)

const (
	alpha         = 0.05
	magThreshold  = 0.10
	stepWidth     = 5
	stepThreshold = 0.25
	stepZmin      = 2.0
)

var tTable = []float64{12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
	2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
	2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042}

// tCritical: df > 30 -> 1.96(正态近似); 否则查表并对小数自由度线性插值。
func tCritical(df float64) float64 {
	if df > 30 {
		return 1.96
	}
	if df <= 1 {
		return tTable[0]
	}
	lo := int(math.Floor(df))
	frac := df - float64(lo)
	return tTable[lo-1] + (tTable[lo]-tTable[lo-1])*frac
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

// Step 一个阶跃候选。
type Step struct {
	Index int
	Delta float64
	Z     float64
}

// stepFit 在结果序列里找阶跃; z 用绝对差值除以 stderr(量纲正确)。
func stepFit(series []float64, width int, threshold, zMin float64) []Step {
	out := []Step{}
	n := len(series)
	for i := width; i <= n-width; i++ {
		before := series[i-width : i]
		after := series[i : i+width]
		mb, ma := mean(before), mean(after)
		if mb == 0 {
			continue
		}
		delta := (ma - mb) / mb
		stderr := math.Sqrt(variance(before)/float64(len(before)) +
			variance(after)/float64(len(after)))
		var z float64
		if stderr == 0 {
			if ma != mb {
				z = math.Inf(1)
			}
		} else {
			z = (ma - mb) / stderr
		}
		if math.Abs(delta) >= threshold && math.Abs(z) > zMin {
			out = append(out, Step{i, delta, z})
		}
	}
	merged := []Step{}
	for _, c := range out {
		if len(merged) > 0 && c.Index-merged[len(merged)-1].Index < width {
			continue
		}
		merged = append(merged, c)
	}
	return merged
}

// naiveDelta 反例: "与前一次构建比大小"。
func naiveDelta(series []float64) []float64 {
	out := []float64{}
	for i := 1; i < len(series); i++ {
		if series[i-1] != 0 {
			out = append(out, (series[i]-series[i-1])/series[i-1])
		}
	}
	return out
}

// Verdict 双判据结论。
type Verdict struct {
	Delta       float64
	T           float64
	Df          float64
	Significant bool
	Label       string
}

// welchVerdict 幅度阈值 + Welch t 显著性的双判据。
func welchVerdict(baseline, current []float64, magTh, assumeCV float64) Verdict {
	nb, nc := len(baseline), len(current)
	mb, mc := mean(baseline), mean(current)
	delta := (mc - mb) / mb
	sb, sc := 0.0, 0.0
	if nb > 1 {
		sb = math.Sqrt(variance(baseline))
	}
	if nc > 1 {
		sc = math.Sqrt(variance(current))
	}
	if sb == 0 {
		sb = math.Abs(mb) * assumeCV
	}
	if sc == 0 {
		sc = math.Abs(mc) * assumeCV
	}
	v1, v2 := sb*sb/float64(nb), sc*sc/float64(nc)
	if v1+v2 == 0 {
		return Verdict{0, 0, 0, false, "identical"}
	}
	t := (mc - mb) / math.Sqrt(v1+v2)
	df := 1.0
	if nb > 1 && nc > 1 {
		df = (v1 + v2) * (v1 + v2) / (v1*v1/float64(nb-1) + v2*v2/float64(nc-1))
	}
	sig := math.Abs(t) > tCritical(df)
	label := "no_significant_change"
	switch {
	case !sig:
	case math.Abs(delta) < magTh:
		label = "acceptable_change"
	case delta < 0:
		label = "improvement"
	default:
		label = "REGRESSION"
	}
	return Verdict{delta, t, df, sig, label}
}

// noiseFloor: max(固定下限, k 倍历史变异系数)。
func noiseFloor(history []float64, k, floor float64) float64 {
	if len(history) < 2 {
		return floor
	}
	cv := math.Sqrt(variance(history)) / math.Abs(mean(history))
	return math.Max(floor, k*cv)
}

// covGate: 可检测性前置条件。
func covGate(series []float64, limit float64) (bool, float64) {
	cv := math.Sqrt(variance(series)) / math.Abs(mean(series))
	return cv <= limit, cv
}

// bonferroni / benjaminiHochberg: 多重比较校正。
func bonferroni(p []float64, a float64) []bool {
	out := make([]bool, len(p))
	for i, v := range p {
		out[i] = v < a/float64(len(p))
	}
	return out
}

func benjaminiHochberg(p []float64, a float64) []bool {
	m := len(p)
	idx := make([]int, m)
	for i := range idx {
		idx[i] = i
	}
	sort.Slice(idx, func(i, j int) bool { return p[idx[i]] < p[idx[j]] })
	out := make([]bool, m)
	kmax := -1
	for rank, id := range idx {
		if p[id] <= a*float64(rank+1)/float64(m) {
			kmax = rank
		}
	}
	for rank, id := range idx {
		if rank <= kmax {
			out[id] = true
		}
	}
	return out
}

func countTrue(v []bool) int {
	c := 0
	for _, b := range v {
		if b {
			c++
		}
	}
	return c
}
