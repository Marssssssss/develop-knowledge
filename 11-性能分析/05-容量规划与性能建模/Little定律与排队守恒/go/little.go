// Package little 实现 Little 定律与 M/M/1 稳态量（与 python/little.py 同口径）。
package little

import (
	"errors"
	"math"
)

// LittleL 已知 lambda 与 W 求 L：L = lambda * W。
func LittleL(lambda, W float64) float64 { return lambda * W }

// Utilization 利用率定律 U = lambda * S / m。
func Utilization(lambda, S float64, servers int) float64 {
	if servers <= 0 {
		panic(errors.New("servers must be positive"))
	}
	return lambda * S / float64(servers)
}

// MM1 单服务台指数排队模型，构造时校验稳定性。
type MM1 struct {
	Lambda float64
	Mu     float64
}

// NewMM1 要求 0 < lambda < mu。
func NewMM1(lambda, mu float64) (*MM1, error) {
	if lambda <= 0 || mu <= 0 {
		return nil, errors.New("lambda and mu must be positive")
	}
	if lambda >= mu {
		return nil, errors.New("unstable: lambda must be < mu")
	}
	return &MM1{Lambda: lambda, Mu: mu}, nil
}

// Rho 利用率 lambda/mu。
func (m *MM1) Rho() float64 { return m.Lambda / m.Mu }

// S 平均服务时间 1/mu。
func (m *MM1) S() float64 { return 1.0 / m.Mu }

// L 系统内平均数量 rho/(1-rho)。
func (m *MM1) L() float64 { return m.Rho() / (1.0 - m.Rho()) }

// Lq 平均排队长度 rho^2/(1-rho)。
func (m *MM1) Lq() float64 {
	r := m.Rho()
	return r * r / (1.0 - r)
}

// W 平均逗留时间 1/(mu-lambda)。
func (m *MM1) W() float64 { return 1.0 / (m.Mu - m.Lambda) }

// Wq 平均排队等待 rho/(mu-lambda)。
func (m *MM1) Wq() float64 { return m.Rho() / (m.Mu - m.Lambda) }

// WaitMultiplier W/S = 1/(1-rho)。
func (m *MM1) WaitMultiplier() float64 { return 1.0 / (1.0 - m.Rho()) }

// StateProb pi_k = (1-rho) rho^k。
func (m *MM1) StateProb(k int) float64 {
	return (1.0 - m.Rho()) * math.Pow(m.Rho(), float64(k))
}

// DeadQueue 队列已有 depth 个元素、以 drainRate 个/秒排空、调用方超时 timeout 秒。
// 第 i 个元素在 t=i/drainRate 完成，完成时刻严格小于 timeout 才算有效。
func DeadQueue(depth int, drainRate, timeout float64) (int, int, error) {
	if depth <= 0 {
		return 0, 0, errors.New("depth must be positive")
	}
	if drainRate <= 0 {
		return 0, 0, errors.New("drainRate must be positive")
	}
	useful := 0
	for useful < depth && float64(useful)/drainRate < timeout {
		useful++
	}
	return depth - useful, useful, nil
}

// PoolSize 由 L = lambda*W 反推并发额度。
func PoolSize(lambda, W, headroom float64) int {
	return int(math.Ceil(lambda * W * headroom))
}

// ThroughputCeiling workers 个一次只处理一个请求的 worker 的吞吐上限。
func ThroughputCeiling(S float64, workers int) float64 {
	return float64(workers) / S
}
