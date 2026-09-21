package main

import "math"

// demo 515 Go 侧:单边 Jacobi SVD 与 svd_flip。

// jacobiSVD 是单边 Jacobi SVD,返回 (U, S, Vt),S 降序。
// 注意 Vt 的装配:Vt[k][i] = V[i][order[k]] —— 下标写反会得到秩 1 的垃圾。
func jacobiSVD(X Mat, tol float64, maxSweeps int) (Mat, []float64, Mat) {
	m, n := len(X), len(X[0])
	swapped := m < n
	A := copyMat(X)
	if swapped {
		A = transpose(X)
	}
	m, n = len(A), len(A[0])
	V := eye(n)
	for sweep := 0; sweep < maxSweeps; sweep++ {
		off := 0.0
		for p := 0; p < n-1; p++ {
			for q := p + 1; q < n; q++ {
				alpha, beta, gamma := 0.0, 0.0, 0.0
				for i := 0; i < m; i++ {
					alpha += A[i][p] * A[i][p]
					beta += A[i][q] * A[i][q]
					gamma += A[i][p] * A[i][q]
				}
				if gamma == 0 || math.Abs(gamma) <= tol*math.Sqrt(alpha*beta) {
					continue
				}
				ratio := math.Abs(gamma) / math.Sqrt(alpha*beta)
				if ratio > off {
					off = ratio
				}
				zeta := (beta - alpha) / (2 * gamma)
				t := math.Copysign(1, zeta) / (math.Abs(zeta) + math.Sqrt(1+zeta*zeta))
				c := 1 / math.Sqrt(1+t*t)
				s := c * t
				for i := 0; i < m; i++ {
					ap, aq := A[i][p], A[i][q]
					A[i][p] = c*ap - s*aq
					A[i][q] = s*ap + c*aq
				}
				for i := 0; i < n; i++ {
					vp, vq := V[i][p], V[i][q]
					V[i][p] = c*vp - s*vq
					V[i][q] = s*vp + c*vq
				}
			}
		}
		if off <= tol {
			break
		}
	}
	sigma := make([]float64, n)
	for j := 0; j < n; j++ {
		sigma[j] = colNorm(A, j)
	}
	order := make([]int, n)
	for j := range order {
		order[j] = j
	}
	for i := 1; i < n; i++ { // 插入排序,降序
		for j := i; j > 0 && sigma[order[j]] > sigma[order[j-1]]; j-- {
			order[j], order[j-1] = order[j-1], order[j]
		}
	}
	sorted := make([]float64, n)
	for k, j := range order {
		sorted[k] = sigma[j]
	}
	U := zeros(m, n)
	for k, j := range order {
		if sorted[k] <= 0 {
			continue
		}
		for i := 0; i < m; i++ {
			U[i][k] = A[i][j] / sorted[k]
		}
	}
	Vt := zeros(n, n)
	for i := 0; i < n; i++ {
		for k, j := range order {
			Vt[k][i] = V[i][j]
		}
	}
	if swapped {
		return transpose(Vt), sorted, transpose(U)
	}
	return U, sorted, Vt
}

func sign(x float64) float64 {
	if x > 0 {
		return 1
	}
	if x < 0 {
		return -1
	}
	return 0
}

// svdFlip 定死符号,避免同一份数据两次分解得到相反符号。
func svdFlip(u, v Mat, uBased bool) (Mat, Mat) {
	if uBased {
		signs := make([]float64, len(u[0]))
		for j := range signs {
			best := 0
			for i := range u {
				if math.Abs(u[i][j]) > math.Abs(u[best][j]) {
					best = i
				}
			}
			signs[j] = sign(u[best][j])
		}
		for i := range u {
			for j := range u[i] {
				u[i][j] *= signs[j]
			}
		}
		if v != nil {
			for i := range v {
				for j := range v[i] {
					v[i][j] *= signs[i]
				}
			}
		}
		return u, v
	}
	signs := make([]float64, len(v))
	for i := range signs {
		best := 0
		for j := range v[i] {
			if math.Abs(v[i][j]) > math.Abs(v[i][best]) {
				best = j
			}
		}
		signs[i] = sign(v[i][best])
	}
	for i := range v {
		for j := range v[i] {
			v[i][j] *= signs[i]
		}
	}
	if u != nil {
		for i := range u {
			for j := range u[i] {
				u[i][j] *= signs[j]
			}
		}
	}
	return u, v
}
