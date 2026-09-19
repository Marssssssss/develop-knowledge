// 同属 main 包:线性判别分析(共享协方差 + 有监督降维)。
package main

import (
	"math"
)
// ------------------------------------------------------------------- LDA
type LDA struct {
	Classes  []int
	Priors   []float64
	Means    [][]float64
	Cov      [][]float64
	Dirs     [][]float64 // 原始空间的判别方向(已单位化)
	ExplVar  []float64
	chol     [][]float64
	shrink   float64
	comps    int
}

func NewLDA(shrinkage float64, comps int) *LDA {
	return &LDA{shrink: shrinkage, comps: comps}
}

func (m *LDA) Fit(X [][]float64, y []int) error {
	n, d := len(X), len(X[0])
	cls := map[int][]int{}
	for i, v := range y {
		cls[v] = append(cls[v], i)
	}
	keys := []int{}
	for k := range cls {
		keys = append(keys, k)
	}
	for i := 0; i < len(keys); i++ { // 排序,保证与 Python 侧的类序一致
		for j := i + 1; j < len(keys); j++ {
			if keys[j] < keys[i] {
				keys[i], keys[j] = keys[j], keys[i]
			}
		}
	}
	m.Classes = keys
	K := len(keys)
	m.Priors = make([]float64, K)
	m.Means = make([][]float64, K)
	for k, c := range keys {
		mem := cls[c]
		m.Priors[k] = float64(len(mem)) / float64(n)
		mu := make([]float64, d)
		for _, i := range mem {
			for j := 0; j < d; j++ {
				mu[j] += X[i][j]
			}
		}
		for j := 0; j < d; j++ {
			mu[j] /= float64(len(mem))
		}
		m.Means[k] = mu
	}
	cov := make([][]float64, d)
	for i := range cov {
		cov[i] = make([]float64, d)
	}
	for k, c := range keys {
		mem := cls[c]
		for a := 0; a < d; a++ {
			for b := 0; b < d; b++ {
				s := 0.0
				for _, i := range mem {
					s += (X[i][a] - m.Means[k][a]) * (X[i][b] - m.Means[k][b])
				}
				cov[a][b] += m.Priors[k] * s / float64(len(mem))
			}
		}
	}
	if m.shrink > 0 {
		cov = shrunkCovariance(cov, m.shrink)
	}
	m.Cov = cov
	L, err := cholesky(cov)
	if err != nil {
		return err
	}
	m.chol = L
	// 白化后类均值的 PCA:v 在白化空间,还原到原始空间为 L⁻ᵀv
	mus := make([][]float64, K)
	for k := 0; k < K; k++ {
		mus[k] = cholSolve(L, m.Means[k])
	}
	gm := make([]float64, d)
	for k := 0; k < K; k++ {
		for j := 0; j < d; j++ {
			gm[j] += m.Priors[k] * mus[k][j]
		}
	}
	S := make([][]float64, d)
	for a := 0; a < d; a++ {
		S[a] = make([]float64, d)
		for b := 0; b < d; b++ {
			s := 0.0
			for k := 0; k < K; k++ {
				s += m.Priors[k] * (mus[k][a] - gm[a]) * (mus[k][b] - gm[b])
			}
			S[a][b] = s
		}
	}
	limit := K - 1
	if m.comps > 0 && m.comps < limit {
		limit = m.comps
	}
	if limit > d {
		limit = d
	}
	m.Dirs, m.ExplVar = nil, nil
	for c := 0; c < limit; c++ {
		lam, v := powerEig(S, uint64(7+c*13))
		if lam <= 1e-12 {
			break
		}
		w := cholSolveT(L, v)
		nw := 0.0
		for _, t := range w {
			nw += t * t
		}
		nw = math.Sqrt(nw)
		for i := range w {
			w[i] /= nw
		}
		m.Dirs = append(m.Dirs, w)
		m.ExplVar = append(m.ExplVar, lam)
		S = deflate(S, lam, v)
	}
	return nil
}

// DecisionFunction 返回 ω_kᵀx + ω_k0。
func (m *LDA) DecisionFunction(X [][]float64) [][]float64 {
	out := make([][]float64, len(X))
	for i, x := range X {
		out[i] = make([]float64, len(m.Classes))
		for k := range m.Classes {
			Sk := cholSolve(m.chol, m.Means[k])
			quad := 0.0
			for j := range Sk {
				quad += m.Means[k][j] * Sk[j]
			}
			s := -0.5*quad + math.Log(m.Priors[k])
			for j := range x {
				s += Sk[j] * x[j]
			}
			out[i][k] = s
		}
	}
	return out
}

func (m *LDA) Predict(X [][]float64) []int {
	out := make([]int, len(X))
	for i, r := range m.DecisionFunction(X) {
		best, bc := math.Inf(-1), 0
		for k, v := range r {
			if v > best {
				best, bc = v, k
			}
		}
		out[i] = bc
	}
	return out
}

func (m *LDA) Transform(X [][]float64) [][]float64 {
	out := make([][]float64, len(X))
	for i, x := range X {
		out[i] = make([]float64, len(m.Dirs))
		for c, v := range m.Dirs {
			s := 0.0
			for j := range x {
				s += v[j] * x[j]
			}
			out[i][c] = s
		}
	}
	return out
}

