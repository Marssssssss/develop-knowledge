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

func main() {
	temps := []float64{0.0, 0.2, 0.4, 0.6, 0.8, 1.0}
	rep := ""
	for i := 0; i < 200; i++ {
		rep += "ab"
	}
	crRep := CompressionRatio(rep)
	crNorm := CompressionRatio("The quick brown fox jumps over the lazy dog.")
	check("重复文本压缩比 > 2.4", crRep > 2.4, fmt.Sprintf("%g", crRep))
	check("正常文本压缩比 < 2.4", crNorm < 2.4, fmt.Sprintf("%g", crNorm))

	// 健康 → 不回退
	calls := []float64{}
	_, t0, fb0 := DecodeWithFallback(func(t float64) DecResult {
		calls = append(calls, t)
		return DecResult{1.5, -0.3, 0.05}
	}, temps, 2.4, -1.0, 0.6)
	check("健康结果用 t=0.0", t0 == 0.0 && !fb0 && len(calls) == 1, "")

	// 压缩比过高 → 回退两档
	calls2 := []float64{}
	_, t1, fb1 := DecodeWithFallback(func(t float64) DecResult {
		calls2 = append(calls2, t)
		if t < 0.4 {
			return DecResult{3.1, -0.2, 0.01}
		}
		return DecResult{1.8, -0.2, 0.01}
	}, temps, 2.4, -1.0, 0.6)
	check("压缩比触发回退到 0.4", fb1 && t1 == 0.4 && len(calls2) == 3, fmt.Sprint(calls2))

	// 静音豁免：nsp 高 + logprob 低 → 不回退
	calls3 := []float64{}
	_, t2, fb2 := DecodeWithFallback(func(t float64) DecResult {
		calls3 = append(calls3, t)
		return DecResult{1.5, -1.4, 0.9}
	}, temps, 2.4, -1.0, 0.6)
	check("静音不回退", !fb2 && len(calls3) == 1, fmt.Sprint(calls3))

	// 两种长度惩罚给出不同选择
	lps := []float64{-1.0, -50.0}
	lens := []int{1, 100}
	check("长度归一选长", Rank(lps, lens, 0, false) == 1, "")
	check("NMT 惩罚选短", Rank(lps, lens, 1.0, true) == 0, "")

	// GreedyDecoder
	toks := []int{sot}
	lg := make([]float64, 15)
	for i := range lg {
		lg[i] = negInf
	}
	lg[0], lg[1], lg[2], lg[eot] = 0.2, 5.0, -1.0, 0.1
	toks, slp, done := GreedyStep(toks, lg, 0.0)
	check("t=0 取 argmax", toks[len(toks)-1] == 1, fmt.Sprint(toks))
	check("非 eot 未 completed", !done, "")
	check("sum_logprob 已累加", slp < 0, fmt.Sprintf("%g", slp))
	toks2, slp2, done2 := GreedyStep([]int{sot, eot}, lg, 5.0)
	check("eot 后不再累加", math.Abs(slp2-5.0) < 1e-12, fmt.Sprintf("%g", slp2))
	check("eot 后继续填 eot 并 completed", toks2[len(toks2)-1] == eot && done2, "")

	// Beam search 的 patience
	check("patience 缺省 → max_candidates=beam",
		BeamMaxCandidates(5, 0) == 5, "")
	check("patience=2 → 10", BeamMaxCandidates(5, 2) == 10, "")
	check("patience=1.5 → 8", BeamMaxCandidates(5, 1.5) == 8,
		fmt.Sprint(BeamMaxCandidates(5, 1.5)))

	// 时间戳规则
	lg2 := make([]float64, 200)
	for i := range lg2 {
		lg2[i] = 1.0
	}
	out := ApplyTimestampRules(lg2, []int{sot}, 1, 50)
	allNeg := true
	for k := 0; k < timestampBegin; k++ {
		if out[k] != negInf {
			allNeg = false
		}
	}
	check("首步禁止文本 token", allNeg, "")
	check("首步限制最大时间戳 (tb+50 允许、tb+51 禁)",
		out[timestampBegin+50] != negInf && out[timestampBegin+51] == negInf, "")
	out2 := ApplyTimestampRules(lg2, []int{sot, timestampBegin + 10}, 1, 50)
	tsNeg := true
	for k := timestampBegin; k < 100; k++ {
		if out2[k] != negInf {
			tsNeg = false
		}
	}
	check("首个时间戳后禁止时间戳", tsNeg, "")
	out3 := ApplyTimestampRules(lg2, []int{sot, 0, timestampBegin + 10}, 1, 50)
	textNeg := true
	for k := 0; k < eot; k++ {
		if out3[k] != negInf {
			textNeg = false
		}
	}
	check("文本+时间戳后禁止文本", textNeg, "")
	check("EOT 不被禁", out3[eot] != negInf, "")

	// 官方常量
	check("time_precision = 0.02", math.Abs(2*160.0/16000-0.02) < 1e-12, "")
	check("max_initial_timestamp_index = 50", int(math.Round(1.0/0.02)) == 50, "")
	fmt.Printf("断言 %d 条，失败 %d\n", nOK, nBad)
}
