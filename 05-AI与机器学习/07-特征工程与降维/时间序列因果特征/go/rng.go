package main

// demo 516 Go 侧:确定性随机源与合成序列,与 python/rng.py 同常数。
// 注意:Go 的无符号整型乘法按 2^32 取模回绕,故这里不需要 Python 那层 & 0xFFFFFFFF。

import "math"

// Rng 是 Numerical Recipes 的 LCG。
type Rng struct {
	S uint32
}

func (r *Rng) U32() uint32 {
	r.S = 1664525*r.S + 1013904223
	return r.S
}

func (r *Rng) Uniform() float64 {
	return float64(r.U32()) / 4294967296.0
}

// Normal 用 Box-Muller;u1 取下限 1e-300 避免 log(0)。
func (r *Rng) Normal() float64 {
	u1 := r.Uniform()
	if u1 < 1e-300 {
		u1 = 1e-300
	}
	u2 := r.Uniform()
	return math.Sqrt(-2.0*math.Log(u1)) * math.Cos(2.0*math.Pi*u2)
}

// MakeAR1 造 x_t = phi*x_{t-1} + sigma*eps_t。
func MakeAR1(n int, phi, sigma float64, seed uint32) []float64 {
	r := &Rng{S: seed}
	out := make([]float64, n)
	prev := 0.0
	for t := 0; t < n; t++ {
		prev = phi*prev + sigma*r.Normal()
		out[t] = prev
	}
	return out
}

// FixtureSeries 是给支持集探针用的**确定性**序列(不含随机成分,便于复现)。
func FixtureSeries(n int) []float64 {
	out := make([]float64, n)
	for j := 0; j < n; j++ {
		out[j] = float64((j*11)%5)*1.3 + float64(j)*0.41
	}
	return out
}
