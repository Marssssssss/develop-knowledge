// 线性代数底座:单边 Jacobi SVD + 循环 Jacobi 对称特征分解。
// 自己实现而不依赖第三方库,以便把"两条路线的数值差异"这件事完整暴露出来。
package main

import "math"

// center 按列去均值。sklearn: "PCA centers but does not scale the input data".
func center(X [][]float64) [][]float64 {
	n, p := len(X), len(X[0])
	mu := make([]float64, p)
	for j := 0; j < p; j++ {
		sum := 0.0
		for r := 0; r < n; r++ {
			sum += X[r][j]
		}
		mu[j] = sum / float64(n)
	}
	out := make([][]float64, n)
	for r := 0; r < n; r++ {
		out[r] = make([]float64, p)
		for j := 0; j < p; j++ {
			out[r][j] = X[r][j] - mu[j]
		}
	}
	return out
}

// jacobiSVD 单边 Jacobi SVD:A = U·diag(s)·Vᵀ(n >= p)。
// 不断用 2×2 旋转把 A 的列两两正交化(θ = atan2(2g, a-b)/2 使 Gram 的非对角元归零),
// 收敛后列范数即奇异值,U 由列归一化得到。右奇异向量精度不像正规方程那样被平方放大。
func jacobiSVD(A [][]float64, maxSweeps int, tol float64) ([][]float64, []float64, [][]float64) {
	n, p := len(A), len(A[0])
	B := make([][]float64, n)
	for r := range B {
		B[r] = append([]float64(nil), A[r]...)
	}
	V := make([][]float64, p)
	for i := range V {
		V[i] = make([]float64, p)
		V[i][i] = 1
	}
	for sweep := 0; sweep < maxSweeps; sweep++ {
		off := 0.0
		for i := 0; i < p-1; i++ {
			for j := i + 1; j < p; j++ {
				a, b, g := 0.0, 0.0, 0.0
				for r := 0; r < n; r++ {
					a += B[r][i] * B[r][i]
					b += B[r][j] * B[r][j]
					g += B[r][i] * B[r][j]
				}
				if g == 0 || math.Abs(g) <= tol*math.Sqrt(a*b) {
					continue
				}
				off += g * g
				theta := 0.5 * math.Atan2(2*g, a-b)
				c, s := math.Cos(theta), math.Sin(theta)
				for r := 0; r < n; r++ { // B ← B·Q
					bi, bj := B[r][i], B[r][j]
					B[r][i] = c*bi + s*bj
					B[r][j] = -s*bi + c*bj
				}
				for r := 0; r < p; r++ { // V ← V·Q
					vi, vj := V[r][i], V[r][j]
					V[r][i] = c*vi + s*vj
					V[r][j] = -s*vi + c*vj
				}
			}
		}
		if off <= 1e-30 {
			break
		}
	}
	s := make([]float64, p)
	for i := 0; i < p; i++ {
		sum := 0.0
		for r := 0; r < n; r++ {
			sum += B[r][i] * B[r][i]
		}
		s[i] = math.Sqrt(sum)
	}
	order := argsortDesc(s)
	U := make([][]float64, n)
	for r := 0; r < n; r++ {
		U[r] = make([]float64, p)
		for k := 0; k < p; k++ {
			if s[order[k]] > 0 {
				U[r][k] = B[r][order[k]] / s[order[k]]
			}
		}
	}
	Vs := make([][]float64, p)
	for r := 0; r < p; r++ {
		Vs[r] = make([]float64, p)
		for k := 0; k < p; k++ {
			Vs[r][k] = V[r][order[k]]
		}
	}
	sorted := make([]float64, p)
	for k := 0; k < p; k++ {
		sorted[k] = s[order[k]]
	}
	return U, sorted, Vs
}

// jacobiEigh 循环 Jacobi 对称特征分解,返回降序特征值与按列存放的特征向量。
func jacobiEigh(S [][]float64, maxSweeps int, tol float64) ([]float64, [][]float64) {
	p := len(S)
	A := make([][]float64, p)
	for i := range A {
		A[i] = append([]float64(nil), S[i]...)
	}
	Q := make([][]float64, p)
	for i := range Q {
		Q[i] = make([]float64, p)
		Q[i][i] = 1
	}
	for sweep := 0; sweep < maxSweeps; sweep++ {
		off := 0.0
		for i := 0; i < p; i++ {
			for j := i + 1; j < p; j++ {
				off += A[i][j] * A[i][j]
			}
		}
		if off <= 1e-30 {
			break
		}
		for i := 0; i < p-1; i++ {
			for j := i + 1; j < p; j++ {
				if A[i][j] == 0 {
					continue
				}
				theta := 0.5 * math.Atan2(2*A[i][j], A[i][i]-A[j][j])
				c, s := math.Cos(theta), math.Sin(theta)
				for k := 0; k < p; k++ { // A ← A·Q
					aki, akj := A[k][i], A[k][j]
					A[k][i] = c*aki + s*akj
					A[k][j] = -s*aki + c*akj
				}
				for k := 0; k < p; k++ { // A ← Qᵀ·A
					aik, ajk := A[i][k], A[j][k]
					A[i][k] = c*aik + s*ajk
					A[j][k] = -s*aik + c*ajk
				}
				for k := 0; k < p; k++ { // Q ← Q·Q
					qki, qkj := Q[k][i], Q[k][j]
					Q[k][i] = c*qki + s*qkj
					Q[k][j] = -s*qki + c*qkj
				}
			}
		}
	}
	w := make([]float64, p)
	for i := 0; i < p; i++ {
		w[i] = A[i][i]
	}
	order := argsortDesc(w)
	lam := make([]float64, p)
	V := make([][]float64, p)
	for r := 0; r < p; r++ {
		V[r] = make([]float64, p)
	}
	for k := 0; k < p; k++ {
		lam[k] = w[order[k]]
		for r := 0; r < p; r++ {
			V[r][k] = Q[r][order[k]]
		}
	}
	return lam, V
}

// argsortDesc 返回按值降序的下标序列(插入排序;本 demo 的 p 只有几十,不必引 sort 包)。
func argsortDesc(v []float64) []int {
	idx := make([]int, len(v))
	for i := range idx {
		idx[i] = i
	}
	for i := 1; i < len(idx); i++ {
		for j := i; j > 0 && v[idx[j]] > v[idx[j-1]]; j-- {
			idx[j], idx[j-1] = idx[j-1], idx[j]
		}
	}
	return idx
}
