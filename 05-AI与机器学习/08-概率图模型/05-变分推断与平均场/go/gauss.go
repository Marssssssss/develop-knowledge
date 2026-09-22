// 二元（可推广到 k 元）高斯目标的平均场变分推断。
//
// 目标 p(x) = N(μ, Λ^{-1})，变分族 q(x) = ∏_i N(m_i, var_i)。
// 坐标更新的结论很反直觉但很硬：
//
//	var_j = 1 / Λ_jj        ← 一步到位，与别的因子无关
//	m_j   = μ_j − (1/Λ_jj) Σ_{k≠j} Λ_jk (m_k − μ_k)
//
// 于是 q 的协方差恒为 0，而 1/Λ_jj 严格小于真边缘方差 (Λ^{-1})_jj ——
// 平均场**必然低估方差**（忘记这条就会把 q 的置信区间当成后验的）。
package main

import "math"

// Det 用高斯消元求行列式（含部分选主元）。
func Det(a [][]float64) float64 {
	n := len(a)
	m := make([][]float64, n)
	for i := range a {
		m[i] = append([]float64(nil), a[i]...)
	}
	d := 1.0
	for c := 0; c < n; c++ {
		piv := c
		for r := c + 1; r < n; r++ {
			if math.Abs(m[r][c]) > math.Abs(m[piv][c]) {
				piv = r
			}
		}
		if math.Abs(m[piv][c]) < 1e-300 {
			return 0.0
		}
		if piv != c {
			m[piv], m[c] = m[c], m[piv]
			d = -d
		}
		d *= m[c][c]
		for r := c + 1; r < n; r++ {
			f := m[r][c] / m[c][c]
			for k := c; k < n; k++ {
				m[r][k] -= f * m[c][k]
			}
		}
	}
	return d
}

// Inv 用 Gauss-Jordan 求逆。
func Inv(a [][]float64) [][]float64 {
	n := len(a)
	m := make([][]float64, n)
	for i := range a {
		row := append([]float64(nil), a[i]...)
		for j := 0; j < n; j++ {
			if i == j {
				row = append(row, 1.0)
			} else {
				row = append(row, 0.0)
			}
		}
		m[i] = row
	}
	for c := 0; c < n; c++ {
		piv := c
		for r := c + 1; r < n; r++ {
			if math.Abs(m[r][c]) > math.Abs(m[piv][c]) {
				piv = r
			}
		}
		m[piv], m[c] = m[c], m[piv]
		pv := m[c][c]
		for k := range m[c] {
			m[c][k] /= pv
		}
		for r := 0; r < n; r++ {
			if r != c && m[r][c] != 0 {
				f := m[r][c]
				for k := range m[r] {
					m[r][k] -= f * m[c][k]
				}
			}
		}
	}
	out := make([][]float64, n)
	for i := range out {
		out[i] = m[i][n:]
	}
	return out
}

// GaussLogZ 返回 ∫ exp(−½(x−μ)ᵀΛ(x−μ)) dx 的对数 = (k/2)ln(2π) − ½ln|Λ|。
func GaussLogZ(lam [][]float64) float64 {
	k := float64(len(lam))
	return 0.5*k*math.Log(2.0*math.Pi) - 0.5*math.Log(Det(lam))
}

// GaussEntropy 是对角高斯的熵 Σ ½ ln(2πe σ²)。
func GaussEntropy(varDiag []float64) float64 {
	s := 0.0
	for _, v := range varDiag {
		s += 0.5 * math.Log(2.0*math.Pi*math.E*v)
	}
	return s
}

// GaussElbo 计算 E_q[log p̃] + H(q)，其中 p̃ 是去掉归一化常数的 exp(−½ quadratic)。
func GaussElbo(m, varDiag, mu []float64, lam [][]float64) float64 {
	k := len(m)
	quad := 0.0
	for i := 0; i < k; i++ {
		quad += lam[i][i] * varDiag[i]
	}
	for i := 0; i < k; i++ {
		for j := 0; j < k; j++ {
			quad += lam[i][j] * (m[i] - mu[i]) * (m[j] - mu[j])
		}
	}
	return -0.5*quad + GaussEntropy(varDiag)
}

// GaussKl 是 KL(N(m, diag(var)) ‖ N(mu, Λ^{-1})) 的闭式：
// ½[ tr(Λ S_q) + (m−μ)ᵀΛ(m−μ) − k − ln|Λ| − Σ ln var_i ]。
func GaussKl(m, varDiag, mu []float64, lam [][]float64) float64 {
	k := len(m)
	quad := 0.0
	for i := 0; i < k; i++ {
		quad += lam[i][i] * varDiag[i]
	}
	for i := 0; i < k; i++ {
		for j := 0; j < k; j++ {
			quad += lam[i][j] * (m[i] - mu[i]) * (m[j] - mu[j])
		}
	}
	logRatio := -math.Log(Det(lam))
	for _, v := range varDiag {
		logRatio -= math.Log(v)
	}
	return 0.5 * (quad - float64(k) + logRatio)
}

// CaviGauss 做坐标上升，返回 (均值, 方差, 每轮 ELBO)。
func CaviGauss(mu []float64, lam [][]float64, iters int, m0 []float64) (
	[]float64, []float64, []float64) {
	k := len(mu)
	m := make([]float64, k)
	if m0 != nil {
		copy(m, m0)
	}
	varDiag := make([]float64, k)
	for i := range varDiag {
		varDiag[i] = 1.0 / lam[i][i]
	}
	hist := make([]float64, 0, iters)
	for it := 0; it < iters; it++ {
		for j := 0; j < k; j++ {
			shift := 0.0
			for i := 0; i < k; i++ {
				if i != j {
					shift += lam[j][i] * (m[i] - mu[i])
				}
			}
			m[j] = mu[j] - shift/lam[j][j]
		}
		hist = append(hist, GaussElbo(m, varDiag, mu, lam))
	}
	return m, varDiag, hist
}
