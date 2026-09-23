package main

import (
	"fmt"
	"math"
)

func lcgUniforms(n, seed int) []float64 {
	x := seed & 0x7FFFFFFF
	out := make([]float64, n)
	for i := 0; i < n; i++ {
		x = (1103515245*x + 12345) & 0x7FFFFFFF
		out[i] = float64(x%1000000)/1000000.0 + 1e-6
	}
	return out
}

func lcgPoints(n, dim, seed int) [][]float64 {
	x := seed & 0x7FFFFFFF
	pts := make([][]float64, n)
	for i := 0; i < n; i++ {
		v := make([]float64, dim)
		for d := 0; d < dim; d++ {
			x = (1103515245*x + 12345) & 0x7FFFFFFF
			v[d] = float64(x%100000) / 100000.0
		}
		pts[i] = v
	}
	return pts
}

func main() {
	data := lcgPoints(300, 4, 90210)
	idx := NewIndex(4, 16, 64, 16, lcgUniforms(600, 555))
	for _, v := range data {
		idx.AddPoint(v)
	}

	fmt.Println("== 层级分布（M=16, mult=1/log M） ==")
	hist := map[int]int{}
	for _, lv := range idx.Levels {
		hist[lv]++
	}
	for lv := 0; lv <= idx.MaxLevel; lv++ {
		fmt.Printf("  level %d : %3d 点\n", lv, hist[lv])
	}
	l1, _ := GetRandomLevel(1.0/16.0, 1.0/math.Log(16.0))
	l2, _ := GetRandomLevel(1.0/256.0, 1.0/math.Log(16.0))
	fmt.Printf("  边界: getRandomLevel(1/M)=%d, getRandomLevel(1/M^2)=%d\n", l1, l2)

	fmt.Println("\n== 出度上限 ==")
	d0, du := 0, 0
	for i := range data {
		if n := len(idx.Links[i][0]); n > d0 {
			d0 = n
		}
		for lv := 1; lv <= idx.Levels[i]; lv++ {
			if n := len(idx.Links[i][lv]); n > du {
				du = n
			}
		}
	}
	fmt.Printf("  底层最大出度 %d (maxM0=%d)\n", d0, idx.MaxM0)
	fmt.Printf("  高层最大出度 %d (maxM=%d)\n", du, idx.MaxM)

	qs := lcgPoints(20, 4, 4242)
	fmt.Println("\n== k=1 时 ef 才不被 max(ef,k) 抬高 ==")
	for _, ef := range []int{1, 2, 8, 300} {
		hit := 0
		for _, q := range qs {
			got := idx.SearchKnn(q, 1, ef)
			want := BruteKnn(q, data, 1)
			if len(got) > 0 && got[0].Node == want[0].Node {
				hit++
			}
		}
		fmt.Printf("  ef=%4d  recall@1 = %.3f\n", ef, float64(hit)/float64(len(qs)))
	}

	fmt.Println("\n== 距离计算次数 ==")
	for _, ef := range []int{10, 64, 300} {
		idx.DistComputations = 0
		for _, q := range qs {
			idx.SearchKnn(q, 10, ef)
		}
		fmt.Printf("  ef=%4d  %d 次\n", ef, idx.DistComputations)
	}
}
