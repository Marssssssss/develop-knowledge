// 多重比较校正的核心:确定性随机源与六种校正方法。
//
// 随机部分**自带 splitmix64 + Box-Muller**,不依赖 math/rand —— math/rand 的
// 默认发生器序列不保证跨 Go 版本一致,而本 demo 的断言依赖可复现的模拟。
//
// 临界常数来源:Benjamini & Hochberg (1995) α_i = (i/m)α;
// Benjamini & Yekutieli (2001) 任意依赖下 α_i = iα / (m·Σ 1/j)。
// 方法清单与"独立下控制、多数在正相关下稳健"的性质来自 statsmodels multipletests 文档。
package main

import (
	"math"
	"sort"
)

const alphaQ = 0.05

// ---- 自带的确定性随机源(splitmix64) ----

type rng struct{ state uint64 }

func newRNG(seed uint64) *rng { return &rng{state: seed} }

func (r *rng) next() uint64 {
	r.state += 0x9E3779B97F4A7C15
	z := r.state
	z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9
	z = (z ^ (z >> 27)) * 0x94D049BB133111EB
	return z ^ (z >> 31)
}

// uniform 返回 [0,1) 上的均匀随机数。
func (r *rng) uniform() float64 { return float64(r.next()>>11) / float64(1<<53) }

// gaussian Box-Muller 变换。
func (r *rng) gaussian(mu, sigma float64) float64 {
	u1, u2 := r.uniform(), r.uniform()
	for u1 <= 0 {
		u1 = r.uniform()
	}
	z := math.Sqrt(-2*math.Log(u1)) * math.Cos(2*math.Pi*u2)
	return mu + sigma*z
}

// ---- 校正方法 ----

func normSF(z float64) float64 { return 0.5 * math.Erfc(z/math.Sqrt2) }

// harmonic 返回 H_m = Σ 1/i,Benjamini-Yekutieli 的惩罚因子。
func harmonic(m int) float64 {
	s := 0.0
	for i := 1; i <= m; i++ {
		s += 1.0 / float64(i)
	}
	return s
}

type indexed struct {
	p   float64
	idx int
}

func sortedIdx(ps []float64) []indexed {
	out := make([]indexed, len(ps))
	for i, p := range ps {
		out[i] = indexed{p, i}
	}
	sort.Slice(out, func(a, b int) bool { return out[a].p < out[b].p })
	return out
}

// bonferroniAdjusted p·m,截到 1。
func bonferroniAdjusted(ps []float64) []float64 {
	m := float64(len(ps))
	out := make([]float64, len(ps))
	for i, p := range ps {
		out[i] = math.Min(1.0, p*m)
	}
	return out
}

// sidakAlpha 由 1-(1-α_c)^m = α 解出单次 α_c。
func sidakAlpha(a float64, m int) float64 { return 1.0 - math.Pow(1.0-a, 1.0/float64(m)) }

// sidakAdjusted 1-(1-p)^m。
func sidakAdjusted(ps []float64) []float64 {
	m := float64(len(ps))
	out := make([]float64, len(ps))
	for i, p := range ps {
		out[i] = math.Min(1.0, 1.0-math.Pow(1.0-p, m))
	}
	return out
}

// holmAdjusted step-down,取后缀最大值保持单调。
func holmAdjusted(ps []float64) []float64 {
	m := len(ps)
	out := make([]float64, m)
	running := 0.0
	for rank, it := range sortedIdx(ps) {
		v := float64(m-rank) * it.p
		if v > running {
			running = v
		}
		out[it.idx] = math.Min(1.0, running)
	}
	return out
}

// hochbergAdjusted step-up,取前缀最小值。
func hochbergAdjusted(ps []float64) []float64 {
	m := len(ps)
	out := make([]float64, m)
	ord := sortedIdx(ps)
	running := 1.0
	for rank := m - 1; rank >= 0; rank-- {
		it := ord[rank]
		v := float64(m-rank) * it.p
		if v < running {
			running = v
		}
		out[it.idx] = math.Min(1.0, running)
	}
	return out
}

// bhAdjusted Benjamini-Hochberg:校正 p = min_{j≥i} (m/j)·p_(j)。
func bhAdjusted(ps []float64) []float64 {
	m := len(ps)
	out := make([]float64, m)
	ord := sortedIdx(ps)
	running := 1.0
	for rank := m - 1; rank >= 0; rank-- {
		it := ord[rank]
		v := float64(m) / float64(rank+1) * it.p
		if v < running {
			running = v
		}
		out[it.idx] = math.Min(1.0, running)
	}
	return out
}

// byAdjusted Benjamini-Yekutieli:把 BH 的校正 p 乘 H_m。
func byAdjusted(ps []float64) []float64 {
	hm := harmonic(len(ps))
	out := bhAdjusted(ps)
	for i := range out {
		out[i] = math.Min(1.0, out[i]*hm)
	}
	return out
}
