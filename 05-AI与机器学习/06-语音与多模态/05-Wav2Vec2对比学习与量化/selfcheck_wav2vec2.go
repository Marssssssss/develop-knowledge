package main

import (
	"fmt"
	"math"
)

var nOK, nBad int

func check(label string, cond bool, detail string) {
	nOK++
	if !cond {
		nBad++
		fmt.Printf("  FAIL %s %s\n", label, detail)
	}
}

func closeF(a, b, eps float64) bool { return math.Abs(a-b) <= eps }

func main() {
	// 掩码
	mask := SampleMask(20000, 0.065, 10, 2026)
	frac, mean, runs := MaskStats(mask)
	fmt.Printf("    掩码比例 %.4f，平均跨度 %.2f，跨度数 %d\n", frac, mean, runs)
	check("掩码比例接近 49%", frac >= 0.45 && frac <= 0.52, fmt.Sprintf("%g", frac))
	check("平均跨度接近 14.7", mean >= 13.0 && mean <= 16.5, fmt.Sprintf("%g", mean))

	// 对比损失
	same := make([]float64, 8)
	for i := range same {
		same[i] = 1.0
	}
	negs := make([][]float64, 100)
	for i := range negs {
		negs[i] = same
	}
	check("无区分度 Lm = ln(K+1)",
		closeF(ContrastiveLoss(same, same, negs, 0.1), math.Log(101), 1e-9), "")

	ct := make([]float64, 8)
	ct[0] = 1.0
	nz := make([][]float64, 100)
	for k := range nz {
		v := make([]float64, 8)
		v[k%7+1] = 1.0
		nz[k] = v
	}
	lm := ContrastiveLoss(ct, ct, nz, 0.1)
	check("完美上下文 Lm = log1p(100·e^-10)",
		closeF(lm, math.Log1p(100*math.Exp(-10.0)), 1e-12), fmt.Sprintf("%g", lm))

	// 负采样
	ni := SampleNegatives(60, 60, 100, 5)
	bad := 0
	for t := 0; t < 60; t++ {
		for _, j := range ni[t] {
			if j == t {
				bad++
			}
		}
	}
	check("负样本不含自身位置", bad == 0, fmt.Sprint(bad))

	// compute_preds
	lg := ComputePreds(ct, ct, [][]float64{ct, {0, 1, 0, 0, 0, 0, 0, 0}}, 0.1, -1e30)
	check("命中正样本的负样本置 -inf", lg[1] == -1e30, fmt.Sprintf("%g", lg[1]))
	check("logits = cos/logit_temp", closeF(lg[0], 10.0, 1e-9), fmt.Sprintf("%g", lg[0]))

	// 码本
	q := NewGVQ(320, 2, [3]float64{2.0, 0.5, 0.999995})
	check("码本大小 102400", q.CodebookSize() == 102400, "")
	check("to_codebook_index([5,7]) = 1607", q.ToCodebookIndex([]int{5, 7}) == 1607, "")
	check("to_codebook_index([319,319]) = 102399",
		q.ToCodebookIndex([]int{319, 319}) == 102399, "")

	// 温度退火
	check("初始温度 2", closeF(q.CurrTemp, 2.0, 1e-12), "")
	q.SetNumUpdates(100000)
	check("10 万步后 = max(2·d^n, 0.5)",
		closeF(q.CurrTemp, math.Max(2*math.Pow(0.999995, 100000), 0.5), 1e-12), "")
	q.SetNumUpdates(1000000000)
	check("不低于 min_temp", closeF(q.CurrTemp, 0.5, 1e-12), "")
	nMin := math.Log(0.25) / math.Log(0.999995)
	check("到 min 约 27.7 万步", nMin > 270000 && nMin < 280000, fmt.Sprintf("%g", nMin))

	// perplexity 与多样性损失
	G, V := 2, 320
	assign := make([][]int, 640)
	for i := range assign {
		assign[i] = []int{(i * 7) % V, (i * 13) % V}
	}
	cp := CodePerplexity(assign, G, V)
	check("均匀分配 code_ppl ≈ 2V", math.Abs(cp-2*float64(V)) < 2.0, fmt.Sprintf("%g", cp))
	uni := make([][]float64, G)
	for g := range uni {
		uni[g] = make([]float64, V)
		for v := range uni[g] {
			uni[g][v] = 1.0 / float64(V)
		}
	}
	check("均匀分布 Ld = −ln(V)/V",
		closeF(DiversityLoss(uni, G, V), -math.Log(float64(V))/float64(V), 1e-12), "")
	check("均匀分布时脚注形式 = 0",
		math.Abs(DiversityLossPplForm(uni, G, V)) < 1e-9, "")

	// 编码器
	check("1 秒 → 49 帧", EncoderOutLength(16000) == 49, fmt.Sprint(EncoderOutLength(16000)))
	check("感受野 400 样本 = 25 ms", closeF(400.0/16000*1000, 25.0, 1e-12), "")
	fmt.Printf("断言 %d 条，失败 %d\n", nOK, nBad)
}
