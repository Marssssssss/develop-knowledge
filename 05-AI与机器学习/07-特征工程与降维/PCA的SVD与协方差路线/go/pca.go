// 两条 PCA 路线与实验数据构造。
//
//   - pcaViaCovariance ↔ sklearn PCA(svd_solver='covariance_eigh')
//     官方原话:compared to the "full" solver, this solver effectively doubles
//     the condition number and is therefore less numerically stable
//   - pcaViaSVD        ↔ sklearn PCA(svd_solver='full')
//     components_ 就是中心化数据的右奇异向量
//
// 两者数学等价(λ_i = σ_i²/(n-1)),差别全在数值。
package main

import (
	"math"
	"math/rand"
)

// pcaViaCovariance 路线一:先物化 p×p 协方差矩阵,再做特征分解。
func pcaViaCovariance(Xc [][]float64, k int) []float64 {
	n, p := len(Xc), len(Xc[0])
	C := make([][]float64, p)
	for i := 0; i < p; i++ {
		C[i] = make([]float64, p)
		for j := 0; j < p; j++ {
			sum := 0.0
			for r := 0; r < n; r++ {
				sum += Xc[r][i] * Xc[r][j]
			}
			C[i][j] = sum / float64(n-1)
		}
	}
	lam, _ := jacobiEigh(C, 100, 1e-15)
	return lam[:k]
}

// pcaViaSVD 路线二:直接对数据矩阵做 SVD,σ²/(n-1) 即特征值。
func pcaViaSVD(Xc [][]float64, k int) ([]float64, []float64, [][]float64) {
	n := len(Xc)
	_, s, V := jacobiSVD(Xc, 60, 1e-15)
	lam := make([]float64, len(s))
	for i := range s {
		lam[i] = s[i] * s[i] / float64(n-1)
	}
	return lam[:k], s, V
}

// orthonormalColumns 生成 n×k 列正交矩阵;skipConst 为真时各列还与常向量 1/√n 正交。
// 后者保证 X = U·diag(σ) 的各列均值为 0,center() 成为恒等操作 —— 否则去均值会像
// 减掉一个秩 1 分量那样改写谱(实测能吃掉约 98% 的方差),实验就测不到目标了。
func orthonormalColumns(n, k int, seed int64, skipConst bool) [][]float64 {
	rng := rand.New(rand.NewSource(seed))
	var basis [][]float64
	if skipConst {
		c := make([]float64, n)
		for i := range c {
			c[i] = 1.0 / math.Sqrt(float64(n))
		}
		basis = append(basis, c)
	}
	need := k
	if skipConst {
		need++
	}
	for len(basis) < need {
		v := make([]float64, n)
		for i := range v {
			v[i] = rng.NormFloat64()
		}
		for round := 0; round < 2; round++ { // 两轮改进 Gram-Schmidt
			for _, u := range basis {
				d := 0.0
				for i := range v {
					d += v[i] * u[i]
				}
				for i := range v {
					v[i] -= d * u[i]
				}
			}
		}
		nrm := 0.0
		for _, x := range v {
			nrm += x * x
		}
		nrm = math.Sqrt(nrm)
		for i := range v {
			v[i] /= nrm
		}
		basis = append(basis, v)
	}
	cols := basis
	if skipConst {
		cols = basis[1:]
	}
	M := make([][]float64, n)
	for r := 0; r < n; r++ {
		M[r] = make([]float64, k)
		for j := 0; j < k; j++ {
			M[r][j] = cols[j][r]
		}
	}
	return M
}

// makeMatrixWithSpectrum 造奇异值恰为 sigma、各列零均值的矩阵 X = U·diag(sigma)。
func makeMatrixWithSpectrum(n, p int, sigma []float64, seed int64) [][]float64 {
	U := orthonormalColumns(n, p, seed, true)
	X := make([][]float64, n)
	for r := 0; r < n; r++ {
		X[r] = make([]float64, p)
		for j := 0; j < p; j++ {
			X[r][j] = U[r][j] * sigma[j]
		}
	}
	return X
}
