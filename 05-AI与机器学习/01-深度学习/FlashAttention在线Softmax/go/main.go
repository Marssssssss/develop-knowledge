package main

import (
	"fmt"
	"math"
	"math/rand"
)

func main() {
	rnd := rand.New(rand.NewSource(20260924))

	fmt.Println("== 启动参数 ==")
	fmt.Printf("  softmax_scale 缺省 = 1/sqrt(d)：d=64 → %.9f\n", SoftmaxScaleFor(64, 0))
	fmt.Printf("  seqlen_q_rounded = ceil(S/128)·128：S=1/128/129 → %d/%d/%d\n",
		SeqlenQRounded(1), SeqlenQRounded(128), SeqlenQRounded(129))

	q := randMat(rnd, 24, 8)
	k := randMat(rnd, 20, 8)
	v := randMat(rnd, 20, 8)
	ref := NaiveAttention(q, k, v, false, SoftmaxScaleFor(8, 0), nil)

	fmt.Println("\n== 与朴素注意力的数值偏差（S_q=24, S_k=20, d=8）==")
	for _, c := range []struct{ m, n int }{{128, 128}, {8, 8}, {3, 7}, {24, 1}} {
		o, _, _ := FlashAttention(q, k, v, false, 0, nil, c.m, c.n, true)
		worst := 0.0
		for i := range o {
			for t := range o[i] {
				d := math.Abs(o[i][t] - ref[i][t])
				if d > worst {
					worst = d
				}
			}
		}
		fmt.Printf("  分块 %3dx%-3d → 最大偏差 %.3e\n", c.m, c.n, worst)
	}

	fmt.Println("\n== 因果掩码：改 k[6..9] 只影响第 6 行之后 ==")
	qc := randMat(rnd, 10, 8)
	kc := randMat(rnd, 10, 8)
	vc := randMat(rnd, 10, 8)
	o0, _, _ := FlashAttention(qc, kc, vc, true, 0, nil, 128, 128, true)
	k2 := clone(kc)
	for j := 6; j < 10; j++ {
		for t := 0; t < 8; t++ {
			k2[j][t] += 5.0
		}
	}
	o1, _, _ := FlashAttention(qc, k2, vc, true, 0, nil, 128, 128, true)
	for i := range o0 {
		worst := 0.0
		for t := range o0[i] {
			d := math.Abs(o0[i][t] - o1[i][t])
			if d > worst {
				worst = d
			}
		}
		tag := ""
		if worst < 1e-12 {
			tag = "（未变）"
		}
		fmt.Printf("  行 %d 偏差 %.3e %s\n", i, worst, tag)
	}

	fmt.Println("\n== 数值稳定性（q·k = 10000，scale=1/2 → logit 5000）==")
	qb := [][]float64{{50, 50, 50, 50}}
	kb := [][]float64{{50, 50, 50, 50}, {0, 0, 0, 0}}
	vb := [][]float64{{1, 0, 0, 0}, {0, 1, 0, 0}}
	ob, lseb, scb := FlashAttention(qb, kb, vb, false, 0, nil, 128, 128, true)
	fmt.Printf("  scale=%.4f lse=%.6f（= 5000）\n  out=%v\n", scb, lseb[0], ob[0])
	fmt.Printf("  朴素 exp(5000) = %v（溢出）\n", math.Exp(5000.0))

	fmt.Println("\n== m_ij 的基准：lse_i vs m_i ==")
	oa, _, _ := FlashAttention(q, k, v, false, 0, nil, 8, 8, true)
	ob2, _, _ := FlashAttention(q, k, v, false, 0, nil, 8, 8, false)
	worst := 0.0
	for i := range oa {
		for t := range oa[i] {
			d := math.Abs(oa[i][t] - ob2[i][t])
			if d > worst {
				worst = d
			}
		}
	}
	fmt.Printf("  两种基准的结果偏差 %.3e\n", worst)
}

func randMat(rnd *rand.Rand, rows, cols int) [][]float64 {
	m := make([][]float64, rows)
	for i := range m {
		m[i] = make([]float64, cols)
		for j := range m[i] {
			m[i][j] = rnd.NormFloat64()
		}
	}
	return m
}

func clone(m [][]float64) [][]float64 {
	out := make([][]float64, len(m))
	for i := range m {
		out[i] = append([]float64(nil), m[i]...)
	}
	return out
}
