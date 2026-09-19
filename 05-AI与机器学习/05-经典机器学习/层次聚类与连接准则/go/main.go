// 层次聚类自检(Go 侧):Lance-Williams 递推 vs 逐步直接重算、Ward 的 ΔSSE、
// 合并次数、树状图切割、链式效应。
package main

import (
	"fmt"
	"math"
	"os"
)

var total, passed int
var fails []string

func check(name string, cond bool, detail string) {
	total++
	if cond {
		passed++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
	} else {
		fails = append(fails, name)
		fmt.Printf("  [FAIL] %s  %s\n", name, detail)
	}
}

type rng struct{ st uint64 }

func newRNG(seed uint64) *rng { return &rng{st: seed} }

func (r *rng) next() float64 {
	r.st = r.st*6364136223846793005 + 1442695040888963407
	return float64(r.st>>11) / float64(1<<53)
}

func points(n int, seed uint64) [][]float64 {
	r := newRNG(seed)
	X := make([][]float64, n)
	for i := range X {
		X[i] = []float64{3 * (r.next()*2 - 1), 3 * (r.next()*2 - 1)}
	}
	return X
}

func sse(X [][]float64, mem []int) float64 {
	if len(mem) <= 1 {
		return 0
	}
	d := len(X[0])
	c := make([]float64, d)
	for _, i := range mem {
		for t := 0; t < d; t++ {
			c[t] += X[i][t]
		}
	}
	for t := range c {
		c[t] /= float64(len(mem))
	}
	s := 0.0
	for _, i := range mem {
		for t := 0; t < d; t++ {
			v := X[i][t] - c[t]
			s += v * v
		}
	}
	return s
}

// naiveHeights 对照实现:每步从原始点直接重算全部簇间距离。
func naiveHeights(X [][]float64, linkage string) []float64 {
	probe := &Agg{Linkage: linkage, X: X}
	groups := [][]int{}
	for i := range X {
		groups = append(groups, []int{i})
	}
	hs := []float64{}
	for len(groups) > 1 {
		bi, bj, bv := 0, 1, math.Inf(1)
		for a := 0; a < len(groups); a++ {
			for b := a + 1; b < len(groups); b++ {
				if v := probe.PairDist(groups[a], groups[b]); v < bv-1e-15 {
					bi, bj, bv = a, b, v
				}
			}
		}
		hs = append(hs, bv)
		merged := append(append([]int{}, groups[bi]...), groups[bj]...)
		ng := [][]int{}
		for i, g := range groups {
			if i != bi && i != bj {
				ng = append(ng, g)
			}
		}
		groups = append(ng, merged)
	}
	return hs
}

func main() {
	fmt.Println("层次聚类与连接准则 自检(Go)")
	fmt.Println("==========================================================================")
	X := points(9, 7)
	linkages := []string{"single", "complete", "average", "ward"}

	// A. 递推 vs 直接重算
	for _, lk := range linkages {
		g := &Agg{Linkage: lk}
		g.Fit(X)
		nh := naiveHeights(X, lk)
		ok := len(nh) == len(g.Merges)
		if ok {
			for i := range nh {
				if math.Abs(nh[i]-g.Merges[i].h) > 1e-9 {
					ok = false
				}
			}
		}
		check("A-"+lk+":合并高度序列与朴素版一致", ok, fmt.Sprintf("%d 步", len(nh)))
	}

	// B. Ward 的 ΔSSE 闭式
	mi, mj := []int{0, 1, 2, 3, 4}, []int{5, 6, 7}
	gain := wardGain(X, mi, mj)
	direct := sse(X, append(append([]int{}, mi...), mj...)) - sse(X, mi) - sse(X, mj)
	check("B1 ΔSSE 闭式 == 合并前后簇内平方和之差", math.Abs(gain-direct) < 1e-9,
		fmt.Sprintf("%.9f vs %.9f", gain, direct))

	// C. 合并次数与更新次数
	gw := &Agg{Linkage: "ward"}
	gw.Fit(X)
	check("C1 恰好 n−1 次合并", len(gw.Merges) == len(X)-1,
		fmt.Sprintf("%d / n=%d", len(gw.Merges), len(X)))
	exp := 0
	for i := 0; i < len(X)-1; i++ {
		exp += len(X) - i - 2
	}
	check("C2 距离更新次数 == Σ(活跃簇数−2)", gw.Updates == exp,
		fmt.Sprintf("%d vs %d", gw.Updates, exp))

	// D. 树状图切割
	for _, k := range []int{1, 2, 3, 5, 9} {
		lab := CutTree(gw.Merges, len(X), k)
		cnt := map[int]int{}
		for _, l := range lab {
			cnt[l]++
		}
		check(fmt.Sprintf("D-k=%d:恰好 %d 个簇", k, k), len(cnt) == k,
			fmt.Sprintf("簇数=%d", len(cnt)))
	}

	// E. 链式效应
	r := newRNG(21)
	Xb := [][]float64{}
	for c := 0; c < 15; c++ {
		Xb = append(Xb, []float64{-4 + (r.next()-0.5)*0.5, (r.next() - 0.5) * 0.5})
	}
	for c := 0; c < 15; c++ {
		Xb = append(Xb, []float64{4 + (r.next()-0.5)*0.5, (r.next() - 0.5) * 0.5})
	}
	for t := 0; t < 9; t++ {
		Xb = append(Xb, []float64{-1.6 + 0.4*float64(t), 0.0})
	}
	joinH := map[string]float64{}
	for _, lk := range linkages {
		g := &Agg{Linkage: lk}
		g.Fit(Xb)
		snap := map[int][]int{}
		for i := range Xb {
			snap[i] = []int{i}
		}
		for step, m := range g.Merges {
			merged := append(append([]int{}, snap[m.a]...), snap[m.b]...)
			inA, inB := false, false
			for _, i := range merged {
				if i < 15 {
					inA = true
				} else if i < 30 {
					inB = true
				}
			}
			if inA && inB {
				joinH[lk] = m.h
				break
			}
			delete(snap, m.a)
			delete(snap, m.b)
			snap[len(Xb)+step] = merged
		}
	}
	check("E1 single 靠桥连起来,合并高度远小于 complete",
		joinH["single"]*3 < joinH["complete"],
		fmt.Sprintf("single=%.4f complete=%.4f", joinH["single"], joinH["complete"]))
	check("E2 ward 要很高代价才肯合并两团",
		joinH["single"]*3 < joinH["ward"],
		fmt.Sprintf("single=%.4f ward=%.4f", joinH["single"], joinH["ward"]))

	fmt.Println("--------------------------------------------------------------------------")
	fmt.Printf("断言 %d/%d 通过\n", passed, total)
	if len(fails) > 0 {
		fmt.Println("失败项:", fails)
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
