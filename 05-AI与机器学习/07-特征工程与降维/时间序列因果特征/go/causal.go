package main

// demo 516 Go 侧:因果性判定。与 python/features.py 逐条对应。
//
// 判据只有一条:**第 t 行的特征不得依赖 x[j] (j > t)**。
// "只用历史" ≠ "不碰当期":在 y_t = x_{t+1} 的设定下 x[t] 是决策时刻已知量,
// 含它不构成未来依赖 —— 它是**对齐**问题,不是泄漏。

import "math"

// ------------------------------------------------------------------ 三档构造

// CausalRollingMean 右端停在 t-shiftBy,shiftBy >= 1 才是因果的。
func CausalRollingMean(x []float64, window, shiftBy int) []float64 {
	return Shift(RollingMean(x, window, mpDefault, false, ""), shiftBy)
}

// ShiftedRollingMean 原样滚动,右端落在 t,含当期。对齐差 1 格。
func ShiftedRollingMean(x []float64, window int) []float64 {
	return RollingMean(x, window, mpDefault, false, "")
}

// LeakyRollingMeanCenter center=True 的窗口横跨当期两侧,第 t 行含 x[t+1] —— 真泄漏。
func LeakyRollingMeanCenter(x []float64, window int) []float64 {
	return RollingMean(x, window, mpDefault, true, "")
}

// LeakyRollingMeanShiftNeg shift(-1) 把未来的窗口搬到现在 —— 真泄漏。
func LeakyRollingMeanShiftNeg(x []float64, window int) []float64 {
	return Shift(RollingMean(x, window, mpDefault, false, ""), -1)
}

// Lag 纯滞后。
func Lag(x []float64, p int) []float64 { return Shift(x, p) }

// ExpandingMeanCausal 扩展窗的因果版。
func ExpandingMeanCausal(x []float64, shiftBy int) []float64 {
	return Shift(ExpandingMean(x, 1), shiftBy)
}

// ------------------------------------------------------------------ 支持集探针

// SupportSet 扰动 x[j],返回使"第 i 行发生变化"的 j(升序)。
// 第 i 行本身缺失(NaN)时返回 nil,表示"该行无值,不计入"。
// 返回升序切片而不是 map:便于与 python 侧排序后逐项比较。
func SupportSet(fn func([]float64) []float64, i, n int, delta, tol float64) []int {
	x := FixtureSeries(n)
	base := fn(x)
	if math.IsNaN(base[i]) {
		return nil
	}
	out := []int{}
	for j := 0; j < n; j++ {
		x2 := append([]float64(nil), x...)
		x2[j] += delta
		v := fn(x2)
		if math.IsNaN(v[i]) || math.Abs(v[i]-base[i]) > tol {
			out = append(out, j)
		}
	}
	return out
}

// FutureDepCount 支持集里 > i 的元素个数 —— 这就是"未来依赖"的度量。
func FutureDepCount(s []int, i int) int {
	c := 0
	for _, j := range s {
		if j > i {
			c++
		}
	}
	return c
}

// FuturePairs 列出所有"第 t 行被 x[j] 牵动"(t < j)的对,升序。
func FuturePairs(fn func([]float64) []float64, x []float64, tol float64) [][2]int {
	base := fn(x)
	out := [][2]int{}
	for j := range x {
		x2 := append([]float64(nil), x...)
		x2[j] += 1.0
		v2 := fn(x2)
		for t := 0; t < j; t++ {
			if math.IsNaN(base[t]) || math.IsNaN(v2[t]) {
				continue
			}
			if math.Abs(v2[t]-base[t]) > tol {
				out = append(out, [2]int{t, j})
			}
		}
	}
	return out
}

// AuditStats 是三轴聚合结果。
type AuditStats struct {
	FutureBad, FutureTotal int // 轴一:依赖未来 x[j] (j > t)
	CurHit, CurTotal       int // 轴二:扰动 x[t] 是否影响第 t 行(含当期)
	NextHit, NextTotal     int // 轴三:扰动 x[t+1] 是否影响第 t 行
}

// Audit 三轴机械探针。不需要标签、不需要切分、不需要跑模型。
// 轴一只能查 j > t,所以它**查不出**"错位"那种右端落在 t 的情况 —— 这是定义如此。
func Audit(fn func([]float64) []float64, n int, seeds []uint32, tol float64) AuditStats {
	var st AuditStats
	for _, sd := range seeds {
		x := MakeAR1(n, 0.9, 1.0, sd)
		base := fn(x)
		for j := 0; j < n; j++ {
			x2 := append([]float64(nil), x...)
			x2[j] += 1.0
			v2 := fn(x2)
			for t := 0; t < j; t++ {
				if math.IsNaN(base[t]) || math.IsNaN(v2[t]) {
					continue
				}
				st.FutureTotal++
				if math.Abs(v2[t]-base[t]) > tol {
					st.FutureBad++
				}
			}
			if !math.IsNaN(base[j]) && !math.IsNaN(v2[j]) {
				st.CurTotal++
				if math.Abs(v2[j]-base[j]) > tol {
					st.CurHit++
				}
			}
		}
		for t := 0; t < n-1; t++ {
			x2 := append([]float64(nil), x...)
			x2[t+1] += 1.0
			v2 := fn(x2)
			if math.IsNaN(base[t]) || math.IsNaN(v2[t]) {
				continue
			}
			st.NextTotal++
			if math.Abs(v2[t]-base[t]) > tol {
				st.NextHit++
			}
		}
	}
	return st
}

// ------------------------------------------------------------------ 矩阵装配

// BuildMatrix 用**同一套**行掩码取行,否则因果版与泄漏版会因缺失位置不同而失去可比性。
func BuildMatrix(cols [][]float64) ([]int, [][]float64) {
	n := len(cols[0])
	rows := []int{}
	for i := 0; i < n; i++ {
		ok := true
		for _, c := range cols {
			if math.IsNaN(c[i]) {
				ok = false
				break
			}
		}
		if ok {
			rows = append(rows, i)
		}
	}
	X := make([][]float64, len(rows))
	for k, i := range rows {
		row := make([]float64, 0, len(cols)+1)
		row = append(row, 1.0)
		for _, c := range cols {
			row = append(row, c[i])
		}
		X[k] = row
	}
	return rows, X
}

// StandardizeFit 返回第 j 列(不含截距)的均值与**总体**标准差。
func StandardizeFit(X [][]float64, j int) (float64, float64) {
	col := make([]float64, len(X))
	for i := range X {
		col[i] = X[i][j]
	}
	mu := meanF(col)
	s := 0.0
	for _, v := range col {
		d := v - mu
		s += d * d
	}
	return mu, math.Sqrt(s / float64(len(col)))
}
