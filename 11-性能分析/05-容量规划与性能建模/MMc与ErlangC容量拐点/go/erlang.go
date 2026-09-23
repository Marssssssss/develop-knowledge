// Package queuemodel 实现 Erlang B/C 与 M/M/c 稳态量（与 python/queuemodel.py 同口径）。
package queuemodel

import (
	"errors"
	"math"
)

func factorial(n int) float64 {
	f := 1.0
	for i := 2; i <= n; i++ {
		f *= float64(i)
	}
	return f
}

// ErlangB 阻塞概率的闭式：B(E,m) = (E^m/m!) / Σ_{i=0}^{m} E^i/i!
func ErlangB(E float64, m int) (float64, error) {
	if E < 0 || m < 0 {
		return 0, errors.New("E and m must be non-negative")
	}
	num := math.Pow(E, float64(m)) / factorial(m)
	den := 0.0
	for i := 0; i <= m; i++ {
		den += math.Pow(E, float64(i)) / factorial(i)
	}
	return num / den, nil
}

// ErlangBRecursive 稳定递推：B(E,0)=1, B(E,j) = E*B(E,j-1)/(E*B(E,j-1)+j)
func ErlangBRecursive(E float64, m int) (float64, error) {
	if E < 0 || m < 0 {
		return 0, errors.New("E and m must be non-negative")
	}
	b := 1.0
	for j := 1; j <= m; j++ {
		b = E * b / (E * b + float64(j))
	}
	return b, nil
}

// ErlangC 等待概率的闭式（要求 E < m）。
func ErlangC(E float64, m int) (float64, error) {
	if E < 0 || m <= 0 {
		return 0, errors.New("E must be non-negative and m positive")
	}
	if E >= float64(m) {
		return 0, errors.New("unstable: E must be < m")
	}
	tail := math.Pow(E, float64(m)) / factorial(m) * (float64(m) / (float64(m) - E))
	head := 0.0
	for i := 0; i < m; i++ {
		head += math.Pow(E, float64(i)) / factorial(i)
	}
	return tail / (head + tail), nil
}

// ErlangCViaB 关系式：C = B / (1 − ρ(1 − B))，ρ = E/m。
func ErlangCViaB(E float64, m int) (float64, error) {
	b, err := ErlangBRecursive(E, m)
	if err != nil {
		return 0, err
	}
	rho := E / float64(m)
	return b / (1.0 - rho*(1.0-b)), nil
}

// MMC 是 c 个并列服务台的排队模型。
type MMC struct {
	Lambda float64
	Mu     float64
	C      int
}

// NewMMC 要求 0 < lambda < c*mu。
func NewMMC(lambda, mu float64, c int) (*MMC, error) {
	if lambda <= 0 || mu <= 0 || c <= 0 {
		return nil, errors.New("lambda, mu must be positive and c positive")
	}
	if lambda >= mu*float64(c) {
		return nil, errors.New("unstable: lambda must be < c*mu")
	}
	return &MMC{Lambda: lambda, Mu: mu, C: c}, nil
}

// OfferedLoad E = lambda/mu（erlang）。
func (m *MMC) OfferedLoad() float64 { return m.Lambda / m.Mu }

// Rho 每服务台利用率 lambda/(c*mu)。
func (m *MMC) Rho() float64 { return m.Lambda / (float64(m.C) * m.Mu) }

// WaitingProb Erlang C。
func (m *MMC) WaitingProb() (float64, error) {
	return ErlangC(m.OfferedLoad(), m.C)
}

// Pi0 空系统概率。
func (m *MMC) Pi0() float64 {
	a := float64(m.C) * m.Rho()
	head := 0.0
	for k := 0; k < m.C; k++ {
		head += math.Pow(a, float64(k)) / factorial(k)
	}
	tail := math.Pow(a, float64(m.C)) / factorial(m.C) / (1.0 - m.Rho())
	return 1.0 / (head + tail)
}

// Wq 平均排队等待 = C(c, lambda/mu) / (c*mu − lambda)。
func (m *MMC) Wq() (float64, error) {
	c, err := m.WaitingProb()
	if err != nil {
		return 0, err
	}
	return c / (float64(m.C)*m.Mu - m.Lambda), nil
}

// W 平均逗留时间 = Wq + 1/mu。
func (m *MMC) W() (float64, error) {
	wq, err := m.Wq()
	if err != nil {
		return 0, err
	}
	return wq + 1.0/m.Mu, nil
}
