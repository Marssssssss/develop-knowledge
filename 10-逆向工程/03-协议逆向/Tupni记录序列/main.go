// Tupni demo 自检入口：go run . （自检失败即 panic）
package main

import "fmt"

const (
	aEip = "L7_cmp"
	bEip = "L9_type"
	cEip = "L11_size"
	dEip = "L12_hdlr"
	h1   = "hdlr1"
	h5   = "hdlr5"
)

func rng(lo, hi int) []int {
	out := []int{}
	for i := lo; i < hi; i++ {
		out = append(out, i)
	}
	return out
}

func trace() []Inst {
	t := []Inst{
		{aEip, rng(0, 4)},
		{bEip, rng(4, 6)}, {cEip, rng(6, 8)}, {h1, rng(8, 12)}, {dEip, rng(8, 16)},
		{aEip, rng(0, 4)},
		{bEip, rng(16, 18)}, {cEip, rng(18, 20)}, {h5, rng(20, 24)}, {dEip, rng(20, 24)},
		{aEip, rng(0, 4)},
		{bEip, rng(24, 26)}, {cEip, rng(26, 28)}, {h5, rng(28, 32)}, {dEip, rng(28, 32)},
		{aEip, rng(0, 4)},
	}
	// 干扰：整包批量访问 + 优化过的 32 位字符串处理
	return append(t, Inst{"bulk", rng(0, 32)}, Inst{"str32", rng(8, 12)}, Inst{"str32", rng(8, 12)})
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + " " + detail)
	}
	fmt.Printf("  ok  %-50s %s\n", label, detail)
}

func has(cs []Chunk, lo, hi int) bool {
	for _, c := range cs {
		if c.Lo == lo && c.Hi == hi {
			return true
		}
	}
	return false
}

func fmtChunks(cs []Chunk) string {
	s := ""
	for _, c := range cs {
		s += fmt.Sprintf("(%d,%d) ", c.Lo, c.Hi)
	}
	return s
}

func main() {
	full := trace()
	msgLen := 32
	w := buildChunks(full)
	check("num_records 被循环条件读 4 次 → chunk (0,4) 权重 4", w[Chunk{0, 4}] == 4, "")
	check("优化字符串访问产生重叠 chunk (8,12) 权重 3", w[Chunk{8, 12}] == 3, "")

	fields := greedyPacking(w)
	check("贪心选中 (8,12) 并挤掉重叠的 (8,16)",
		has(fields, 8, 12) && !has(fields, 8, 16), fmtChunks(fields))
	check("贪心选中 (0,4) 并挤掉覆盖整包的 (0,32)",
		has(fields, 0, 4) && !has(fields, 0, msgLen), "")
	check("未被访问的 [12,16) 成为 virtual field",
		has(virtualFields(fields, msgLen), 12, 16), fmtChunks(virtualFields(fields, msgLen)))

	iters := splitIterations(full[:len(full)-3], aEip)
	check("按入口点切成 4 段迭代", len(iters) == 4, "")
	I, n := iterationDependent(iters, fields)
	check("末次迭代 I_n 为空 → n 由 4 降到 3", n == 3, fmt.Sprintf("n=%d", n))
	check("循环条件指令 A 不是迭代相关", !I[0][aEip], "")
	check("B/C/D 都是迭代相关指令", I[0][bEip] && I[0][cEip] && I[0][dEip], "")

	bnd := findRecordBoundaries(n, I, fields, iters)
	check("s = [4,16,24]", fmt.Sprint(bnd.s) == "[4 16 24]", fmt.Sprint(bnd.s))
	check("e = [15,23,32]（前 n-1 个闭区间、末个开区间）",
		fmt.Sprint(bnd.e) == "[15 23 32]", fmt.Sprint(bnd.e))

	q1 := qiOf(iters[0], 4, 16)
	q2 := qiOf(iters[1], 16, 24)
	q3 := qiOf(iters[2], 24, 32)
	check("Q1 ≠ Q2（handler 不同）", fmt.Sprint(q1) != fmt.Sprint(q2), fmt.Sprint(q1))
	check("Q2 == Q3 → 记录 2、3 同类型", fmt.Sprint(q2) == fmt.Sprint(q3), fmt.Sprint(q2))
	raw := []string{"a", "x", "x", "x", "b"}
	seg := [][3]interface{}{{1, 4, "L1"}}
	check("子循环段被折叠成一条虚指令",
		fmt.Sprint(collapseChildLoops(raw, seg)) == "[a V:L1 b]",
		fmt.Sprint(collapseChildLoops(raw, seg)))

	check("循环条件依赖字段 → case (b)",
		determineLength(3, nil, 1) == "b", "")
	eq := [][3]interface{}{{1, 0, false}, {2, 0, false}, {3, 0, true}}
	check("末次等值比较成功 → case (a)", determineLength(3, eq, 0) == "a", "")
	check("两者皆无 → case (c)", determineLength(3, nil, 0) == "c", "")
	fmt.Println("Tupni(Go): ALL ASSERTIONS PASSED")
}
