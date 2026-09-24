// FST 的 TopN 搜索与零输出补全 —— Go 侧演示入口（与 python/main.py 同题）。
package main

import (
	"fmt"
	"math/rand"
	"sort"
)

func main() {
	entries := [][2]interface{}{
		{[]int{1, 2}, 5}, {[]int{1, 3}, 7}, {[]int{9}, 2},
	}

	fmt.Println("[1] 输出前推：让每个非根节点都有一条零输出弧")
	raw := Build(entries, false)
	fst := Build(entries, true)
	fmt.Printf("    前推前零弧不变式=%v  前推后=%v\n",
		raw.CheckZeroArcInvariant(), fst.CheckZeroArcInvariant())
	fmt.Printf("    前推不改变路径输出: %v\n", sameOutputs(raw, fst))

	fmt.Println("\n[2] top-N 搜索（按 output 升序取最小的 N 个）")
	for _, tn := range []int{1, 2, 3} {
		r, _ := search(fst, tn, 50, false)
		fmt.Printf("    topN=%d → %v isComplete=%v\n", tn, fmtResults(r), r.IsComplete)
	}

	fmt.Println("\n[3] 队列深度不够会丢候选")
	for _, d := range []int{1, 2, 3, 50} {
		r, _ := search(fst, 3, d, false)
		fmt.Printf("    topN=3 depth=%-2d → 收到 %d 条 isComplete=%v\n",
			d, len(r.Results), r.IsComplete)
	}

	fmt.Println("\n[4] 同分按 input 字典序")
	tie := Build([][2]interface{}{{[]int{2}, 0}, {[]int{1}, 0}, {[]int{3}, 0}}, true)
	r, _ := search(tie, 3, 50, false)
	fmt.Printf("    顺序=%v\n", inputs(r))

	fmt.Println("\n[5] 空串只在 allowEmptyString=true 时入队")
	empty := Build([][2]interface{}{{[]int{}, 4}, {[]int{7}, 9}}, true)
	r0, _ := search(empty, 5, 50, false)
	r1, _ := search(empty, 5, 50, true)
	fmt.Printf("    false → %v\n    true  → %v\n", fmtResults(r0), fmtResults(r1))

	fmt.Println("\n[6] 零弧不变式被破坏 → assert foundZero")
	bad := NewFST()
	mid := &Node{}
	bad.Root.AddArc(1, NoOutput, mid)
	mid.AddArc(EndLabel, 5, &Node{})
	sb := NewTopNSearcher(bad, 1, 5)
	sb.AddStartPaths(bad.Root, NoOutput, []int{}, false)
	if _, err := sb.Search(); err != nil {
		fmt.Printf("    error: %v\n", err)
	}

	fmt.Println("\n[7] 大样本对拍")
	rnd := rand.New(rand.NewSource(11))
	big := [][2]interface{}{}
	for i := 0; i < 12; i++ {
		big = append(big, [2]interface{}{
			[]int{rnd.Intn(5) + 1, rnd.Intn(5) + 1}, rnd.Intn(51)})
	}
	fbig := Build(big, true)
	ap := AllPaths(fbig)
	outs := []int{}
	for _, p := range ap {
		outs = append(outs, p[1].(int))
	}
	sort.Ints(outs)
	rb, _ := search(fbig, len(ap), 200, false)
	got := []int{}
	for _, r := range rb.Results {
		got = append(got, r.Output)
	}
	sort.Ints(got)
	fmt.Printf("    路径 %d 条，全量搜索 %d 条，一致=%v；暴力最小的 3 个=%v\n",
		len(ap), len(rb.Results), equalInts(got, outs), outs[:3])
}

func search(fst *FST, topN, depth int, allowEmpty bool) (*TopResults, *TopNSearcher) {
	s := NewTopNSearcher(fst, topN, depth)
	s.AddStartPaths(fst.Root, NoOutput, []int{}, allowEmpty)
	r, _ := s.Search()
	return r, s
}

func fmtResults(r *TopResults) string {
	out := ""
	for _, x := range r.Results {
		out += fmt.Sprintf("(%v,%d) ", x.Input, x.Output)
	}
	return out
}

func inputs(r *TopResults) []int {
	out := []int{}
	for _, x := range r.Results {
		out = append(out, x.Input[0])
	}
	return out
}

func sameOutputs(a, b *FST) bool {
	pa, pb := AllPaths(a), AllPaths(b)
	if len(pa) != len(pb) {
		return false
	}
	key := func(ps [][2]interface{}) []string {
		out := []string{}
		for _, p := range ps {
			out = append(out, fmt.Sprintf("%v=%d", p[0], p[1]))
		}
		sort.Strings(out)
		return out
	}
	ka, kb := key(pa), key(pb)
	for i := range ka {
		if ka[i] != kb[i] {
			return false
		}
	}
	return true
}

func equalInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
