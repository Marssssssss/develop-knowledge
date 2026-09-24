// 模糊查询与编辑距离自动机 —— Go 侧演示入口（与 python/main.py 同题）。
package main

import "fmt"

func main() {
	fmt.Println("[1] FuzzyQuery 默认值与上限")
	q, _ := NewFuzzyQuery("lucene", DefaultMaxEdits, DefaultPrefixLength,
		DefaultMaxExpansions, DefaultTranspositions)
	fmt.Printf("    maxEdits=%d prefixLength=%d maxExpansions=%d transpositions=%v\n",
		q.MaxEdits, q.PrefixLength, q.MaxExpansions, q.Transpositions)
	fmt.Printf("    MAXIMUM_SUPPORTED_DISTANCE=%d\n", MaximumSupportedDistance)
	q0, _ := NewFuzzyQuery("x", 0, 0, 50, true)
	fmt.Printf("    maxEdits=0 -> %s\n", q0.TermsEnumKind())
	for _, c := range []struct {
		me, pl, mx int
		label      string
	}{{3, 0, 50, "maxEdits 越界"}, {1, -1, 50, "prefixLength 为负"},
		{1, 0, 0, "maxExpansions 非正"}} {
		_, err := NewFuzzyQuery("x", c.me, c.pl, c.mx, true)
		fmt.Printf("    %-16s -> %v\n", c.label, err)
	}

	fmt.Println("\n[2] FloatToEdits：相似度 -> 编辑距离")
	for _, c := range []struct {
		sim float32
		tl  int
	}{{0.0, 10}, {0.5, 10}, {0.7, 10}, {0.8, 10}, {0.9, 5}, {1.0, 10}, {5.0, 10}} {
		fmt.Printf("    similarity=%-4v termLen=%-3d -> maxEdits=%d\n",
			c.sim, c.tl, FloatToEdits(c.sim, c.tl))
	}

	fmt.Println("\n[3] transpositions 开关：OSA vs 经典 Levenshtein")
	for _, c := range []struct{ a, b string }{{"ab", "ba"}, {"ca", "abc"}, {"kitten", "sitting"}} {
		ra, rb := []rune(c.a), []rune(c.b)
		fmt.Printf("    %-8q -> %-10q 经典=%d OSA=%d\n",
			c.a, c.b, Levenshtein(ra, rb), DamerauOSA(ra, rb))
	}

	word := []int{}
	for _, c := range "abca" {
		word = append(word, int(c))
	}
	la, _ := NewLevenshteinAutomata(word, CharacterMaxCodePoint, true)
	fmt.Printf("\n[4] LevenshteinAutomata('abca'): alphabet=%v numRanges=%d\n",
		la.Alphabet, la.NumRanges)
	for i := 0; i < la.NumRanges; i++ {
		fmt.Printf("    补集区间 [%d, %d]\n", la.RangeLower[i], la.RangeUpper[i])
	}

	fmt.Println("\n[5] 参数化描述：状态数 = |minErrors| x (w+1)")
	for _, c := range []struct {
		name string
		d    *ParametricDescription
	}{{"Lev1 ", NewLev1(4)}, {"Lev1T", NewLev1T(4)}, {"Lev2 ", NewLev2(4)}} {
		fmt.Printf("    %s w=4: |minErrors|=%-3d -> size=%-4d 接受态 %d 个\n",
			c.name, len(c.d.MinErrors), c.d.Size(), len(c.d.AcceptStates()))
	}

	fmt.Println("\n[6] ToAutomaton 的计划量")
	fmt.Printf("    n=0 -> %+v\n", la.ToAutomatonPlan(0, ""))
	for _, n := range []int{1, 2} {
		p := la.ToAutomatonPlan(n, "")
		fmt.Printf("    n=%d: range=%d states=%d transitions=%d\n",
			n, p.Range, p.NumStates, p.NumTransitions)
	}
	fmt.Printf("    n=3 -> %s\n", la.ToAutomatonPlan(3, "").Kind)

	fmt.Println("\n[7] 特征向量（先左移再判等置位）")
	for _, x := range "abcz" {
		v, end := la.VectorWindow(int(x), 0, 1)
		fmt.Printf("    X(%q, 0, %d) = %03b\n", x, end, v)
	}
}
