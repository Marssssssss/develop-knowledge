// demo 515 的 Go 侧:纯标准库线性代数内核,与 python/linalg.py 同构。
//
// 含:矩阵乘、转置、Householder QR、单边 Jacobi SVD、svd_flip、部分主元 LU、LCG 随机源。
// 本机没有 Go 工具链,故这些文件走人工审查 + bracket_check / go_sanity / go_crossref。
package main

import "math"

// Mat 是行主序稠密矩阵。
type Mat = [][]float64

func zeros(m, n int) Mat {
	out := make(Mat, m)
	for i := range out {
		out[i] = make([]float64, n)
	}
	return out
}

func eye(n int) Mat {
	out := zeros(n, n)
	for i := 0; i < n; i++ {
		out[i][i] = 1
	}
	return out
}

func copyMat(A Mat) Mat {
	out := make(Mat, len(A))
	for i := range A {
		out[i] = append([]float64(nil), A[i]...)
	}
	return out
}

func matmul(A, B Mat) Mat {
	n, k, m := len(A), len(B), len(B[0])
	out := zeros(n, m)
	for i := 0; i < n; i++ {
		for t := 0; t < k; t++ {
			a := A[i][t]
			if a == 0 {
				continue
			}
			Bt := B[t]
			Oi := out[i]
			for j := 0; j < m; j++ {
				Oi[j] += a * Bt[j]
			}
		}
	}
	return out
}

func transpose(A Mat) Mat {
	out := zeros(len(A[0]), len(A))
	for i := range A {
		for j := range A[i] {
			out[j][i] = A[i][j]
		}
	}
	return out
}

func colNorm(A Mat, j int) float64 {
	s := 0.0
	for i := range A {
		s += A[i][j] * A[i][j]
	}
	return math.Sqrt(s)
}

// qrHouse 是 Householder QR(A = QR),Q 为 m×n 正交列,R 为 n×n 上三角。
func qrHouse(A Mat) (Mat, Mat) {
	m, n := len(A), len(A[0])
	R := copyMat(A)
	Q := eye(m)
	for k := 0; k < n; k++ {
		norm := 0.0
		for i := k; i < m; i++ {
			norm += R[i][k] * R[i][k]
		}
		norm = math.Sqrt(norm)
		if norm == 0 {
			continue
		}
		alpha := -norm
		if R[k][k] < 0 {
			alpha = norm
		}
		v := make([]float64, m)
		for i := k; i < m; i++ {
			v[i] = R[i][k]
		}
		v[k] -= alpha
		vnorm2 := 0.0
		for _, x := range v {
			vnorm2 += x * x
		}
		if vnorm2 == 0 {
			continue
		}
		for j := k; j < n; j++ {
			s := 0.0
			for i := k; i < m; i++ {
				s += v[i] * R[i][j]
			}
			s *= 2 / vnorm2
			for i := k; i < m; i++ {
				R[i][j] -= s * v[i]
			}
		}
		for j := 0; j < m; j++ {
			s := 0.0
			for i := k; i < m; i++ {
				s += Q[j][i] * v[i]
			}
			s *= 2 / vnorm2
			for i := k; i < m; i++ {
				Q[j][i] -= s * v[i]
			}
		}
	}
	Qn := zeros(m, n)
	for i := 0; i < m; i++ {
		copy(Qn[i], Q[i][:n])
	}
	Rn := zeros(n, n)
	for i := 0; i < n; i++ {
		copy(Rn[i], R[i][:n])
	}
	return Qn, Rn
}
