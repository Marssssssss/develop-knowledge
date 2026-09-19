// 同属 main 包:二次判别分析与朴素贝叶斯(用于验证"QDA + 对角协方差 ⇔ GaussianNB")。
package main

import (
	"math"
)
// ------------------------------------------------------------------- QDA
type QDA struct {
	Classes []int
	Priors  []float64
	Means   [][]float64
	Cov     [][][]float64
	Diag    bool
	Reg     float64
}

func (q *QDA) Fit(X [][]float64, y []int) {
	n, d := len(X), len(X[0])
	cls := map[int][]int{}
	for i, v := range y {
		cls[v] = append(cls[v], i)
	}
	keys := []int{}
	for k := range cls {
		keys = append(keys, k)
	}
	for i := 0; i < len(keys); i++ {
		for j := i + 1; j < len(keys); j++ {
			if keys[j] < keys[i] {
				keys[i], keys[j] = keys[j], keys[i]
			}
		}
	}
	q.Classes = keys
	K := len(keys)
	q.Priors = make([]float64, K)
	q.Means = make([][]float64, K)
	q.Cov = make([][][]float64, K)
	for k, c := range keys {
		mem := cls[c]
		q.Priors[k] = float64(len(mem)) / float64(n)
		mu := make([]float64, d)
		for _, i := range mem {
			for j := 0; j < d; j++ {
				mu[j] += X[i][j]
			}
		}
		for j := 0; j < d; j++ {
			mu[j] /= float64(len(mem))
		}
		q.Means[k] = mu
		cm := make([][]float64, d)
		for a := 0; a < d; a++ {
			cm[a] = make([]float64, d)
			for b := 0; b < d; b++ {
				if q.Diag && a != b {
					continue
				}
				s := 0.0
				for _, i := range mem {
					s += (X[i][a] - mu[a]) * (X[i][b] - mu[b])
				}
				cm[a][b] = s / float64(len(mem))
			}
			cm[a][a] += q.Reg
		}
		q.Cov[k] = cm
	}
}

// LogPosterior 原始形式:−½log|Σ_k| −½ 马氏距离 + log π_k。
func (q *QDA) LogPosterior(X [][]float64) [][]float64 {
	d := len(X[0])
	out := make([][]float64, len(X))
	for i, x := range X {
		out[i] = make([]float64, len(q.Classes))
		for k := range q.Classes {
			L, _ := cholesky(q.Cov[k])
			diff := make([]float64, d)
			for j := 0; j < d; j++ {
				diff[j] = x[j] - q.Means[k][j]
			}
			y := cholSolve(L, diff)
			mh := 0.0
			for j := 0; j < d; j++ {
				mh += diff[j] * y[j]
			}
			out[i][k] = -0.5*logDetChol(L) - 0.5*mh + math.Log(q.Priors[k])
		}
	}
	return out
}

func (q *QDA) Predict(X [][]float64) []int {
	out := make([]int, len(X))
	for i, r := range q.LogPosterior(X) {
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

// GaussianNB 条件独立版本(用于验证"QDA + 对角协方差 ⇔ GaussianNB")。
type GaussianNB struct {
	Classes []int
	Priors  []float64
	Means   [][]float64
	Vars    [][]float64
}

func (nb *GaussianNB) Fit(X [][]float64, y []int) {
	n, d := len(X), len(X[0])
	cls := map[int][]int{}
	for i, v := range y {
		cls[v] = append(cls[v], i)
	}
	keys := []int{}
	for k := range cls {
		keys = append(keys, k)
	}
	for i := 0; i < len(keys); i++ {
		for j := i + 1; j < len(keys); j++ {
			if keys[j] < keys[i] {
				keys[i], keys[j] = keys[j], keys[i]
			}
		}
	}
	nb.Classes = keys
	K := len(keys)
	nb.Priors = make([]float64, K)
	nb.Means = make([][]float64, K)
	nb.Vars = make([][]float64, K)
	for k, c := range keys {
		mem := cls[c]
		nb.Priors[k] = float64(len(mem)) / float64(n)
		mu := make([]float64, d)
		for _, i := range mem {
			for j := 0; j < d; j++ {
				mu[j] += X[i][j]
			}
		}
		for j := 0; j < d; j++ {
			mu[j] /= float64(len(mem))
		}
		nb.Means[k] = mu
		v := make([]float64, d)
		for j := 0; j < d; j++ {
			s := 0.0
			for _, i := range mem {
				s += (X[i][j] - mu[j]) * (X[i][j] - mu[j])
			}
			v[j] = s/float64(len(mem)) + 1e-6
		}
		nb.Vars[k] = v
	}
}

func (nb *GaussianNB) JointLogLikelihood(X [][]float64) [][]float64 {
	d := len(X[0])
	out := make([][]float64, len(X))
	for i, x := range X {
		out[i] = make([]float64, len(nb.Classes))
		for k := range nb.Classes {
			s := math.Log(nb.Priors[k])
			for j := 0; j < d; j++ {
				v := nb.Vars[k][j]
				s += -0.5*math.Log(2*math.Pi*v) - (x[j]-nb.Means[k][j])*(x[j]-nb.Means[k][j])/(2*v)
			}
			out[i][k] = s
		}
	}
	return out
}

func (nb *GaussianNB) Predict(X [][]float64) []int {
	out := make([]int, len(X))
	for i, r := range nb.JointLogLikelihood(X) {
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
