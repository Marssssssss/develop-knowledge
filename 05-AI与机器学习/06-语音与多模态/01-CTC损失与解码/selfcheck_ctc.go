package main

import (
	"fmt"
	"math"
	"math/rand"
)

// ---------- 演示用的自检 ----------

func softmax(u []float64) []float64 {
	m := u[0]
	for _, v := range u {
		if v > m {
			m = v
		}
	}
	e := make([]float64, len(u))
	s := 0.0
	for i, v := range u {
		e[i] = math.Exp(v - m)
		s += e[i]
	}
	for i := range e {
		e[i] /= s
	}
	return e
}

var nOK = 0
var nBad = 0

func check(label string, cond bool, detail string) {
	nOK++
	if !cond {
		nBad++
		fmt.Printf("  FAIL %s %s\n", label, detail)
	}
}

func main() {
	// 论文 §3.1 的两个原例
	c1 := Collapse([]int{'a', BLANK, 'a', 'b', BLANK})
	check("论文原例 a-ab- → aab", fmt.Sprint(c1) == "[97 97 98]", fmt.Sprint(c1))
	c2 := Collapse([]int{BLANK, 'a', 'a', BLANK, BLANK, 'a', 'b', 'b'})
	check("论文原例 -aa--abb → aab", fmt.Sprint(c2) == "[97 97 98]", fmt.Sprint(c2))
	check("blank 分隔不合并", len(Collapse([]int{1, BLANK, 1})) == 2, "")
	check("相邻重复合并", len(Collapse([]int{1, 1})) == 1, "")
	check("|l'|=2|l|+1", len(ExtendTarget([]int{3, 4, 5})) == 7, "")

	// 构造一段 7 帧、2 标签输出
	rnd := rand.New(rand.NewSource(20260920))
	T := 7
	rows := make([][]float64, T)
	for t := 0; t < T; t++ {
		rows[t] = softmax([]float64{rnd.NormFloat64(), rnd.NormFloat64(), rnd.NormFloat64()})
	}
	y := &Yale{Probs: rows}
	ext := ExtendTarget([]int{0, 1})
	la := y.ForwardLog(ext, true)
	lb := y.BackwardLog(ext, true)
	logP := FinalLogProb(la, ext)

	// 式(14)：对任意 t 都应得到同一个 p
	maxSpread := 0.0
	for t := 0; t < T; t++ {
		terms := []float64{}
		for s := 0; s < len(ext); s++ {
			if la[t][s] == negInf || lb[t][s] == negInf {
				continue
			}
			terms = append(terms, la[t][s]+lb[t][s]-y.At(t, ext[s]))
		}
		if len(terms) == 0 {
			continue
		}
		v := logSumExp(terms)
		if d := math.Abs(v - logP); d > maxSpread {
			maxSpread = d
		}
	}
	check("式(14) 对任意 t 同值", maxSpread < 1e-9, fmt.Sprintf("%g", maxSpread))

	// 穷举全部路径核对 p(l)
	brute := 0.0
	for mask := 0; mask < powI(3, T); mask++ {
		m := mask
		p := 1.0
		path := []int{}
		for t := 0; t < T; t++ {
			sym := []int{0, 1, BLANK}[m%3]
			m /= 3
			p *= math.Exp(y.At(t, sym))
			path = append(path, sym)
		}
		if fmt.Sprint(Collapse(path)) == fmt.Sprint([]int{0, 1}) {
			brute += p
		}
	}
	check("前向 == 穷举", math.Abs(math.Exp(logP)-brute) < 1e-9,
		fmt.Sprintf("%g vs %g", math.Exp(logP), brute))

	// best path decoding
	bp := y.BestPathDecode(2)
	fmt.Printf("best path 解码 = %v, log p((0,1)) = %.6f\n", bp, logP)
	fmt.Printf("断言 %d 条，失败 %d\n", nOK, nBad)
}

func powI(a, b int) int {
	r := 1
	for i := 0; i < b; i++ {
		r *= a
	}
	return r
}
