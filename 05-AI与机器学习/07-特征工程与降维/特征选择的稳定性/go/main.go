// 特征选择的稳定性:与 Python 版同题,数值应逐位一致(共用同一条 LCG)。
//
//	go run .
package main

import (
	"fmt"
	"math"
	"sort"
)

func sortInts(a []int) { sort.Ints(a) }

// sortIntsByScore 按 (分数, 下标) 升序排,等价于 Python 的 sorted(key=(score, i))。
func sortIntsByScore(idx []int, score []float64) {
	sort.SliceStable(idx, func(a, b int) bool { return score[idx[a]] < score[idx[b]] })
}

func printGroup(title string, sets []Set, d int) {
	fmt.Printf("     %-12s Jaccard=%.4f  Dice=%.4f  Kuncheva=%+.4f  Φ̂=%+.4f\n", title,
		MeanPairwise(sets, Jaccard), MeanPairwise(sets, Dice),
		MeanPairwise(sets, func(a, b Set) float64 { return Kuncheva(a, b, d) }),
		PhiStability(sets, d))
}

func main() {
	// ---- E1:零模型 vs 真实数据 ----
	d, k, m := 60, 5, 60
	fmt.Printf("[E1] 零模型与真实数据 d=%d k=%d M=%d\n", d, k, m)
	printGroup("零模型", RandomSets(m, k, d, NewRng(2026)), d)
	xr, yr := MakeDataset(100, 60, 4, 0.9, 7)
	real := SelectionSets(xr, yr, k, m, NewRng(11), false)
	printGroup("真实数据", real, d)
	fmt.Printf("     入选频率 ≥ 0.6 的特征数 = %d\n",
		StableFeatures(SelectionProbabilities(real, d), 0.6).Size())

	// ---- E2:并列取舍与常量列 ----
	y60 := make([]int, 60)
	for i := range y60 {
		y60[i] = i % 2
	}
	rng := NewRng(77)
	sig := make([]float64, 60)
	noi := make([]float64, 60)
	for i := 0; i < 60; i++ {
		sig[i] = rng.Normal()
		if y60[i] == 1 {
			sig[i] += 1.5
		}
		noi[i] = rng.Normal()
	}
	x5 := make([][]float64, 60)
	for i := 0; i < 60; i++ {
		x5[i] = []float64{sig[i], sig[i], sig[i], sig[i], noi[i]}
	}
	scores := FClassif(x5, y60)
	fmt.Printf("[E2] 4 列副本 F=%.12f,噪声列 F=%.12f\n", scores[0], scores[4])
	for _, kk := range []int{1, 2, 3} {
		fmt.Printf("     k=%d 选出下标 = %v(并列留下下标大的)\n", kk, sortedKeys(SelectKBest(scores, kk)))
	}
	xc := make([][]float64, 60)
	for i := 0; i < 60; i++ {
		xc[i] = append(append([]float64{}, x5[i]...), 1.0)
	}
	fmt.Printf("     常量列 F = %v(0/0,官方给 NaN 而不是 0)\n", FClassif(xc, y60)[5])

	// ---- E3:零模型的机会校正 + Theorem 5 ----
	nullSets := RandomSets(25, 6, 40, NewRng(4242))
	ic := MeanPairwise(nullSets, func(a, b Set) float64 { return Kuncheva(a, b, 40) })
	phi := PhiStability(nullSets, 40)
	fmt.Printf("[E3] 常量 k=6,d=40:逐对 Kuncheva 平均=%.12f  Φ̂=%.12f  差=%.3e\n",
		ic, phi, math.Abs(ic-phi))

	// ---- E4:稳定性选择的误差控制 ----
	trials, mm, dd, kk := 12, 80, 60, 8
	fmt.Printf("[E4] 纯噪声 d=%d k=%d M=%d,重复 %d 次\n", dd, kk, mm, trials)
	for _, piThr := range []float64{0.6, 0.75, 0.9} {
		tot := 0.0
		for t := 0; t < trials; t++ {
			x, y := MakeDataset(120, dd, 0, 0.0, uint32(100+t))
			sets := SelectionSets(x, y, kk, mm, NewRng(uint32(1000+t)), false)
			tot += float64(StableFeatures(SelectionProbabilities(sets, dd), piThr).Size())
		}
		fmt.Printf("     π_thr=%.2f:单次选择 V=%d  稳定性选择 V=%.2f  上界=%.2f\n",
			piThr, kk, tot/float64(trials), PferBound(float64(kk), piThr, dd))
	}

	// ---- E5:稳定但无用 ----
	n, dd2, k2 := 120, 14, 2
	y2 := make([]int, n)
	for i := range y2 {
		y2[i] = i % 2
	}
	r2 := NewRng(77)
	a := make([]float64, n)
	for i := 0; i < n; i++ {
		a[i] = r2.Normal()
		if y2[i] == 1 {
			a[i] += 1.5
		}
	}
	x2 := make([][]float64, n)
	for i := 0; i < n; i++ {
		row := []float64{a[i], a[i], a[i], a[i]}
		for j := 0; j < dd2-4; j++ {
			row = append(row, r2.Normal())
		}
		x2[i] = row
	}
	sets2 := SelectionSets(x2, y2, k2, 40, NewRng(5), false)
	probs := SelectionProbabilities(sets2, dd2)
	fmt.Printf("[E5] 副本数据:Φ̂=%+.6f,每次选出 %v\n",
		PhiStability(sets2, dd2), sortedKeys(sets2[0]))
	fmt.Printf("     probs[0..3]=%.1f %.1f %.1f %.1f,其余最大=%.1f\n",
		probs[0], probs[1], probs[2], probs[3], maxOf(probs[4:]))
	fmt.Println("done")
}

func sortedKeys(s Set) []int {
	out := make([]int, 0, len(s))
	for k := range s {
		out = append(out, k)
	}
	sort.Ints(out)
	return out
}

func maxOf(v []float64) float64 {
	m := v[0]
	for _, x := range v[1:] {
		if x > m {
			m = x
		}
	}
	return m
}
