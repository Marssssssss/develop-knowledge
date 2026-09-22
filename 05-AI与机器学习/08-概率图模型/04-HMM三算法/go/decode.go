// Viterbi 解码、后验（map）解码、Baum-Welch 重估与暴力枚举参照。
package main

import "math"

const tiny = 1e-300

func safeLog(x float64) float64 {
	if x < tiny {
		return math.Log(tiny)
	}
	return math.Log(x)
}

// Viterbi 在对数域做动态规划，返回 (log 概率, 最优路径)。
//
// v_t(j) = max_i [v_{t−1}(i) · a_ij] · b_j(o_t)，同时记回溯指针。
// 用 log 是为了长序列不 underflow；代价是不能再乘回去，只能比大小。
func Viterbi(h *HMM, obs []int) (float64, []int) {
	tLen := len(obs)
	lp := make([][]float64, tLen)
	bp := make([][]int, tLen)
	lp[0] = make([]float64, h.N)
	bp[0] = make([]int, h.N)
	for i := 0; i < h.N; i++ {
		lp[0][i] = safeLog(h.Pi[i] * h.B[i][obs[0]])
	}
	for t := 1; t < tLen; t++ {
		lp[t] = make([]float64, h.N)
		bp[t] = make([]int, h.N)
		for j := 0; j < h.N; j++ {
			best, arg := math.Inf(-1), 0
			for i := 0; i < h.N; i++ {
				v := lp[t-1][i] + safeLog(h.A[i][j])
				if v > best {
					best, arg = v, i
				}
			}
			bp[t][j] = arg
			lp[t][j] = best + safeLog(h.B[j][obs[t]])
		}
	}
	last := 0
	for i := 1; i < h.N; i++ {
		if lp[tLen-1][i] > lp[tLen-1][last] {
			last = i
		}
	}
	path := make([]int, tLen)
	path[tLen-1] = last
	for t := tLen - 1; t > 0; t-- {
		path[t-1] = bp[t][path[t]]
	}
	return lp[tLen-1][last], path
}

// PosteriorDecode 对应 hmmlearn 的 _decode_map：逐时刻独立取后验最大。
//
// 它**不等于** Viterbi：map 路径可能经过 a_ij = 0 的转移（逐点最优拼不出全局最优），
// 也不保证是联合概率最大的那条路径。
func PosteriorDecode(gamma [][]float64) []int {
	path := make([]int, len(gamma))
	for t, row := range gamma {
		best := 0
		for i := 1; i < len(row); i++ {
			if row[i] > row[best] {
				best = i
			}
		}
		path[t] = best
	}
	return path
}

// BaumWelch 做 EM 重估，返回 (每轮 log 似然, 最终模型)。
//
// 重估式：π̂_i = γ_1(i)；â_ij = Σ_{t<T−1} ξ_t(i,j) / Σ_{t<T−1} γ_t(i)；
// b̂_j(v) = Σ_{t: o_t=v} γ_t(j) / Σ_t γ_t(j)。
// **长度为 1 的序列没有转移，A 不动**（hmmlearn _accumulate_sufficient_statistics
// 里 if n_samples == 1: return 的官方规则）。
func BaumWelch(h *HMM, obs []int, iterations int) ([]float64, *HMM) {
	cur := h
	logs := make([]float64, 0, iterations+1)
	for it := 0; it < iterations; it++ {
		alpha, _ := Forward(cur, obs)
		beta := Backward(cur, obs)
		g, prob := GammaUnscaled(cur, obs)
		x := Xi(cur, obs, alpha, beta, prob)
		logs = append(logs, math.Log(prob))

		n, m, T := cur.N, cur.M, len(obs)
		pi := make([]float64, n)
		copy(pi, g[0])

		a := make([][]float64, n)
		for i := range a {
			a[i] = make([]float64, n)
		}
		if T > 1 {
			for i := 0; i < n; i++ {
				denom := 0.0
				for t := 0; t < T-1; t++ {
					denom += g[t][i]
				}
				for j := 0; j < n; j++ {
					if denom > 0 {
						num := 0.0
						for t := 0; t < T-1; t++ {
							num += x[t][i][j]
						}
						a[i][j] = num / denom
					} else {
						a[i][j] = cur.A[i][j]
					}
				}
			}
		} else {
			for i := 0; i < n; i++ {
				copy(a[i], cur.A[i])
			}
		}

		b := make([][]float64, n)
		for j := 0; j < n; j++ {
			b[j] = make([]float64, m)
			denom := 0.0
			for t := 0; t < T; t++ {
				denom += g[t][j]
			}
			for v := 0; v < m; v++ {
				if denom > 0 {
					num := 0.0
					for t := 0; t < T; t++ {
						if obs[t] == v {
							num += g[t][j]
						}
					}
					b[j][v] = num / denom
				} else {
					b[j][v] = cur.B[j][v]
				}
			}
		}
		cur = NewHMM(pi, a, b)
	}
	_, prob := Forward(cur, obs)
	logs = append(logs, math.Log(prob))
	return logs, cur
}

// BruteForce 枚举全部 N^T 条路径，返回 (P(O|λ), 最优路径, 最优路径概率)。
// 只作小规模对照用（T 一大就是指数级）。
func BruteForce(h *HMM, obs []int) (float64, []int, float64) {
	tLen := len(obs)
	total := 0.0
	best := -1.0
	bestPath := make([]int, tLen)
	path := make([]int, tLen)
	for {
		p := h.Pi[path[0]] * h.B[path[0]][obs[0]]
		for t := 1; t < tLen; t++ {
			p *= h.A[path[t-1]][path[t]] * h.B[path[t]][obs[t]]
		}
		total += p
		if p > best {
			best = p
			copy(bestPath, path)
		}
		// 进位生成下一条路径
		i := tLen - 1
		for i >= 0 {
			path[i]++
			if path[i] < h.N {
				break
			}
			path[i] = 0
			i--
		}
		if i < 0 {
			break
		}
	}
	return total, bestPath, best
}

// IceCream 构造 SLP3 Fig A.2 的冰激凌 HMM：状态序 [HOT, COLD]，观测 1/2/3 → 0/1/2。
//
// A = [[.6,.4],[.5,.5]]（图内数字排布有歧义；本 demo 的定量断言全部另由
// BruteForce 独立验证，不依赖这一处读数）。
// B(1|HOT)=.2 B(2|HOT)=.4 B(3|HOT)=.4 / B(1|COLD)=.5 B(2|COLD)=.4 B(3|COLD)=.1
func IceCream() *HMM {
	return NewHMM(
		[]float64{0.2, 0.8},
		[][]float64{{0.6, 0.4}, {0.5, 0.5}},
		[][]float64{{0.2, 0.4, 0.4}, {0.5, 0.4, 0.1}},
	)
}
