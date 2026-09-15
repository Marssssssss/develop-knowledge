// 数学工具:稠密矩阵乘法、两轮改进 Gram-Schmidt、Jacobi 对称特征分解、LU 的 L 因子。
// 不引第三方库,以便误差只来自算法本身(与 python/main.py 同题)。
package main

import (
	"math"
	"math/rand"
)

func matmul(A, B [][]float64) [][]float64 {
	m, k, n := len(A), len(B), len(B[0])
	C := make([][]float64, m)
	for i := 0; i < m; i++ {
		C[i] = make([]float64, n)
		for j := 0; j < n; j++ {
			s := 0.0
			for t := 0; t < k; t++ {
				s += A[i][t] * B[t][j]
			}
			C[i][j] = s
		}
	}
	return C
}

func transpose(A [][]float64) [][]float64 {
	m, n := len(A), len(A[0])
	T := make([][]float64, n)
	for j := 0; j < n; j++ {
		T[j] = make([]float64, m)
		for i := 0; i < m; i++ {
			T[j][i] = A[i][j]
		}
	}
	return T
}

func sqNorm(v []float64) float64 {
	s := 0.0
	for _, x := range v {
		s += x * x
	}
	return s
}

// qrQ 对 Y(n×l) 做两轮改进 Gram-Schmidt,返回正交基 Q(n×l)。
// 每算完一列就地归一化;否则减掉的是"投影 × 未归一列的模长",列不会正交。
func qrQ(Y [][]float64) [][]float64 {
	n, l := len(Y), len(Y[0])
	cols := make([][]float64, l)
	for j := 0; j < l; j++ {
		cols[j] = make([]float64, n)
		for i := 0; i < n; i++ {
			cols[j][i] = Y[i][j]
		}
	}
	for round := 0; round < 2; round++ {
		for j := 0; j < l; j++ {
			for t := 0; t < j; t++ {
				d := 0.0
				for i := 0; i < n; i++ {
					d += cols[j][i] * cols[t][i]
				}
				for i := 0; i < n; i++ {
					cols[j][i] -= d * cols[t][i]
				}
			}
			if nrm := math.Sqrt(sqNorm(cols[j])); nrm > 0 {
				for i := 0; i < n; i++ {
					cols[j][i] /= nrm
				}
			}
		}
	}
	Q := make([][]float64, n)
	for i := 0; i < n; i++ {
		Q[i] = make([]float64, l)
		for j := 0; j < l; j++ {
			Q[i][j] = cols[j][i]
		}
	}
	return Q
}

// luLFactor 带部分主元的 LU,返回 L 因子(n×l,单位下三角)。
// Halko 的 'LU' 归一化即"用 L 的列当新基":张成同一空间,但尺度被拉回 O(1)。
func luLFactor(Y [][]float64) [][]float64 {
	n, l := len(Y), len(Y[0])
	Z := make([][]float64, n)
	for i := range Z {
		Z[i] = append([]float64(nil), Y[i]...)
	}
	L := make([][]float64, n)
	for i := range L {
		L[i] = make([]float64, l)
	}
	for j := 0; j < l; j++ {
		piv := j
		for r := j + 1; r < n; r++ {
			if math.Abs(Z[r][j]) > math.Abs(Z[piv][j]) {
				piv = r
			}
		}
		Z[j], Z[piv] = Z[piv], Z[j]
		for i := j + 1; i < n; i++ {
			m := 0.0
			if Z[j][j] != 0 {
				m = Z[i][j] / Z[j][j]
			}
			L[i][j] = m
			for c := j; c < l; c++ {
				Z[i][c] -= m * Z[j][c]
			}
		}
		L[j][j] = 1
	}
	return L
}

// jacobiEigh 循环 Jacobi 对称特征分解,特征值与特征向量均降序。
func jacobiEigh(S [][]float64) ([]float64, [][]float64) {
	p := len(S)
	A := make([][]float64, p)
	Q := make([][]float64, p)
	for i := 0; i < p; i++ {
		A[i] = append([]float64(nil), S[i]...)
		Q[i] = make([]float64, p)
		Q[i][i] = 1
	}
	for sweep := 0; sweep < 300; sweep++ {
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
				for k := 0; k < p; k++ {
					aki, akj := A[k][i], A[k][j]
					A[k][i], A[k][j] = c*aki+s*akj, -s*aki+c*akj
				}
				for k := 0; k < p; k++ {
					aik, ajk := A[i][k], A[j][k]
					A[i][k], A[j][k] = c*aik+s*ajk, -s*aik+c*ajk
				}
				for k := 0; k < p; k++ {
					qki, qkj := Q[k][i], Q[k][j]
					Q[k][i], Q[k][j] = c*qki+s*qkj, -s*qki+c*qkj
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

// smallSVD 矮矩阵 B(l×p,l 很小)的左奇异向量与奇异值:B·Bᵀ 的特征分解。
func smallSVD(B [][]float64) ([][]float64, []float64) {
	w, Q := jacobiEigh(matmul(B, transpose(B)))
	s := make([]float64, len(w))
	for i, x := range w {
		s[i] = math.Sqrt(math.Max(x, 0))
	}
	return Q, s
}

func frobOrthErr(Q [][]float64) float64 {
	k := len(Q[0])
	QtQ := matmul(transpose(Q), Q)
	sum := 0.0
	for i := 0; i < k; i++ {
		for j := 0; j < k; j++ {
			d := QtQ[i][j]
			if i == j {
				d--
			}
			sum += d * d
		}
	}
	return math.Sqrt(sum)
}

func orthonormalColumns(n, k int, rng *rand.Rand) [][]float64 {
	cols := make([][]float64, k)
	for j := 0; j < k; j++ {
		v := make([]float64, n)
		for i := range v {
			v[i] = rng.NormFloat64()
		}
		for round := 0; round < 2; round++ {
			for t := 0; t < j; t++ {
				d := 0.0
				for i := 0; i < n; i++ {
					d += v[i] * cols[t][i]
				}
				for i := 0; i < n; i++ {
					v[i] -= d * cols[t][i]
				}
			}
		}
		nrm := math.Sqrt(sqNorm(v))
		for i := range v {
			v[i] /= nrm
		}
		cols[j] = v
	}
	M := make([][]float64, n)
	for i := 0; i < n; i++ {
		M[i] = make([]float64, k)
		for j := 0; j < k; j++ {
			M[i][j] = cols[j][i]
		}
	}
	return M
}

// makeMatrixWithSpectrum 造奇异值恰为 sigma 的矩阵 A = U·diag(σ)·Vᵀ,并返回 (A, U)。
// U 必须一并返回:验证子空间误差的参照物是 A 自己的左奇异向量。
func makeMatrixWithSpectrum(n, p int, sigma []float64, seed int64) ([][]float64, [][]float64) {
	rng := rand.New(rand.NewSource(seed))
	U := orthonormalColumns(n, p, rng)
	V := orthonormalColumns(p, p, rng)
	A := make([][]float64, n)
	for r := 0; r < n; r++ {
		A[r] = make([]float64, p)
		for j := 0; j < p; j++ {
			s := 0.0
			for t := 0; t < p; t++ {
				s += U[r][t] * sigma[t] * V[j][t]
			}
			A[r][j] = s
		}
	}
	return A, U
}
