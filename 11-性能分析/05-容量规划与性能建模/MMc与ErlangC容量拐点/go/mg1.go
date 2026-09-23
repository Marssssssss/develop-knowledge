package queuemodel

import (
	"errors"
	"math"
)

// MG1Wq Pollaczek–Khinchine 排队等待：
// W' = (rho + lambda*mu*Var(S)) / (2(mu − lambda))
func MG1Wq(lambda, mu, varS float64) (float64, error) {
	if lambda <= 0 || mu <= 0 || varS < 0 {
		return 0, errors.New("invalid parameters")
	}
	if lambda >= mu {
		return 0, errors.New("unstable: lambda must be < mu")
	}
	rho := lambda / mu
	return (rho + lambda*mu*varS) / (2 * (mu - lambda)), nil
}

// MG1W 平均逗留时间 = Wq + 1/mu。
func MG1W(lambda, mu, varS float64) (float64, error) {
	wq, err := MG1Wq(lambda, mu, varS)
	if err != nil {
		return 0, err
	}
	return wq + 1.0/mu, nil
}

// MM1K 是容量为 K（含正在服务的那一个）的有限缓冲单服务台。
type MM1K struct {
	Lambda float64
	Mu     float64
	K      int
}

// NewMM1K 校验参数。
func NewMM1K(lambda, mu float64, K int) (*MM1K, error) {
	if lambda <= 0 || mu <= 0 || K <= 0 {
		return nil, errors.New("invalid parameters")
	}
	return &MM1K{Lambda: lambda, Mu: mu, K: K}, nil
}

// Rho lambda/mu（有限容量下允许 >= 1）。
func (m *MM1K) Rho() float64 { return m.Lambda / m.Mu }

// Pi0 空系统概率；rho==1 时等比和退化为 K+1。
func (m *MM1K) Pi0() float64 {
	rho := m.Rho()
	if math.Abs(rho-1.0) < 1e-15 {
		return 1.0 / float64(m.K+1)
	}
	return (1.0 - rho) / (1.0 - math.Pow(rho, float64(m.K+1)))
}

// Pi 状态概率 pi_k = pi0 * rho^k（k > K 时为 0）。
func (m *MM1K) Pi(k int) float64 {
	if k > m.K || k < 0 {
		return 0.0
	}
	return m.Pi0() * math.Pow(m.Rho(), float64(k))
}

// PBlock 客满概率 = pi_K。
func (m *MM1K) PBlock() float64 { return m.Pi(m.K) }

// LambdaA 有效到达率 = lambda*(1 − p_K)。
func (m *MM1K) LambdaA() float64 { return m.Lambda * (1.0 - m.PBlock()) }

// Throughput 离开率 = mu*(1 − pi_0)。
func (m *MM1K) Throughput() float64 { return m.Mu * (1.0 - m.Pi0()) }

// L 系统内平均数量。
func (m *MM1K) L() float64 {
	s := 0.0
	for k := 0; k <= m.K; k++ {
		s += float64(k) * m.Pi(k)
	}
	return s
}
