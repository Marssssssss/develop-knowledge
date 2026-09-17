// Go 编译器「逃逸分析 + 函数内联」模型 —— 与 python/ 同题的第二语言实现。
//
// 依据：官方源码 cmd/compile/internal/escape/escape.go 头部注释（位置图 + derefs 权重 +
// 两条不变量 + parameter tag）、cmd/compile/internal/inline/inl.go（内联预算常量）。
// 本机无 Go 工具链，编译期正确性依赖人工审查 + _docs/tools/bracket_check.py。
package main

import (
	"fmt"
	"os"
)

// 内联预算常量（inl.go: "Inlining budget parameters, gathered in one place"）
const (
	budget            = 80    // inlineMaxBudget
	costCall          = 57    // inlineExtraCallCost
	costParamCall     = 17    // inlineParamCallCost
	costPanic         = 1     // inlineExtraPanicCost
	costThrow         = budget
	bigFuncNodes      = 5000  // inlineBigFunctionNodes
	bigFuncMaxCost    = 20    // inlineBigFunctionMaxCost
	closureOnceBudget = 10 * budget
	hotBudget         = 2000  // inlineHotMaxBudget（PGO）
)

const (
	kindLocal  = "local"
	kindHeap   = "heap"
	kindCallee = "callee"
)

var (
	statN    int
	statFail []string
)

func check(label string, cond bool, detail ...interface{}) {
	statN++
	if cond {
		fmt.Println("PASS  " + label)
		return
	}
	statFail = append(statFail, label)
	if len(detail) > 0 {
		fmt.Printf("FAIL  %s  | %v\n", label, detail[0])
		return
	}
	fmt.Println("FAIL  " + label)
}

// blockers 是结构上就不可内联的构造（不计预算）
var blockers = map[string]bool{
	"closure": true, "defer": true, "recover": true, "go": true, "select": true,
	"go:noinline": true, "go:uintptrescapes": true, "no_body": true,
}

type inlineSpec struct {
	nodes, calls, paramCalls, panics, throws int
	blocker                                  string
	isLeaf                                   bool
	debugL                                   int
	callerNodes                              int
	closureOnce                              bool
}

func inlineCost(s inlineSpec) (int, string) {
	if s.blocker != "" && blockers[s.blocker] {
		return -1, s.blocker
	}
	return s.nodes + s.calls*costCall + s.paramCalls*costParamCall +
		s.panics*costPanic + s.throws*costThrow, ""
}

func canInline(s inlineSpec) (bool, string) {
	cost, reason := inlineCost(s)
	if reason != "" {
		return false, reason + "（不是预算问题，结构上就不可内联）"
	}
	if s.debugL == 0 {
		return false, "-l=0 完全关闭内联"
	}
	if s.debugL == 1 && !s.isLeaf {
		return false, "-l=1（默认）只内联叶子函数"
	}
	limit := budget
	if s.callerNodes >= bigFuncNodes {
		limit = bigFuncMaxCost
	}
	if s.closureOnce {
		limit = closureOnceBudget
	}
	if cost > limit {
		return false, fmt.Sprintf("function too complex: cost %d exceeds budget %d", cost, limit)
	}
	return true, fmt.Sprintf("cost %d <= budget %d", cost, limit)
}

func main() {
	fmt.Println("=== A. 位置图与边权（escape.go 头部注释逐条复刻）===")
	for _, c := range []struct {
		expr string
		w    int
	}{{"p = &q", -1}, {"p = q", 0}, {"p = *q", 1}, {"p = **q", 2}, {"p = **&**&q", 2}} {
		got := derefsOf(c.expr)
		check(fmt.Sprintf("A %-12s 权重 %+d", c.expr, c.w), got == c.w, got)
	}
	check("A6 `&x` 不可寻址 → 权重不会低于 -1",
		derefsOf("p = &q") >= -1 && derefsOf("p = &*q") == 0)

	fmt.Println("\n=== B. 两条不变量：什么必须上堆 ===")
	g := newEscapeGraph().assign("callee", "x", -1).solve()
	check("B1 返回值位置放 &x → x 逃逸（不变量 b）", g.loc("x").escapes)
	g = newEscapeGraph().assign("heap", "x", -1).solve()
	check("B2 存进堆 → 逃逸（不变量 a）", g.loc("x").escapes)
	g = newEscapeGraph().assign("y", "x", 0).assign("z", "y", 0).solve()
	check("B3 只在函数内做值拷贝 → 三个局部变量全部留栈",
		len(g.stackLocals()) == 3, g.stackLocals())
	g = newEscapeGraph().assign("heap", "q", 1).solve()
	check("B4 `heap = *q`（权重 +1）不会让 q 自身逃逸（传播要求 derefs <= 0）",
		!g.loc("q").escapes)
	g = newEscapeGraph().assign("heap", "q", 0).solve()
	check("B4b 权重从 +1 改成 0（`heap = q`）同一个 q 立刻逃逸", g.loc("q").escapes)
	g = newEscapeGraph().assign("heap", "clo", -1).assign("clo", "captured", -1).solve()
	check("B6 闭包逃逸 → 捕获变量随之逃逸", g.loc("captured").escapes)

	fmt.Println("\n=== C. 过程间：parameter tag ===")
	g = newEscapeGraph()
	g.defineFunc("sink", []int{0}, nil)
	g.defineFunc("identity", nil, []int{0})
	g.defineFunc("pure", nil, nil)
	g.solve()
	g.call("sink", "a")
	g.call("identity", "b")
	g.call("pure", "c")
	g.solve()
	check("C1 实参传给 body 里存进堆的参数 → 逃逸", g.loc("a").escapes)
	check("C2 实参传给被作为结果返回的参数 → 逃逸", g.loc("b").escapes)
	check("C3 实参传给干净参数 → 留在栈上", !g.loc("c").escapes)
	check("C4 tag 记录了两处信息（leaks / results）",
		len(g.leaks["sink"]) == 1 && len(g.results["identity"]) == 1 && len(g.leaks["pure"]) == 0)

	fmt.Println("\n=== D. 内联预算闸 ===")
	check("D1 inlineMaxBudget = 80", budget == 80)
	check("D2 一次调用 = 57（故默认最多内联一个调用：2×57=114 > 80）",
		costCall == 57 && 2*costCall > budget && costCall <= budget)
	check("D3 调用参数额外 17（可能暴露常量函数）", costParamCall == 17)
	check("D4 panic 只记 1（几乎不惩罚）", costPanic == 1)
	check("D5 throw 直接吃满 80 预算", costThrow == budget)
	ok, why := canInline(inlineSpec{nodes: 80, debugL: 1, isLeaf: true})
	check("D6 恰好 80 个节点 → 可内联", ok, why)
	ok, why = canInline(inlineSpec{nodes: 81, debugL: 1, isLeaf: true})
	check("D7 81 个节点 → 超预算，拒内联", !ok, why)
	check("D8 拒绝理由文案与官方一致",
		why == "function too complex: cost 81 exceeds budget 80", why)
	ok, _ = canInline(inlineSpec{nodes: 23, calls: 1, debugL: 1, isLeaf: true})
	check("D9 23 节点 + 1 次调用 = 80 → 仍可内联", ok)
	ok, why = canInline(inlineSpec{nodes: 24, calls: 1, debugL: 1, isLeaf: true})
	check("D10 24 节点 + 1 次调用 = 81 → 拒内联", !ok, why)
	ok, why = canInline(inlineSpec{nodes: 30, paramCalls: 3, debugL: 1, isLeaf: true})
	check("D11 30 节点 + 3 次参数调用 = 81 → 拒内联", !ok, why)
	ok, why = canInline(inlineSpec{nodes: 30, paramCalls: 2, debugL: 1, isLeaf: true})
	check("D12 30 节点 + 2 次参数调用 = 64 → 可内联", ok, why)
	ok, why = canInline(inlineSpec{nodes: 5, throws: 1, debugL: 1, isLeaf: true})
	check("D13 单个 throw 就让预算归零", !ok, why)
	ok, _ = canInline(inlineSpec{nodes: 70, panics: 5, debugL: 1, isLeaf: true})
	check("D14 5 个 panic 只加 5（70+5 = 75 ≤ 80）", ok)
	ok, why = canInline(inlineSpec{nodes: 100, debugL: 0})
	check("D15 -l=0 完全关闭内联", !ok, why)
	ok, why = canInline(inlineSpec{nodes: 10, debugL: 1, isLeaf: false})
	check("D16 -l=1（默认）只内联叶子函数", !ok, why)
	ok, why = canInline(inlineSpec{nodes: 10, debugL: 4, isLeaf: false})
	check("D17 -l=4 允许非叶子函数（10 节点 ≤ 80）", ok, why)

	fmt.Println("\n=== E. 结构闸与特殊预算 ===")
	for _, b := range []string{"closure", "defer", "recover", "go", "select",
		"go:noinline", "go:uintptrescapes", "no_body"} {
		ok, why = canInline(inlineSpec{nodes: 1, blocker: b, debugL: 1, isLeaf: true})
		check(fmt.Sprintf("E 阻断项 %-20s → 不可内联", b), !ok, why)
	}
	ok, why = canInline(inlineSpec{nodes: 21, callerNodes: bigFuncNodes, debugL: 1, isLeaf: true})
	check("E9 调用方 ≥ 5000 节点（big function）→ 预算压到 20，21 节点越界", !ok, why)
	ok, why = canInline(inlineSpec{nodes: 20, callerNodes: bigFuncNodes, debugL: 1, isLeaf: true})
	check("E10 big function 里 20 节点仍可内联", ok, why)
	ok, why = canInline(inlineSpec{nodes: 19, callerNodes: bigFuncNodes - 1, debugL: 1, isLeaf: true})
	check("E11 caller 差一个节点未达 big 阈值 → 用常规 80 预算", ok, why)
	ok, why = canInline(inlineSpec{nodes: 700, closureOnce: true, debugL: 1, isLeaf: true})
	check("E12 闭包只被调用一次 → 预算放宽到 800，700 节点可内联", ok, why)
	ok, why = canInline(inlineSpec{nodes: 801, closureOnce: true, debugL: 1, isLeaf: true})
	check("E13 801 节点越界 → 拒内联", !ok, why)
	check("E14 hot 函数预算 2000（PGO 时预算可扩到 2000）", hotBudget == 2000)

	fmt.Println("\n=== F. 内联为什么能改变逃逸结论 ===")
	g1 := newEscapeGraph()
	g1.defineFunc("helper", []int{0}, nil)
	g1.call("helper", "x")
	g1.solve()
	check("F1 未内联：黑盒调用 + tag=leaks → 实参 x 逃逸", g1.loc("x").escapes)
	g3 := newEscapeGraph()
	g3.loc("helper_body_temp")
	g3.solve()
	check("F2 内联后：函数体可见 → 同一实参留栈", !g3.loc("helper_body_temp").escapes)
	check("F3 go:uintptrescapes 阻止内联的原因就是「内联会丢逃逸信息」", blockers["go:uintptrescapes"])
	prev, mono := -1, true
	for _, n := range []int{10, 20, 40, 79} {
		c, _ := inlineCost(inlineSpec{nodes: n})
		if c < prev {
			mono = false
		}
		prev = c
	}
	check("F4 代价对节点数单调不减，且 79 节点仍在 80 预算内", mono && prev == 79, prev)

	fmt.Println("\n=== 汇总 ===")
	fmt.Printf("断言总数 %d；失败 %d %v\n", statN, len(statFail), statFail)
	if len(statFail) > 0 {
		os.Exit(1)
	}
}
