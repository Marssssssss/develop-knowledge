// wav2vec 2.0 掩码、对比损失与乘积量化 —— Go 实现（与 wav2vec2.py 同一套官方口径）
//
// A. 论文《wav2vec 2.0》(arXiv 2006.11477)：§3.1 掩码 p=0.065 / M=10（约 49%、
//    平均跨度 14.7）；§3.2 式(3) Lm 与式(4) Ld；§4.2 G=2、V=320→102.4k 码字、
//    κ=0.1、K=100、α=0.1、τ 从 2 按 0.999995 衰减到 0.5、编码器 strides/kernels。
// B. fairseq 官方实现：logit_temp=0.1、num_negatives=100、latent_temp=(2,0.5,0.999995)；
//    compute_preds 用 cosine_similarity/logit_temp 且 neg_is_pos 置 −inf；
//    GumbelVectorQuantizer.set_num_updates 与 to_codebook_index。
package main

import (
	"fmt"
	"math"
	"math/rand"
)

var (
	convStrides = []int{5, 2, 2, 2, 2, 2, 2}
	convKernels = []int{10, 3, 3, 3, 3, 2, 2}
)

// SampleMask 不放回采样 p·T 个起点，每个起点掩码后续 M 步（跨度可重叠）
func SampleMask(T int, p float64, M int, seed int64) []bool {
	r := rand.New(rand.NewSource(seed))
	nStart := int(math.Round(p * float64(T)))
	idx := r.Perm(T)[:nStart]
	mask := make([]bool, T)
	for _, s := range idx {
		for k := 0; k < M && s+k < T; k++ {
			mask[s+k] = true
		}
	}
	return mask
}

// MaskStats 返回 (被掩码比例, 平均跨度长度, 跨度个数)
func MaskStats(mask []bool) (float64, float64, int) {
	cnt := 0
	for _, m := range mask {
		if m {
			cnt++
		}
	}
	frac := float64(cnt) / float64(len(mask))
	runs := []int{}
	cur := 0
	for _, m := range mask {
		if m {
			cur++
		} else if cur > 0 {
			runs = append(runs, cur)
			cur = 0
		}
	}
	if cur > 0 {
		runs = append(runs, cur)
	}
	sum := 0
	for _, r := range runs {
		sum += r
	}
	mean := 0.0
	if len(runs) > 0 {
		mean = float64(sum) / float64(len(runs))
	}
	return frac, mean, len(runs)
}

func cosine(a, b []float64) float64 {
	na, nb, d := 0.0, 0.0, 0.0
	for i := range a {
		na += a[i] * a[i]
		nb += b[i] * b[i]
		d += a[i] * b[i]
	}
	if na == 0 || nb == 0 {
		return 0.0
	}
	return d / (math.Sqrt(na) * math.Sqrt(nb))
}

// ContrastiveLoss 论文式(3)：Lm = −log exp(sim/κ) / Σ exp(sim/κ)
func ContrastiveLoss(ct, qt []float64, negatives [][]float64, kappa float64) float64 {
	sims := []float64{cosine(ct, qt) / kappa}
	for _, n := range negatives {
		sims = append(sims, cosine(ct, n)/kappa)
	}
	m := sims[0]
	for _, s := range sims {
		if s > m {
			m = s
		}
	}
	z := 0.0
	for _, s := range sims {
		z += math.Exp(s - m)
	}
	return -(sims[0] - m - math.Log(z))
}

// SampleNegatives fairseq：randint(0, high-1)，j >= t 时 +1，避开自身位置
func SampleNegatives(tsz, num, nNeg int, seed int64) [][]int {
	r := rand.New(rand.NewSource(seed))
	out := make([][]int, num)
	for t := 0; t < num; t++ {
		row := make([]int, nNeg)
		for i := 0; i < nNeg; i++ {
			j := r.Intn(tsz - 1)
			if j >= t {
				j++
			}
			row[i] = j
		}
		out[t] = row
	}
	return out
}

// ComputePreds fairseq：logits = cos/logit_temp；与正样本相同的负样本置 −inf
func ComputePreds(x, y []float64, negatives [][]float64, logitTemp, negInf float64) []float64 {
	logits := []float64{cosine(x, y) / logitTemp}
	for _, n := range negatives {
		v := cosine(x, n) / logitTemp
		same := true
		for i := range y {
			if y[i] != n[i] {
				same = false
				break
			}
		}
		if same {
			v = negInf
		}
		logits = append(logits, v)
	}
	return logits
}

// GumbelVectorQuantizer 对应 fairseq/modules/gumbel_vector_quantizer.py
type GumbelVectorQuantizer struct {
	NumVars, Groups           int
	MaxTemp, MinTemp, Decay   float64
	CurrTemp                  float64
}

func NewGVQ(numVars, groups int, temp [3]float64) *GumbelVectorQuantizer {
	q := &GumbelVectorQuantizer{NumVars: numVars, Groups: groups,
		MaxTemp: temp[0], MinTemp: temp[1], Decay: temp[2]}
	q.CurrTemp = q.MaxTemp
	return q
}

func (q *GumbelVectorQuantizer) SetNumUpdates(n int) {
	q.CurrTemp = math.Max(q.MaxTemp*math.Pow(q.Decay, float64(n)), q.MinTemp)
}

// ToCodebookIndex res += indices[i] * V^(G-i-1)
func (q *GumbelVectorQuantizer) ToCodebookIndex(indices []int) int {
	res := 0
	for i := 0; i < q.Groups; i++ {
		res += indices[i] * int(math.Pow(float64(q.NumVars), float64(q.Groups-i-1)))
	}
	return res
}

func (q *GumbelVectorQuantizer) CodebookSize() int {
	return int(math.Pow(float64(q.NumVars), float64(q.Groups)))
}

func entropyOf(probs []float64) float64 {
	h := 0.0
	for _, p := range probs {
		if p > 0 {
			h -= p * math.Log(p+1e-7)
		}
	}
	return h
}

// CodePerplexity hard one-hot 的批次平均分布 → exp(熵)，按组求和
func CodePerplexity(assign [][]int, groups, V int) float64 {
	total := 0.0
	for g := 0; g < groups; g++ {
		counts := make([]float64, V)
		for _, a := range assign {
			counts[a[g]]++
		}
		n := float64(len(assign))
		avg := make([]float64, V)
		for v := range avg {
			avg[v] = counts[v] / n
		}
		total += math.Exp(entropyOf(avg))
	}
	return total
}

// DiversityLoss 论文式(4)：Ld = (1/GV) Σ_g Σ_v p̄ log p̄
func DiversityLoss(avg [][]float64, G, V int) float64 {
	s := 0.0
	for g := 0; g < G; g++ {
		for v := 0; v < V; v++ {
			if avg[g][v] > 0 {
				s += avg[g][v] * math.Log(avg[g][v])
			}
		}
	}
	return s / float64(G*V)
}

// DiversityLossPplForm 论文脚注 2：(GV − Σ_g exp(−Σ_v p log p)) / GV
func DiversityLossPplForm(avg [][]float64, G, V int) float64 {
	s := 0.0
	for g := 0; g < G; g++ {
		h := 0.0
		for v := 0; v < V; v++ {
			if avg[g][v] > 0 {
				h -= avg[g][v] * math.Log(avg[g][v])
			}
		}
		s += math.Exp(h)
	}
	return (float64(G*V) - s) / float64(G*V)
}

func convOutLength(L, k, s int) int { return int(math.Floor(float64(L-k)/float64(s) + 1)) }

func EncoderOutLength(L int) int {
	for i := range convStrides {
		L = convOutLength(L, convKernels[i], convStrides[i])
	}
	return L
}
