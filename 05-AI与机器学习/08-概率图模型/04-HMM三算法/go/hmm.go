// Package main 实现 HMM 的前向-后向算法（SLP3 附录 A）。
//
// 命名沿用 python/main.py：alpha[t][i] = P(o1..ot, qt=i)，
// beta[t][i] = P(o_{t+1}..oT | qt=i)。缩放版用同一组 c_t 抵消。
package main

import "math"

// HMM 是离散一阶隐马尔可夫模型。n 为状态数，m 为观测符号数。
type HMM struct {
	Pi []float64
	A  [][]float64
	B  [][]float64
	N  int
	M  int
}

// NewHMM 构造 HMM，自动填 N / M。
func NewHMM(pi []float64, a, b [][]float64) *HMM {
	return &HMM{Pi: pi, A: a, B: b, N: len(pi), M: len(b[0])}
}

// Forward 返回 (alpha, P(O|λ))。
// α_1(j) = π_j b_j(o_1)；α_t(j) = [Σ_i α_{t−1}(i) a_ij] b_j(o_t)（SLP3 Eq A.11）。
func Forward(h *HMM, obs []int) ([][]float64, float64) {
	tLen := len(obs)
	alpha := make([][]float64, tLen)
	for t := range alpha {
		alpha[t] = make([]float64, h.N)
	}
	for i := 0; i < h.N; i++ {
		alpha[0][i] = h.Pi[i] * h.B[i][obs[0]]
	}
	for t := 1; t < tLen; t++ {
		for j := 0; j < h.N; j++ {
			s := 0.0
			for i := 0; i < h.N; i++ {
				s += alpha[t-1][i] * h.A[i][j]
			}
			alpha[t][j] = s * h.B[j][obs[t]]
		}
	}
	p := 0.0
	for i := 0; i < h.N; i++ {
		p += alpha[tLen-1][i]
	}
	return alpha, p
}

// ForwardScaled 返回 (α̂, c, logP)。
//
// c_t = Σ_j α_t(j) 是「除数」，所以 log P(O) = +Σ log c_t。
// 注意 hmmlearn 的 scaling_factors 存的是倒数（1/c_t），那边写成 −Σ log c_t；
// 两处符号相反仅仅是因为记法不同，不是矛盾。
func ForwardScaled(h *HMM, obs []int) ([][]float64, []float64, float64) {
	tLen := len(obs)
	alpha := make([][]float64, tLen)
	for t := range alpha {
		alpha[t] = make([]float64, h.N)
	}
	cs := make([]float64, tLen)
	for i := 0; i < h.N; i++ {
		alpha[0][i] = h.Pi[i] * h.B[i][obs[0]]
	}
	cs[0] = 0.0
	for i := 0; i < h.N; i++ {
		cs[0] += alpha[0][i]
	}
	for i := 0; i < h.N; i++ {
		alpha[0][i] /= cs[0]
	}
	for t := 1; t < tLen; t++ {
		for j := 0; j < h.N; j++ {
			s := 0.0
			for i := 0; i < h.N; i++ {
				s += alpha[t-1][i] * h.A[i][j]
			}
			alpha[t][j] = s * h.B[j][obs[t]]
		}
		cs[t] = 0.0
		for j := 0; j < h.N; j++ {
			cs[t] += alpha[t][j]
		}
		for j := 0; j < h.N; j++ {
			alpha[t][j] /= cs[t]
		}
	}
	logP := 0.0
	for _, c := range cs {
		logP += math.Log(c)
	}
	return alpha, cs, logP
}

// Backward 返回未缩放 β：β_{T−1}(i) = 1，
// β_t(i) = Σ_j a_ij b_j(o_{t+1}) β_{t+1}(j)。
func Backward(h *HMM, obs []int) [][]float64 {
	tLen := len(obs)
	beta := make([][]float64, tLen)
	for t := range beta {
		beta[t] = make([]float64, h.N)
	}
	for i := 0; i < h.N; i++ {
		beta[tLen-1][i] = 1.0
	}
	for t := tLen - 2; t >= 0; t-- {
		for i := 0; i < h.N; i++ {
			s := 0.0
			for j := 0; j < h.N; j++ {
				s += h.A[i][j] * h.B[j][obs[t+1]] * beta[t+1][j]
			}
			beta[t][i] = s
		}
	}
	return beta
}

// BackwardScaled 用与 ForwardScaled 同一组 c_t 缩放 β。
//
// 记 P_t = ∏_{s≤t} c_s，则 α̂_t = α_t / P_t。要让 α̂β̂ 与 αβ 差同一个常数，
// 需 β̂_t = β_t · P_t，于是：
//
//	β̂_{T−1} = β_{T−1} · P_{T−1} = P(O)
//	β̂_t     = [Σ_j a_ij b_j(o_{t+1}) β̂_{t+1}(j)] / c_{t+1}
func BackwardScaled(h *HMM, obs []int, cs []float64) [][]float64 {
	tLen := len(obs)
	beta := make([][]float64, tLen)
	for t := range beta {
		beta[t] = make([]float64, h.N)
	}
	prod := 1.0
	for _, c := range cs {
		prod *= c
	}
	for i := 0; i < h.N; i++ {
		beta[tLen-1][i] = prod
	}
	for t := tLen - 2; t >= 0; t-- {
		for i := 0; i < h.N; i++ {
			s := 0.0
			for j := 0; j < h.N; j++ {
				s += h.A[i][j] * h.B[j][obs[t+1]] * beta[t+1][j]
			}
			beta[t][i] = s / cs[t+1]
		}
	}
	return beta
}

// GammaUnscaled 返回 (γ, P(O))，γ_t(i) = α_t(i) β_t(i) / P(O)。
func GammaUnscaled(h *HMM, obs []int) ([][]float64, float64) {
	alpha, prob := Forward(h, obs)
	beta := Backward(h, obs)
	g := make([][]float64, len(obs))
	for t := range g {
		g[t] = make([]float64, h.N)
		for i := 0; i < h.N; i++ {
			g[t][i] = alpha[t][i] * beta[t][i] / prob
		}
	}
	return g, prob
}

// GammaScaled 对应 hmmlearn _compute_posteriors_scaling：
// 逐元素相乘后**逐行归一化**（缩放版必须先归一化，不能直接当 γ 用）。
func GammaScaled(alphaHat, betaHat [][]float64) [][]float64 {
	out := make([][]float64, len(alphaHat))
	for t := range out {
		out[t] = make([]float64, len(alphaHat[t]))
		z := 0.0
		for i := range alphaHat[t] {
			out[t][i] = alphaHat[t][i] * betaHat[t][i]
			z += out[t][i]
		}
		for i := range out[t] {
			out[t][i] /= z
		}
	}
	return out
}

// Xi 返回 ξ，长度 T−1：ξ_t(i,j) = P(q_t=i, q_{t+1}=j | O, λ)
// = α_t(i) a_ij b_j(o_{t+1}) β_{t+1}(j) / P(O)。
func Xi(h *HMM, obs []int, alpha, beta [][]float64, prob float64) [][][]float64 {
	out := make([][][]float64, len(obs)-1)
	for t := 0; t < len(obs)-1; t++ {
		out[t] = make([][]float64, h.N)
		for i := 0; i < h.N; i++ {
			out[t][i] = make([]float64, h.N)
			for j := 0; j < h.N; j++ {
				out[t][i][j] = alpha[t][i] * h.A[i][j] * h.B[j][obs[t+1]] * beta[t+1][j] / prob
			}
		}
	}
	return out
}
