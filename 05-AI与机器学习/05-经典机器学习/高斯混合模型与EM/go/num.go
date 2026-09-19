// 本文件与 gmm.go / mstep.go 同属 main 包:数值工具(logSumExp / Cholesky / 马氏距离)
// 与 k-means 初始化(GMM 的 init_params='kmeans' 默认值)。
package main

import (
	"errors"
	"math"
)

const regCovar = 1e-6 // sklearn GaussianMixture 的 reg_covar 默认值

func logSumExp(v []float64) float64 {
	m := math.Inf(-1)
	for _, x := range v {
		if x > m {
			m = x
		}
	}
	if math.IsInf(m, -1) {
		return math.Inf(-1)
	}
	s := 0.0
	for _, x := range v {
		s += math.Exp(x - m)
	}
	return m + math.Log(s)
}

// cholesky 返回下三角 L 使 A = L·Lᵀ;非正定时返回错误(对应 GMM 的奇异性)。
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
					return nil, errors.New("covariance is not positive definite (singular component)")
				}
				L[i][i] = math.Sqrt(d)
			} else {
				L[i][j] = (A[i][j] - s) / L[j][j]
			}
		}
	}
	return L, nil
}

func logDetChol(L [][]float64) float64 {
	s := 0.0
	for i := range L {
		s += math.Log(L[i][i])
	}
	return 2 * s
}

// mahalSqChol 前代解 L·y = d,返回 ||y||²,即 dᵀΣ⁻¹d。
func mahalSqChol(L, d []float64) float64 {
	y := make([]float64, len(d))
	for i := range d {
		s := 0.0
		for k := 0; k < i; k++ {
			s += L[i][k] * y[k]
		}
		y[i] = (d[i] - s) / L[i][i]
	}
	s := 0.0
	for _, v := range y {
		s += v * v
	}
	return s
}

type RNG struct{ st uint64 }

func NewRNG(seed uint64) *RNG { return &RNG{st: seed} }

func (r *RNG) Next() float64 {
	r.st = r.st*6364136223846793005 + 1442695040888963407
	return float64(r.st>>11) / float64(1<<53)
}

// KMeans 用 Lloyd 迭代产出 GMM 的默认初始化中心(init_params='kmeans')。
func KMeans(X [][]float64, k int, seed uint64) (centers [][]float64, labels []int) {
	r := NewRNG(seed)
	n, d := len(X), len(X[0])
	centers = append(centers, append([]float64(nil), X[int(r.Next()*float64(n))]...))
	for c := 1; c < k; c++ {
		d2 := make([]float64, n)
		total := 0.0
		for i, x := range X {
			best := math.Inf(1)
			for _, ct := range centers {
				s := 0.0
				for j := 0; j < d; j++ {
					s += (x[j] - ct[j]) * (x[j] - ct[j])
				}
				if s < best {
					best = s
				}
			}
			d2[i] = best
			total += best
		}
		target := r.Next() * total
		acc, picked := 0.0, n-1
		for i, v := range d2 {
			acc += v
			if acc >= target {
				picked = i
				break
			}
		}
		centers = append(centers, append([]float64(nil), X[picked]...))
	}
	labels = make([]int, n)
	for it := 0; it < 100; it++ {
		changed := false
		for i, x := range X {
			best, bc := math.Inf(1), 0
			for c, ct := range centers {
				s := 0.0
				for j := 0; j < d; j++ {
					s += (x[j] - ct[j]) * (x[j] - ct[j])
				}
				if s < best {
					best, bc = s, c
				}
			}
			if labels[i] != bc {
				labels[i] = bc
				changed = true
			}
		}
		for c := 0; c < k; c++ {
			cnt := 0
			sum := make([]float64, d)
			for i, l := range labels {
				if l == c {
					cnt++
					for j := 0; j < d; j++ {
						sum[j] += X[i][j]
					}
				}
			}
			if cnt == 0 {
				continue
			}
			for j := 0; j < d; j++ {
				centers[c][j] = sum[j] / float64(cnt)
			}
		}
		if !changed {
			break
		}
	}
	return centers, labels
}

