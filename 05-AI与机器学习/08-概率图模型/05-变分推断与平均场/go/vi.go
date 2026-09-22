// Package main：变分推断 + 平均场（CAVI）的离散（Ising）部分。
//
// 公式编号沿用 Blei, Kucukelbir & McAuliffe, arXiv:1601.00670：
//   Eq (13) ELBO(q) = E[log p(z,x)] − E[log q(z)]
//   Eq (14) log p(x) = KL(q‖p) + ELBO(q)
//   Eq (40) ν_j = E_{−j}[ η_j(z_{−j}, x) ]（完全条件属指数族时）
package main

import "math"

func sigmoid(x float64) float64 {
	if x >= 0 {
		if x > 700 {
			return 1.0
		}
		return 1.0 / (1.0 + math.Exp(-x))
	}
	e := math.Exp(x)
	return e / (1.0 + e)
}

func logit(p float64) float64 { return math.Log(p / (1.0 - p)) }

func bernEntropy(p float64) float64 {
	if p <= 0 || p >= 1 {
		return 0.0
	}
	return -(p*math.Log(p) + (1-p)*math.Log(1-p))
}

func logSumExp(v []float64) float64 {
	mx := v[0]
	for _, x := range v {
		if x > mx {
			mx = x
		}
	}
	s := 0.0
	for _, x := range v {
		s += math.Exp(x - mx)
	}
	return mx + math.Log(s)
}

// allStates 枚举 {0,1}^n 的全部赋值，顺序与 Python 的 itertools.product 一致
// （最后一个分量变化最快）。
func allStates(n int) [][]int {
	total := 1 << n
	out := make([][]int, 0, total)
	for code := 0; code < total; code++ {
		z := make([]int, n)
		for i := 0; i < n; i++ {
			z[n-1-i] = (code >> i) & 1
		}
		out = append(out, z)
	}
	return out
}

// Ising 是未归一化的二元 pairwise 模型：
// log p̃(z) = Σ_i θ_i z_i + Σ_{i<j} w_ij z_i z_j。
type Ising struct {
	Theta []float64
	W     [][]float64
	N     int
}

// NewIsing 构造模型。W 必须对称且对角为 0。
func NewIsing(theta []float64, w [][]float64) *Ising {
	return &Ising{Theta: theta, W: w, N: len(theta)}
}

// LogTilde 返回未归一化的对数密度。
func (g *Ising) LogTilde(z []int) float64 {
	s := 0.0
	for i := 0; i < g.N; i++ {
		s += g.Theta[i] * float64(z[i])
	}
	for i := 0; i < g.N; i++ {
		for j := i + 1; j < g.N; j++ {
			s += g.W[i][j] * float64(z[i]) * float64(z[j])
		}
	}
	return s
}

// LogZ 用 logsumexp 求配分函数的对数（状态数是 2^N，故 N 必须小）。
func (g *Ising) LogZ() float64 {
	vs := make([]float64, 0, 1<<g.N)
	for _, z := range allStates(g.N) {
		vs = append(vs, g.LogTilde(z))
	}
	return logSumExp(vs)
}

// Marginal 返回精确后验的 P(z_i = 1)。
func (g *Ising) Marginal(i int) float64 {
	lz := g.LogZ()
	s := 0.0
	for _, z := range allStates(g.N) {
		if z[i] == 1 {
			s += math.Exp(g.LogTilde(z) - lz)
		}
	}
	return s
}

// Pair 返回精确后验的 P(z_i = 1, z_j = 1)。
func (g *Ising) Pair(i, j int) float64 {
	lz := g.LogZ()
	s := 0.0
	for _, z := range allStates(g.N) {
		if z[i] == 1 && z[j] == 1 {
			s += math.Exp(g.LogTilde(z) - lz)
		}
	}
	return s
}

// Eta 是完全条件的自然参数 η_j(z_{−j}) = θ_j + Σ_{k≠j} w_jk z_k，
// 满足 P(z_j = 1 | z_{−j}) = sigmoid(η_j)。
func (g *Ising) Eta(j int, z []int) float64 {
	s := g.Theta[j]
	for k := 0; k < g.N; k++ {
		if k != j {
			s += g.W[j][k] * float64(z[k])
		}
	}
	return s
}

// QProb 是平均场族 q(z) = ∏_i m_i^{z_i} (1−m_i)^{1−z_i} 的概率。
func QProb(m []float64, z []int) float64 {
	p := 1.0
	for i, zi := range z {
		if zi == 1 {
			p *= m[i]
		} else {
			p *= 1.0 - m[i]
		}
	}
	return p
}

// ElboIsing 计算 Eq (13)：E_q[log p̃] + H(q)。
func ElboIsing(g *Ising, m []float64) float64 {
	e := 0.0
	for i := 0; i < g.N; i++ {
		e += g.Theta[i] * m[i]
	}
	for i := 0; i < g.N; i++ {
		for j := i + 1; j < g.N; j++ {
			e += g.W[i][j] * m[i] * m[j]
		}
	}
	for _, x := range m {
		e += bernEntropy(x)
	}
	return e
}

// KlIsing 按定义枚举 2^N 项求 KL(q ‖ p)。
func KlIsing(g *Ising, m []float64) float64 {
	lz := g.LogZ()
	tot := 0.0
	for _, z := range allStates(g.N) {
		qp := QProb(m, z)
		if qp <= 0 {
			continue
		}
		tot += qp * (math.Log(qp) - (g.LogTilde(z) - lz))
	}
	return tot
}

// CaviIsing 即 Algorithm 1：按 order 顺序逐个把因子设成
// q_j ∝ exp(E_{−j}[log p(z_j | z_{−j})])，返回 (m, 每轮结束后的 ELBO)。
func CaviIsing(g *Ising, iters int, m0 []float64, order []int) ([]float64, []float64) {
	m := make([]float64, g.N)
	for i := range m {
		m[i] = 0.5
	}
	if m0 != nil {
		copy(m, m0)
	}
	if order == nil {
		order = make([]int, g.N)
		for i := range order {
			order[i] = i
		}
	}
	hist := make([]float64, 0, iters)
	for it := 0; it < iters; it++ {
		for _, j := range order {
			nu := g.Theta[j]
			for k := 0; k < g.N; k++ {
				if k != j {
					nu += g.W[j][k] * m[k]
				}
			}
			m[j] = sigmoid(nu) // Eq (40)
		}
		hist = append(hist, ElboIsing(g, m))
	}
	return m, hist
}
