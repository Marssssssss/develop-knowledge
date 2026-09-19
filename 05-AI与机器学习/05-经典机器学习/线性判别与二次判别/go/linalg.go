// 同属 main 包:线性代数与随机数工具(Cholesky / 收缩协方差 / 幂迭代求特征对)。
package main

import (
	"errors"
	"math"
)


import (
	"errors"
	"math"
)

func cholesky(A [][]float64) ([][]float64, error) {
	n := len(A)
	L := make([][]float64, n)
	for i := range L {
		L[i] = make([]float64, n)
	}
	for i := 0; i < n; i++ {
		for j := 0; j <= i; j++ {
			s := 0.0
			for k := 0; k < j; k++ {
				s += L[i][k] * L[j][k]
			}
			if i == j {
				d := A[i][i] - s
				if d <= 1e-300 {
					return nil, errors.New("not positive definite")
				}
				L[i][i] = math.Sqrt(d)
			} else {
				L[i][j] = (A[i][j] - s) / L[j][j]
			}
		}
	}
	return L, nil
}

// cholSolve 解 (L·Lᵀ)x = b。
func cholSolve(L [][]float64, b []float64) []float64 {
	n := len(b)
	y := make([]float64, n)
	for i := 0; i < n; i++ {
		s := 0.0
		for k := 0; k < i; k++ {
			s += L[i][k] * y[k]
		}
		y[i] = (b[i] - s) / L[i][i]
	}
	x := make([]float64, n)
	for i := n - 1; i >= 0; i-- {
		s := 0.0
		for k := i + 1; k < n; k++ {
			s += L[k][i] * x[k]
		}
		x[i] = (y[i] - s) / L[i][i]
	}
	return x
}

// cholSolveT 解 Lᵀx = b(用于把白化空间的方向还原到原始空间)。
func cholSolveT(L [][]float64, b []float64) []float64 {
	n := len(b)
	x := make([]float64, n)
	for i := n - 1; i >= 0; i-- {
		s := 0.0
		for k := i + 1; k < n; k++ {
			s += L[k][i] * x[k]
		}
		x[i] = (b[i] - s) / L[i][i]
	}
	return x
}

func logDetChol(L [][]float64) float64 {
	s := 0.0
	for i := range L {
		s += math.Log(L[i][i])
	}
	return 2 * s
}

// shrunkCovariance:(1−γ)Σ + γ·(tr Σ / p)·I(sklearn 源码口径)。
func shrunkCovariance(cov [][]float64, gamma float64) [][]float64 {
	p := len(cov)
	mu := 0.0
	for i := 0; i < p; i++ {
		mu += cov[i][i]
	}
	mu /= float64(p)
	out := make([][]float64, p)
	for i := 0; i < p; i++ {
		out[i] = make([]float64, p)
		for j := 0; j < p; j++ {
			v := (1 - gamma) * cov[i][j]
			if i == j {
				v += gamma * mu
			}
			out[i][j] = v
		}
	}
	return out
}

type rng struct{ st uint64 }

func newRNG(seed uint64) *rng { return &rng{st: seed} }

func (r *rng) next() float64 {
	r.st = r.st*6364136223846793005 + 1442695040888963407
	return float64(r.st>>11) / float64(1<<53)
}

func (r *rng) gauss() float64 {
	return math.Sqrt(-2*math.Log(r.next()+1e-12)) * math.Cos(2*math.Pi*r.next())
}

// powerEig 幂迭代:对称矩阵的主特征对(用于白化后类均值的 PCA)。
func powerEig(M [][]float64, seed uint64) (float64, []float64) {
	r := newRNG(seed)
	n := len(M)
	v := make([]float64, n)
	for i := range v {
		v[i] = r.next() - 0.5
	}
	norm := func(x []float64) []float64 {
		s := 0.0
		for _, t := range x {
			s += t * t
		}
		s = math.Sqrt(s)
		out := make([]float64, len(x))
		for i := range x {
			out[i] = x[i] / s
		}
		return out
	}
	v = norm(v)
	for it := 0; it < 500; it++ {
		w := make([]float64, n)
		for i := 0; i < n; i++ {
			s := 0.0
			for j := 0; j < n; j++ {
				s += M[i][j] * v[j]
			}
			w[i] = s
		}
		v = norm(w)
	}
	w := make([]float64, n)
	for i := 0; i < n; i++ {
		s := 0.0
		for j := 0; j < n; j++ {
			s += M[i][j] * v[j]
		}
		w[i] = s
	}
	lam := 0.0
	for i := 0; i < n; i++ {
		lam += v[i] * w[i]
	}
	return lam, v
}

func deflate(M [][]float64, lam float64, v []float64) [][]float64 {
	n := len(M)
	out := make([][]float64, n)
	for i := 0; i < n; i++ {
		out[i] = make([]float64, n)
		for j := 0; j < n; j++ {
			out[i][j] = M[i][j] - lam*v[i]*v[j]
		}
	}
	return out
}

