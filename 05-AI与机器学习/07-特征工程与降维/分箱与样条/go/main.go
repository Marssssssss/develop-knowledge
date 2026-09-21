package main

// 分箱与样条:6 组实验(KBins 见 bins.go,SplineTransformer 见 spline.go)。
// 跑法: go run .
// 与 python/main.py 同题、同口径;本 demo 全确定性,无随机源。

import (
	"fmt"
	"math"
)

// docX 是 sklearn 文档里 KBinsDiscretizer 的例子数据
var docX = [][]float64{
	{-2, 1, -4, -1},
	{-1, 2, -3, -0.5},
	{0, 3, -2, 0.5},
	{1, 4, -1, 2},
}

func fmtVal(v float64) string {
	if math.IsInf(v, 1) {
		return "+inf"
	}
	if math.IsInf(v, -1) {
		return "-inf"
	}
	return fmt.Sprintf("%.4f", v)
}

func fmtVec(v []float64) string {
	s := "["
	for i, x := range v {
		if i > 0 {
			s += " "
		}
		s += fmtVal(x)
	}
	return s + "]"
}

func fmtCol(X [][]float64, j int) string {
	s := "["
	for i, row := range X {
		if i > 0 {
			s += " "
		}
		s += fmt.Sprintf("%d", int(row[j]))
	}
	return s + "]"
}

func fmtSum(v []float64) float64 {
	t := 0.0
	for _, x := range v {
		t += x
	}
	return t
}

func rowsToVec(X [][]float64, j int) []float64 {
	out := make([]float64, len(X))
	for i, r := range X {
		out[i] = r[j]
	}
	return out
}

func uniformData() [][]float64 {
	grid := Linspace(0, 1, 11)
	out := make([][]float64, len(grid))
	for i, v := range grid {
		out[i] = []float64{v}
	}
	return out
}

// ------------------------------------------------------------------ E1

func expStrategies() {
	fmt.Println("[E1] 三种 strategy(n_bins=3, data = sklearn 文档例)")
	for _, st := range []string{"uniform", "quantile", "kmeans"} {
		kb := NewKBins(3, "ordinal", st, "averaged_inverted_cdf").Fit(docX)
		codes := kb.Transform(docX)
		fmt.Printf("     %-9s 列0 边界 = %s\n", st, fmtVec(kb.BinEdges[0]))
		fmt.Printf("              列0 编码 = %s\n", fmtCol(codes, 0))
	}
	fmt.Println("     文档给出的 uniform 编码 = [0 1 2 2],边界 = [-2 -1 0 1]")

	fmt.Println("     三种 quantile_method 的边界差(单列 [1,2,3],n_bins=3):")
	tri := [][]float64{{1}, {2}, {3}}
	for _, m := range []string{"linear", "averaged_inverted_cdf", "inverted_cdf"} {
		kb := NewKBins(3, "ordinal", "quantile", m).Fit(tri)
		note := ""
		if kb.NBinsPer[0] != 3 {
			note = fmt.Sprintf("  <- 窄箱被剔除,箱数掉到 %d", kb.NBinsPer[0])
		}
		fmt.Printf("       %-22s -> %s   n_bins_=%v%s\n", m,
			fmtVec(kb.BinEdges[0]), kb.NBinsPer, note)
	}
}

// ------------------------------------------------------------------ E2

func expBoundary() {
	fmt.Println("[E2] 边界归属与越界折叠(n_bins=4 uniform,x = 0,0.25,...,1)")
	kb := NewKBins(4, "ordinal", "uniform", "linear").Fit(uniformData())
	fmt.Printf("     边界 = %s\n", fmtVec(kb.BinEdges[0]))
	probes := []float64{-5, -0.001, 0, 0.25, 0.5, 0.75, 1, 1.001, 9}
	rows := make([][]float64, len(probes))
	for i, p := range probes {
		rows[i] = []float64{p}
	}
	codes := kb.Transform(rows)
	for i, p := range probes {
		tag := ""
		if p == 0.25 || p == 0.5 || p == 0.75 {
			tag = "  <- 恰在边界上,归上一箱(bisect_right)"
		} else if p < 0 || p > 1 {
			tag = "  <- 越界,折进端点箱"
		}
		fmt.Printf("     x = %-8s -> 箱 %d%s\n", fmtVal(p), int(codes[i][0]), tag)
	}
}

// ------------------------------------------------------------------ E3

func expDegenerate() {
	fmt.Println("[E3] 退化情形")
	xc := [][]float64{{1, 1}, {1, 2}, {1, 3}}
	kb := NewKBins(3, "ordinal", "quantile", "averaged_inverted_cdf").Fit(xc)
	fmt.Printf("     常量列(列0 恒为 1):边界 = %s,n_bins_ = %v,transform 全 0\n",
		fmtVec(kb.BinEdges[0]), kb.NBinsPer)
	fmt.Println("       官方警告文案:Feature 0 is constant and will be replaced with 0.")
	fmt.Println("       代价:inverse_transform 用箱中心 (edges[k]+edges[k+1])/2 还原,")
	fmt.Println("             而 (-inf + +inf)/2 = nan —— 常量列反变换得到 nan 而不是 1。")

	xd := [][]float64{{0}, {0}, {0}, {1e-12}, {1}, {2}, {3}}
	for _, m := range []string{"linear", "averaged_inverted_cdf"} {
		kb := NewKBins(5, "ordinal", "quantile", m).Fit(xd)
		raw := Percentiles(rowsToVec(xd, 0), Linspace(0, 100, 6), m)
		fmt.Printf("     7 点含 1e-12,n_bins=5,method=%-22s\n", m)
		fmt.Printf("       未过滤分位数 = %s\n", fmtVec(raw))
		fmt.Printf("       过滤后边界   = %s  n_bins_ = %v\n",
			fmtVec(kb.BinEdges[0]), kb.NBinsPer)
	}
	kept, _ := DropNarrowEdges([]float64{0, 0, 0, 1, 2, 3})
	fmt.Printf("     DropNarrowEdges([0 0 0 1 2 3]) -> %s(丢掉了 %d 个)\n",
		fmtVec(kept), 6-len(kept))
}

// ------------------------------------------------------------------ E4

func expSplineKnots() {
	fmt.Println("[E4] B-spline 基(n_knots=4,x = linspace(0,1,6))")
	xs := toCols(Linspace(0, 1, 6))
	for _, deg := range []int{1, 2, 3} {
		st := NewSpline(4, deg, "error", true).Fit(xs)
		rows := st.Transform(xs)
		t := st.KnotVecs[0]
		fmt.Printf("     degree=%d  base 结 = %s\n", deg, fmtVec(t[deg:len(t)-deg]))
		fmt.Printf("               完整结 t = %s\n", fmtVec(t))
		fmt.Printf("               n_features_out_ = %d = n_knots + degree - 1 = %d\n",
			st.NFeaturesOut, 4+deg-1)
		lo, hi := 1.0, 1.0
		for _, r := range rows {
			s := fmtSum(r)
			if s < lo {
				lo = s
			}
			if s > hi {
				hi = s
			}
		}
		fmt.Printf("               每行之和 ∈ [%.12f, %.12f](单位分解 => 隐含截距列)\n", lo, hi)
	}
	fmt.Println("     端外结不重复首末结,而是沿用首/末两结间距 dist = 1/3")
	fmt.Println("     (Eilers & Marx 的建议,官方注释里明确否掉了 np.tile 写法)")
}

// ------------------------------------------------------------------ E5

func expExtrapolation() {
	fmt.Println("[E5] extrapolation 对照(degree=3,n_knots=4,训练域 [0,1];列 0 的基)")
	xs := toCols(Linspace(0, 1, 6))
	probes := []float64{-0.5, 0.5, 1.5}
	for _, ext := range []string{"constant", "linear", "continue", "periodic"} {
		st := NewSpline(4, 3, ext, true).Fit(xs)
		fmt.Printf("     %-9s n_features_out_=%d\n", ext, st.NFeaturesOut)
		for _, p := range probes {
			row := st.Transform([][]float64{{p}})[0]
			fmt.Printf("       x=%-5s -> %s  sum=%.4f\n",
				fmtVal(p), fmtVec(row), fmtSum(row))
		}
	}
	st := NewSpline(4, 3, "error", true).Fit(xs)
	msg := catchPanic(func() { st.Transform([][]float64{{-0.5}}) })
	if msg == "" {
		fmt.Println("     error     未报错(不应发生)")
	} else {
		fmt.Printf("     error     x=-0.5 抛错:%s\n", msg)
	}
	fmt.Println("     要点:constant / linear 的越界行仍满足「行和 = 1」;")
	fmt.Println("           continue 是多项式延拓,行和也保持 1(仍落在基的仿射包上);")
	fmt.Println("           error 只在越界时报错,域内与 constant 完全相同。")
}

func catchPanic(f func()) (msg string) {
	defer func() {
		if r := recover(); r != nil {
			msg = fmt.Sprint(r)
		}
	}()
	f()
	return ""
}

// ------------------------------------------------------------------ E6

func expPeriodicAndQuantile() {
	fmt.Println("[E6] periodic 与 quantile 结")
	grid := Linspace(0, 2*math.Pi, 9)
	xp := toCols(grid)
	for _, nk := range []int{4, 5} {
		st := NewSpline(nk, 2, "periodic", true).Fit(xp)
		fmt.Printf("     periodic n_knots=%d -> n_features_out_=%d (= n_knots - 1)\n",
			nk, st.NFeaturesOut)
		fmt.Printf("       t = %s\n", fmtVec(st.KnotVecs[0]))
	}
	fmt.Println("       周期 = base[-1] - base[0] = 2*pi,端外结按周期平移而来")

	xq := [][]float64{{0}, {0}, {0}, {1}, {2}, {3}, {10}}
	for _, kk := range []string{"uniform", "quantile"} {
		st := NewSplineKnots(5, 2, kk, "constant", true).Fit(xq)
		fmt.Printf("     knots=%-8s t = %s\n", kk, fmtVec(st.KnotVecs[0]))
	}
	fmt.Println("       quantile 结用 np.nanpercentile(**默认 linear**),与 KBinsDiscretizer")
	fmt.Println("       默认的 averaged_inverted_cdf 不是同一个口径;base 结允许重复(见上)")
}

func toCols(v []float64) [][]float64 {
	out := make([][]float64, len(v))
	for i, x := range v {
		out[i] = []float64{x}
	}
	return out
}

func main() {
	expStrategies()
	fmt.Println()
	expBoundary()
	fmt.Println()
	expDegenerate()
	fmt.Println()
	expSplineKnots()
	fmt.Println()
	expExtrapolation()
	fmt.Println()
	expPeriodicAndQuantile()
}
