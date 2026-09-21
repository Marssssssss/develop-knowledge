package main

// demo 516 Go 侧:滚动窗口的**对齐内核**。
//
// 与 python/rolling.py 逐条对应(python 侧已与 pandas 3.0.6 对拍到 1439/1439),
// 唯一口径差异:Python 用 None 表示"不足 min_periods",Go 没有 None,
// 统一用 math.NaN() 作缺失标记 —— 因此"结果本身是真 NaN"与"窗口不足"不可区分,
// 这在 rolling_mean/rolling_std 上不会出问题,但**不要**把这个内核喂给本来就含 NaN 的序列。
//
// 规则(由 pandas 3.0.6 实测反推):
//   * 基准窗口 closed='right',标签 i 覆盖 [i-window+1, i];
//   * center=True 只把**末端**右移 (window-1)//2,故偶数窗偏左;
//   * closed 的四个取值各自再挪 ≤1 格;
//   * min_periods 默认取 window;std 默认 ddof=1。

import "math"

// mpDefault 表示"未指定 min_periods",等价于 Python 的 None。
const mpDefault = -1

func windowBounds(i, window int, center bool, closed string) (int, int) {
	end := i
	if center {
		end = i + (window-1)/2
	}
	start := end - window + 1
	switch closed {
	case "", "right":
		return start, end
	case "left":
		return start - 1, end - 1
	case "both":
		return start - 1, end
	case "neither":
		return start, end - 1
	}
	panic("closed 只能是 \"\" / right / left / both / neither")
}

func sumF(w []float64) float64 {
	s := 0.0
	for _, v := range w {
		s += v
	}
	return s
}

func meanF(w []float64) float64 {
	if len(w) == 0 {
		return math.NaN()
	}
	return sumF(w) / float64(len(w))
}

func minF(w []float64) float64 {
	if len(w) == 0 {
		return math.NaN()
	}
	m := w[0]
	for _, v := range w[1:] {
		if v < m {
			m = v
		}
	}
	return m
}

func maxF(w []float64) float64 {
	if len(w) == 0 {
		return math.NaN()
	}
	m := w[0]
	for _, v := range w[1:] {
		if v > m {
			m = v
		}
	}
	return m
}

// stdF 返回带 ddof 的样本标准差闭包。len(w) <= ddof 时分母非正 → NaN
// (对应 python 侧 ZeroDivisionError 被吞成 None)。
func stdF(ddof int) func([]float64) float64 {
	return func(w []float64) float64 {
		if len(w) <= ddof {
			return math.NaN()
		}
		m := meanF(w)
		s := 0.0
		for _, v := range w {
			d := v - m
			s += d * d
		}
		return math.Sqrt(s / float64(len(w)-ddof))
	}
}

// RollingApply 是唯一入口:其余滚动函数都是它的薄封装。
func RollingApply(x []float64, window int, fn func([]float64) float64,
	mp int, center bool, closed string) []float64 {
	if window < 1 {
		panic("window 必须 >= 1")
	}
	n := len(x)
	if mp == mpDefault {
		mp = window
	}
	if mp > window {
		panic("min_periods must be <= window")
	}
	out := make([]float64, n)
	for i := range out {
		out[i] = math.NaN()
	}
	if n == 0 {
		return out
	}
	for i := 0; i < n; i++ {
		lo, hi := windowBounds(i, window, center, closed)
		a, b := lo, hi
		if a < 0 {
			a = 0
		}
		if b > n-1 {
			b = n - 1
		}
		cnt := 0
		if b >= a {
			cnt = b - a + 1
		}
		if cnt < mp {
			continue
		}
		// min_periods=0 时 pandas 仍对空窗口求值:sum→0,mean/min/max→NaN。
		// 此处 fn(空)=相应值,NaN 会被下面的判定挡掉。
		v := fn(append([]float64(nil), x[a:b+1]...))
		if math.IsNaN(v) {
			continue
		}
		out[i] = v
	}
	return out
}

func RollingSum(x []float64, window, mp int, center bool, closed string) []float64 {
	return RollingApply(x, window, sumF, mp, center, closed)
}

func RollingMean(x []float64, window, mp int, center bool, closed string) []float64 {
	return RollingApply(x, window, meanF, mp, center, closed)
}

func RollingMin(x []float64, window, mp int, center bool, closed string) []float64 {
	return RollingApply(x, window, minF, mp, center, closed)
}

func RollingMax(x []float64, window, mp int, center bool, closed string) []float64 {
	return RollingApply(x, window, maxF, mp, center, closed)
}

func RollingStd(x []float64, window, ddof, mp int, center bool, closed string) []float64 {
	return RollingApply(x, window, stdF(ddof), mp, center, closed)
}

// Shift:pandas 口径,正数向后挪(新值来自过去),负数向前挪(会读到未来)。
func Shift(x []float64, periods int) []float64 {
	n := len(x)
	out := make([]float64, n)
	for i := range out {
		out[i] = math.NaN()
	}
	for i := 0; i < n; i++ {
		j := i - periods
		if j >= 0 && j < n {
			out[i] = x[j]
		}
	}
	return out
}

func Diff(x []float64, periods int) []float64 {
	n := len(x)
	out := make([]float64, n)
	s := Shift(x, periods)
	for i := 0; i < n; i++ {
		if math.IsNaN(s[i]) {
			out[i] = math.NaN()
			continue
		}
		out[i] = x[i] - s[i]
	}
	return out
}

func PctChange(x []float64, periods int) []float64 {
	n := len(x)
	out := make([]float64, n)
	s := Shift(x, periods)
	for i := 0; i < n; i++ {
		if math.IsNaN(s[i]) || s[i] == 0.0 {
			out[i] = math.NaN() // 分母是**前一期**,故 0 出现在 s[i]==0 处
			continue
		}
		out[i] = (x[i] - s[i]) / s[i]
	}
	return out
}

// ExpandingApply 覆盖 [0, i],默认 min_periods=1,即**含当期**。
func ExpandingApply(x []float64, fn func([]float64) float64, minPeriods int) []float64 {
	n := len(x)
	out := make([]float64, n)
	for i := range out {
		out[i] = math.NaN()
	}
	for i := 0; i < n; i++ {
		if i+1 < minPeriods {
			continue
		}
		v := fn(append([]float64(nil), x[:i+1]...))
		if math.IsNaN(v) {
			continue
		}
		out[i] = v
	}
	return out
}

func ExpandingMean(x []float64, minPeriods int) []float64 {
	return ExpandingApply(x, meanF, minPeriods)
}

func ExpandingSum(x []float64, minPeriods int) []float64 {
	return ExpandingApply(x, sumF, minPeriods)
}
