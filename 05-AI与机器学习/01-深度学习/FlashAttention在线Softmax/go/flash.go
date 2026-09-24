// Package flash —— Dao-AILab/flash-attention 的 flash_attn_triton.py 中 _fwd_kernel 的 Go 转写。
// 关键：源码维护的是 m_i（running max）与 lse_i（running log-sum-exp），不是常见的 (m, l) 组合。
package main

import "math"

// NegInf 负无穷（对应 Triton 里的 float("-inf")）。
var NegInf = math.Inf(-1)

// SoftmaxScaleFor 对应 softmax_scale = softmax_scale or 1.0 / math.sqrt(d)。
func SoftmaxScaleFor(d int, scale float64) float64 {
	if scale != 0 {
		return scale
	}
	return 1.0 / math.Sqrt(float64(d))
}

// SeqlenQRounded 对应 math.ceil(seqlen_q/128)*128。
func SeqlenQRounded(seqlenQ int) int {
	return int(math.Ceil(float64(seqlenQ)/128)) * 128
}

// Dot 内积。
func Dot(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		s += a[i] * b[i]
	}
	return s
}

// NaiveAttention 朴素实现：整张 QK^T 落地（对照组）。
func NaiveAttention(q, k, v [][]float64, causal bool, scale float64, bias []float64) [][]float64 {
	sq, sk, d := len(q), len(k), len(v[0])
	out := make([][]float64, sq)
	for i := 0; i < sq; i++ {
		scores := make([]float64, sk)
		for j := 0; j < sk; j++ {
			s := Dot(q[i], k[j]) * scale
			if bias != nil {
				s += bias[j]
			}
			if causal && j > i {
				s = NegInf
			}
			scores[j] = s
		}
		mx := scores[0]
		for _, s := range scores {
			if s > mx {
				mx = s
			}
		}
		exps := make([]float64, sk)
		tot := 0.0
		for j, s := range scores {
			exps[j] = math.Exp(s - mx)
			tot += exps[j]
		}
		row := make([]float64, d)
		for j, e := range exps {
			w := e / tot
			for t := 0; t < d; t++ {
				row[t] += w * v[j][t]
			}
		}
		out[i] = row
	}
	return out
}

// FlashAttention 逐块在线 softmax；返回 (out, lse, scale)。
func FlashAttention(q, k, v [][]float64, causal bool, softmaxScale float64, bias []float64,
	blockM, blockN int, useLseForMax bool) ([][]float64, []float64, float64) {
	sq, sk, d := len(q), len(k), len(q[0])
	if d > 128 {
		panic("FlashAttention only support head dimensions up to 128")
	}
	scale := SoftmaxScaleFor(d, softmaxScale)
	out := make([][]float64, 0, sq)
	lse := make([]float64, 0, sq)
	for startM := 0; startM < sq; startM += blockM {
		end := startM + blockM
		if end > sq {
			end = sq
		}
		n := end - startM
		mI := make([]float64, n)
		lseI := make([]float64, n)
		acc := make([][]float64, n)
		for r := range mI {
			mI[r] = NegInf
			lseI[r] = NegInf
			acc[r] = make([]float64, d)
		}
		endN := sk
		if causal {
			if (startM+1)*blockM < sk {
				endN = (startM + 1) * blockM
			} else {
				endN = sk
			}
		}
		for startN := 0; startN < endN; startN += blockN {
			cn := startN + blockN
			if cn > sk {
				cn = sk
			}
			cols := make([]int, 0, cn-startN)
			for j := startN; j < cn; j++ {
				cols = append(cols, j)
			}
			for r := 0; r < n; r++ {
				i := startM + r
				rowMax := NegInf
				qk := make([]float64, len(cols))
				for ci, j := range cols {
					s := Dot(q[i], k[j]) * scale
					if bias != nil {
						s += bias[j]
					}
					if causal && j > i {
						s = NegInf
					}
					qk[ci] = s
					if s > rowMax {
						rowMax = s
					}
				}
				base := mI[r]
				if useLseForMax {
					base = lseI[r]
				}
				mIJ := rowMax
				if base > mIJ {
					mIJ = base
				}
				p := make([]float64, len(cols))
				lIJ := 0.0
				if math.IsInf(mIJ, -1) {
					for ci := range p {
						p[ci] = 0.0
					}
				} else {
					for ci, s := range qk {
						if math.IsInf(s, -1) {
							p[ci] = 0.0
							continue
						}
						p[ci] = math.Exp(s - mIJ)
						lIJ += p[ci]
					}
				}
				var accScale float64
				if math.IsInf(mIJ, -1) {
					accScale = 0.0
				} else {
					accScale = math.Exp(mI[r] - mIJ)
				}
				for t := 0; t < d; t++ {
					acc[r][t] *= accScale
				}
				for ci, e := range p {
					if e == 0.0 {
						continue
					}
					vj := v[cols[ci]]
					for t := 0; t < d; t++ {
						acc[r][t] += e * vj[t]
					}
				}
				newL := lIJ
				if !math.IsInf(mIJ, -1) && !math.IsInf(lseI[r], -1) {
					newL += math.Exp(lseI[r] - mIJ)
				}
				mI[r] = mIJ
				if newL > 0.0 && !math.IsInf(mIJ, -1) {
					lseI[r] = mIJ + math.Log(newL)
				} else {
					lseI[r] = NegInf
				}
			}
		}
		for r := 0; r < n; r++ {
			var oScale float64
			if math.IsInf(lseI[r], -1) {
				oScale = 0.0
			} else {
				oScale = math.Exp(mI[r] - lseI[r])
			}
			row := make([]float64, d)
			for t := 0; t < d; t++ {
				row[t] = acc[r][t] * oScale
			}
			out = append(out, row)
			lse = append(lse, lseI[r])
		}
	}
	return out, lse, scale
}
