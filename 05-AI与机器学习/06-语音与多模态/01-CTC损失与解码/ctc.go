// CTC 前向-后向与解码 —— Go 实现（与 ctc.py 同一套论文公式）
//
// 依据 A. Graves et al.《Connectionist Temporal Classification》(ICML 2006):
// §3.1 B 映射先合并相邻重复再去 blank；l' 长 2|l|+1
// §4.1 式(5)-(11) 前向/后向变量；式(8) p(l|x)=α_T(|l'|)+α_T(|l'|-1)
// §4.1 零区：α_t(s)=0 ∀s<|l'|−2(T−t)−1，β_t(s)=0 ∀s>2t（按论文显式置零）
// §4.1 式(14) p(l|x)=Σ_s α_t(s)β_t(s)/y^t_{l'_s}
// §3.2 best path decoding: π*=每帧最大激活的拼接，h(x)≈B(π*)
package main

import (
	"fmt"
	"math"
	"math/rand"
)

// BLANK 是论文里的 blank / b 符号
const BLANK = -1

var negInf = math.Inf(-1)

// Collapse 实现 B：先合并相邻重复，再去掉 blank
func Collapse(path []int) []int {
	merged := []int{}
	for _, c := range path {
		if len(merged) == 0 || merged[len(merged)-1] != c {
			merged = append(merged, c)
		}
	}
	out := []int{}
	for _, c := range merged {
		if c != BLANK {
			out = append(out, c)
		}
	}
	return out
}

// ExtendTarget 得到 l'：首尾与每两个标签之间插 blank，长度 2|l|+1
func ExtendTarget(labels []int) []int {
	ext := []int{BLANK}
	for _, c := range labels {
		ext = append(ext, c, BLANK)
	}
	return ext
}

// LabPositions lab(l,k) = {s : l'_s = k}，可能为空
func LabPositions(ext []int, k int) []int {
	out := []int{}
	for s, c := range ext {
		if c == k {
			out = append(out, s)
		}
	}
	return out
}

func logSumExp(vals []float64) float64 {
	m := negInf
	for _, v := range vals {
		if v > m {
			m = v
		}
	}
	if m == negInf {
		return negInf
	}
	acc := 0.0
	for _, v := range vals {
		acc += math.Exp(v - m)
	}
	return m + math.Log(acc)
}

// AlphaZeroBound 论文 §4.1 的 α 零区下界（1-based t 与 s）
func AlphaZeroBound(S, T, t1 int) int { return S - 2*(T-t1) - 1 }

// BetaZeroBound 论文 §4.1 的 β 零区上界（1-based）
func BetaZeroBound(t1 int) int { return 2 * t1 }

// Yale 承载每帧的符号概率；Probs[t] 的最后一个位置是 blank
type Yale struct {
	Probs [][]float64 // 顺序为 [label0, label1, ..., blank]
}

func (y *Yale) At(t, sym int) float64 {
	if sym == BLANK {
		return y.Probs[t][len(y.Probs[t])-1]
	}
	return y.Probs[t][sym]
}

// ForwardLogY 真正的 log 空间前向，带可选的零区剪枝
func (y *Yale) ForwardLog(ext []int, prune bool) [][]float64 {
	T := len(y.Probs)
	S := len(ext)
	la := make([][]float64, T)
	for t := 0; t < T; t++ {
		la[t] = make([]float64, S)
		for s := 0; s < S; s++ {
			la[t][s] = negInf
		}
	}
	la[0][0] = y.At(0, BLANK)
	if S >= 2 {
		la[0][1] = y.At(0, ext[1])
	}
	if prune {
		b := AlphaZeroBound(S, T, 1)
		for s := 0; s < S; s++ {
			if s+1 < b {
				la[0][s] = negInf
			}
		}
	}
	for t := 1; t < T; t++ {
		prev := la[t-1]
		cur := la[t]
		b := AlphaZeroBound(S, T, t+1)
		for s := 0; s < S; s++ {
			terms := []float64{prev[s]}
			if s-1 >= 0 {
				terms = append(terms, prev[s-1])
			}
			if !(ext[s] == BLANK || (s >= 2 && ext[s-2] == ext[s])) {
				if s-2 >= 0 {
					terms = append(terms, prev[s-2])
				}
			}
			allNeg := true
			for _, v := range terms {
				if v != negInf {
					allNeg = false
					break
				}
			}
			if allNeg {
				cur[s] = negInf
			} else {
				cur[s] = logSumExp(terms) + y.At(t, ext[s])
			}
		}
		if prune {
			for s := 0; s < S; s++ {
				if s+1 < b {
					cur[s] = negInf
				}
			}
		}
	}
	return la
}

// BackwardLog 论文式(10)(11)，prune 时置零 s>2t
func (y *Yale) BackwardLog(ext []int, prune bool) [][]float64 {
	T := len(y.Probs)
	S := len(ext)
	lb := make([][]float64, T)
	for t := 0; t < T; t++ {
		lb[t] = make([]float64, S)
		for s := 0; s < S; s++ {
			lb[t][s] = negInf
		}
	}
	lb[T-1][S-1] = y.At(T-1, BLANK)
	if S >= 2 {
		lb[T-1][S-2] = y.At(T-1, ext[S-2])
	}
	if prune {
		b := BetaZeroBound(T)
		for s := 0; s < S; s++ {
			if s+1 > b {
				lb[T-1][s] = negInf
			}
		}
	}
	for t := T - 2; t >= 0; t-- {
		nxt := lb[t+1]
		b := BetaZeroBound(t + 1)
		for s := 0; s < S; s++ {
			terms := []float64{nxt[s]}
			if s+1 < S {
				terms = append(terms, nxt[s+1])
			}
			if !(ext[s] == BLANK || (s+2 < S && ext[s+2] == ext[s])) {
				if s+2 < S {
					terms = append(terms, nxt[s+2])
				}
			}
			allNeg := true
			for _, v := range terms {
				if v != negInf {
					allNeg = false
					break
				}
			}
			if allNeg {
				lb[t][s] = negInf
			} else {
				lb[t][s] = logSumExp(terms) + y.At(t, ext[s])
			}
		}
		if prune {
			for s := 0; s < S; s++ {
				if s+1 > b {
					lb[t][s] = negInf
				}
			}
		}
	}
	return lb
}

// FinalLogProb 式(8)；|l'|=1（空标签）时只有 α_T(1)
func FinalLogProb(la [][]float64, ext []int) float64 {
	S := len(ext)
	if S == 1 {
		return la[len(la)-1][0]
	}
	return logSumExp([]float64{la[len(la)-1][S-1], la[len(la)-1][S-2]})
}

// CTCLogProb 对外主入口
func (y *Yale) CTCLogProb(labels []int) float64 {
	ext := ExtendTarget(labels)
	if len(y.Probs) < len(labels) {
		return negInf
	}
	return FinalLogProb(y.ForwardLog(ext, true), ext)
}

// BestPathDecode §3.2：每帧取最大激活，拼接后过 B
func (y *Yale) BestPathDecode(nLabels int) []int {
	pi := []int{}
	for t := 0; t < len(y.Probs); t++ {
		best := BLANK
		bv := math.Inf(-1)
		for k := 0; k < nLabels; k++ {
			if y.At(t, k) > bv {
				bv = y.At(t, k)
				best = k
			}
		}
		if y.At(t, BLANK) > bv {
			best = BLANK
		}
		pi = append(pi, best)
	}
	return Collapse(pi)
}
