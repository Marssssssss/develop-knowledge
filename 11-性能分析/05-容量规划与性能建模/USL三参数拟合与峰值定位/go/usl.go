// Package uslmodel 实现 Universal Scalability Law 的模型与峰值定位。
//
// 公式口径来自 Neil J. Gunther《How to Quantify Scalability》与 R 包 usl 的源码：
//
//	X(N)  = γN / [1 + α(N−1) + βN(N−1)]
//	Nmax  = √[(1 − α)/β]                 （β = 0 时峰值在无穷远）
//	Nopt  = |1/α|
//	Xlim  = γ · Nopt                     （Amdahl 渐近线）
package uslmodel

import (
	"errors"
	"math"
)

// Model 是拟合出的三参数 USL 模型。
type Model struct {
	Gamma float64
	Alpha float64
	Beta  float64
}

// Denom 分母 1 + α(N−1) + βN(N−1)。
func (m Model) denom(N float64) float64 {
	return 1.0 + m.Alpha*(N-1.0) + m.Beta*N*(N-1.0)
}

// X 绝对吞吐 X(N)。
func (m Model) X(N float64) (float64, error) {
	d := m.denom(N)
	if d <= 0 {
		return 0, errors.New("non-positive denominator")
	}
	return m.Gamma * N / d, nil
}

// C 相对容量 C(N) = N / [1 + α(N−1) + βN(N−1)]。
func (m Model) C(N float64) float64 { return N / m.denom(N) }

// HasPeak β > 0 才存在有限峰值。
func (m Model) HasPeak() bool { return m.Beta > 0 }

// Peak 返回 (Nmax, Xmax)；β = 0 时 Nmax 为 +Inf、Xmax 取 Amdahl 渐近线。
func (m Model) Peak() (float64, float64) {
	if !m.HasPeak() {
		if m.Alpha <= 0 {
			return math.Inf(1), math.Inf(1)
		}
		return math.Inf(1), m.Gamma / m.Alpha
	}
	nmax := math.Sqrt((1.0 - m.Alpha) / m.Beta)
	xmax, _ := m.X(nmax)
	return nmax, xmax
}

// Optimal 返回 (Nopt, Xopt)，Nopt = |1/α|。
func (m Model) Optimal() (float64, float64) {
	if m.Alpha <= 0 {
		return math.Inf(1), math.Inf(1)
	}
	nopt := 1.0 / m.Alpha
	xopt, _ := m.X(nopt)
	return nopt, xopt
}

// Limit Amdahl 渐近线 Xlim = γ · Nopt = γ/α。
func (m Model) Limit() float64 {
	if m.Alpha <= 0 {
		return math.Inf(1)
	}
	return m.Gamma / m.Alpha
}

// Efficiency R 包 efficiency() 的口径：观测值 / (γN)。
func (m Model) Efficiency(N, observed float64) float64 {
	return observed / (m.Gamma * N)
}

// SSE 残差平方和。
func (m Model) SSE(data [][2]float64) (float64, error) {
	s := 0.0
	for _, p := range data {
		x, err := m.X(p[0])
		if err != nil {
			return 0, err
		}
		s += (p[1] - x) * (p[1] - x)
	}
	return s, nil
}

// ResidualStdError sqrt(SSE / df)，df = n − 3。
func (m Model) ResidualStdError(data [][2]float64) (float64, error) {
	df := len(data) - 3
	if df <= 0 {
		return 0, errors.New("not enough data points")
	}
	s, err := m.SSE(data)
	if err != nil {
		return 0, err
	}
	return math.Sqrt(s / float64(df)), nil
}

// Raytracer 是 R 包 usl 自带的 BRL-CAD 光线追踪数据集。
var Raytracer = [][2]float64{
	{1, 20}, {4, 78}, {8, 130}, {12, 170}, {16, 190}, {20, 200},
	{24, 210}, {28, 230}, {32, 260}, {48, 280}, {64, 310},
}

// SpecSDM91 是 R 包 usl 自带的 SPEC SDM91 数据集。
var SpecSDM91 = [][2]float64{
	{1, 64.9}, {18, 995.9}, {36, 1652.4}, {72, 1853.2},
	{108, 1828.9}, {144, 1775.0}, {216, 1702.2},
}
