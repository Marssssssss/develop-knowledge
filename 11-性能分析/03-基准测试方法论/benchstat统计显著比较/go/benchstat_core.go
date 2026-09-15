// benchstat 核心统计(Go 版) —— 被 benchstat_lite.go 复用。
//
//  1. 非参数中位数置信区间(次序统计量法)
//  2. Mann-Whitney U 检验: 精确 U 分布(无并列且样本小) + 正态近似(含并列秩校正与连续性校正)
//  3. geomean 比例语义 + benchstat 风格表格
//
// 数值锚点来自 golang.org/x/perf/internal/stats/utest.go:
//   - U1 = R1 - n1(n1+1)/2, U2 = n1*n2 - U1, 检验统计量取两者较小者
//   - 精确分布阈值: 无并列 n<=50, 有并列 n<=25(有并列时秩非整数, 本实现退化为正态近似)
//   - 正态近似: sigma_U = sqrt(n1*n2*((N+1) - t/(N*(N-1)))/12), t = Σ(tj^3 - tj)
//   - 双侧 p 值: numer -= sign(numer)*0.5 后 z = numer/sigma_U, p = 2*min(Φ(z), 1-Φ(z))
//   - 特例: U1 == U2 时 p = 1(离散分布不能直接 2*CDF, 会重复计入 Usmall 处的概率质量)
package main

import (
	"fmt"
	"math"
	"sort"
)

const alphaBW = 0.05

func binomTailLE(n, k int) float64 {
	if k < 0 {
		return 0
	}
	acc := 0.0
	for i := 0; i <= k && i <= n; i++ {
		acc += float64(binom(n, i))
	}
	return acc / math.Pow(2, float64(n))
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

// medianCIOrderStatistic 返回 (下界, 上界, 名义覆盖率下界)。
func medianCIOrderStatistic(x []float64, a float64) (float64, float64, float64) {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	n := len(xs)
	k := 1
	for cand := 1; cand <= n/2; cand++ {
		if binomTailLE(n, cand-1) <= a/2 {
			k = cand
		} else {
			break
		}
	}
	lo, hi := k-1, n-k
	for lo > 0 && xs[lo-1] == xs[lo] {
		lo--
	}
	for hi < n-1 && xs[hi+1] == xs[hi] {
		hi++
	}
	return xs[lo], xs[hi], 1 - 2*binomTailLE(n, k-1)
}

// averageRanks 返回样本 1 的秩和与各并列组规模。
func averageRanks(x1, x2 []float64) (float64, []int) {
	type item struct {
		v float64
		g int
	}
	merged := make([]item, 0, len(x1)+len(x2))
	for _, v := range x1 {
		merged = append(merged, item{v, 1})
	}
	for _, v := range x2 {
		merged = append(merged, item{v, 2})
	}
	sort.Slice(merged, func(i, j int) bool { return merged[i].v < merged[j].v })

	r1 := 0.0
	tieSizes := []int{}
	for i := 0; i < len(merged); {
		j := i
		for j+1 < len(merged) && merged[j+1].v == merged[i].v {
			j++
		}
		avgRank := float64(i+1+j+1) / 2
		for t := i; t <= j; t++ {
			if merged[t].g == 1 {
				r1 += avgRank
			}
		}
		tieSizes = append(tieSizes, j-i+1)
		i = j + 1
	}
	return r1, tieSizes
}

// uDistributionExact 精确 U 分布: 从 1..N 取 n1 个元素的子集和的计数, 平移后即 U 的计数。
func uDistributionExact(n1, n2 int) map[int]int64 {
	n := n1 + n2
	maxSum := n1 * n
	dp := make([][]int64, n1+1)
	for i := range dp {
		dp[i] = make([]int64, maxSum+1)
	}
	dp[0][0] = 1
	for v := 1; v <= n; v++ {
		kmax := v
		if kmax > n1 {
			kmax = n1
		}
		for k := kmax; k >= 1; k-- {
			for s := maxSum; s >= v; s-- {
				if dp[k-1][s-v] != 0 {
					dp[k][s] += dp[k-1][s-v]
				}
			}
		}
	}
	offset := n1 * (n1 + 1) / 2
	out := map[int]int64{}
	for s, c := range dp[n1] {
		if c != 0 {
			out[s-offset] = c
		}
	}
	return out
}

func normalCDF(z float64) float64 { return 0.5 * (1 + math.Erf(z/math.Sqrt2)) }

// mannWhitneyU 返回 (U1, p, 方法名)。
func mannWhitneyU(x1, x2 []float64, a float64) (float64, float64, string) {
	n1, n2 := len(x1), len(x2)
	r1, ties := averageRanks(x1, x2)
	u1 := r1 - float64(n1*(n1+1))/2
	u2 := float64(n1*n2) - u1
	uSmall := math.Min(u1, u2)

	hasTies := false
	for _, t := range ties {
		if t > 1 {
			hasTies = true
		}
	}

	if !hasTies && n1 <= 50 && n2 <= 50 && n1*n2 <= 2500 {
		counts := uDistributionExact(n1, n2)
		var total int64
		for _, c := range counts {
			total += c
		}
		if u1 == u2 {
			return u1, 1, "exact"
		}
		var le int64
		for u, c := range counts {
			if float64(u) <= uSmall {
				le += c
			}
		}
		return u1, math.Min(1, 2*float64(le)/float64(total)), "exact"
	}

	t := 0.0
	for _, ts := range ties {
		t += float64(ts*ts*ts - ts)
	}
	n := float64(n1 + n2)
	sigma2 := float64(n1*n2) * ((n + 1) - t/(n*(n-1))) / 12
	if sigma2 <= 0 {
		return u1, math.NaN(), "degenerate"
	}
	numer := u1 - float64(n1*n2)/2
	if numer > 0 {
		numer -= 0.5
	} else if numer < 0 {
		numer += 0.5
	}
	z := numer / math.Sqrt(sigma2)
	p := 2 * math.Min(normalCDF(z), 1-normalCDF(z))
	_ = a
	return u1, math.Min(1, p), "normal+tie+continuity"
}

func geomean(v []float64) float64 {
	s := 0.0
	for _, x := range v {
		s += math.Log(x)
	}
	return math.Exp(s / float64(len(v)))
}

func geomeanRatio(after, before []float64) float64 {
	s := 0.0
	for i := range after {
		s += math.Log(after[i] / before[i])
	}
	return math.Exp(s / float64(len(after)))
}

// BenchRow 一行对比数据(名称 + 前后两次测量)。
type BenchRow struct {
	Name   string
	Before []float64
	After  []float64
}

const tableSep = "-------------------------------------------------------------------------------------"

// benchstatTable 生成 benchstat 风格表格; p >= alpha 时差异列显示 '~'。
func benchstatTable(rows []BenchRow, a float64) []string {
	out := []string{
		"benchmark                   base(sec/op)        new(sec/op)      vs base",
		tableSep,
	}
	baseMed, newMed := []float64{}, []float64{}
	for _, row := range rows {
		b, nw := medianOf(row.Before), medianOf(row.After)
		baseMed = append(baseMed, b)
		newMed = append(newMed, nw)
		_, p, method := mannWhitneyU(row.Before, row.After, a)
		delta := fmt.Sprintf("~ (p=%.3f n=%d)", p, len(row.Before))
		if p < a {
			delta = fmt.Sprintf("%+.2f%% (p=%.3f n=%d, %s)",
				(nw-b)/b*100, p, len(row.Before), method)
		}
		out = append(out, fmt.Sprintf("%-26s %12.6g      %12.6g     %s",
			row.Name, b, nw, delta))
	}
	out = append(out, tableSep)
	out = append(out, fmt.Sprintf("%-26s %12.6g      %12.6g     %+.2f%%",
		"geomean", geomean(baseMed), geomean(newMed), (geomeanRatio(newMed, baseMed)-1)*100))
	return out
}

func medianOf(x []float64) float64 {
	xs := append([]float64(nil), x...)
	sort.Float64s(xs)
	n := len(xs)
	if n%2 == 1 {
		return xs[n/2]
	}
	return (xs[n/2-1] + xs[n/2]) / 2
}
