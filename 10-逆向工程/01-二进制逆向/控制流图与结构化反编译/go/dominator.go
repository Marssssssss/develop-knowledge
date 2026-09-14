// dominator.go —— 回边、自然循环、可归约性判定与对拍分析
//
// 与 cfg_builder.py 同题异构：Python 版从指令流切块并做完整结构恢复，
// Go 版聚焦"支配树"这一枢纽——从邻接表出发，求逆后序、迭代式 idom、
// 支配树、回边、自然循环、可归约性判定，并用两个图对拍：
//   图 A：单入口循环（可归约）      图 B：多入口循环（不可归约）
// 图结构与支配树基础设施在 cfg_graph.go（同 package main）。
//
// 用法：go run dominator.go cfg_graph.go
// 参考：Nystrom (Cornell) §2.1.1-2.1.2（Lengauer-Tarjan O(E·α(E,V))）
package main

import (
	"fmt"
	"sort"
	"strings"
)

// --------------------------------------------------- 回边与自然循环

// backEdges：回边 = (n -> h) 且 h 支配 n。
// 注意 DFS 祖先关系只是必要条件，支配性才是定义。
func backEdges(g *Graph, idom []int) [][2]int {
	var out [][2]int
	for u := range g.Succs {
		for _, v := range g.Succs[u] {
			if u == v || contains(dominatorsOf(u, idom), v) {
				out = append(out, [2]int{u, v})
			}
		}
	}
	return out
}

// naturalLoop：节点集 = {h} ∪ {所有能不经过 h 到达 n 的节点}
func naturalLoop(g *Graph, n, h int) map[int]bool {
	loop := map[int]bool{h: true, n: true}
	stack := []int{n}
	for len(stack) > 0 {
		m := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		for _, p := range g.Preds[m] {
			if !loop[p] {
				loop[p] = true
				if p != h {
					stack = append(stack, p)
				}
			}
		}
	}
	return loop
}

func loopMembers(loop map[int]bool) []int {
	out := make([]int, 0, len(loop))
	for k := range loop {
		out = append(out, k)
	}
	sort.Ints(out)
	return out
}

// loopEntries：外部入口 —— 源在循环外、目标在循环内的边的目标集合
func loopEntries(g *Graph, loop map[int]bool) []int {
	set := map[int]bool{}
	for u := range g.Succs {
		if loop[u] {
			continue
		}
		for _, v := range g.Succs[u] {
			if loop[v] {
				set[v] = true
			}
		}
	}
	out := make([]int, 0, len(set))
	for k := range set {
		out = append(out, k)
	}
	sort.Ints(out)
	return out
}

// ------------------------------------------------------- 可归约性判定

// dfsSpan：DFS 的 pre/post 编号，用于判定"回退边"（retreating edge）
func dfsSpan(g *Graph, entry int) (pre, post []int) {
	pre = make([]int, len(g.Names))
	post = make([]int, len(g.Names))
	for i := range pre {
		pre[i] = -1
		post[i] = -1
	}
	counter := 0
	var dfs func(n int)
	dfs = func(n int) {
		pre[n] = counter
		counter++
		for _, s := range g.Succs[n] {
			if pre[s] == -1 {
				dfs(s)
			}
		}
		post[n] = counter
		counter++
	}
	dfs(entry)
	return pre, post
}

// checkReducible：可归约 ⟺ 所有回退边都是回边
func checkReducible(g *Graph, entry int, idom []int) (bool, [][2]int) {
	pre, post := dfsSpan(g, entry)
	var bad [][2]int
	for u := range g.Succs {
		if pre[u] == -1 {
			continue
		}
		for _, v := range g.Succs[u] {
			if pre[v] == -1 {
				continue
			}
			retreating := pre[v] <= pre[u] && post[v] >= post[u]
			isBack := u == v || contains(dominatorsOf(u, idom), v)
			if retreating && !isBack {
				bad = append(bad, [2]int{u, v})
			}
		}
	}
	return len(bad) == 0, bad
}

// --------------------------------------------------------------- 分析

func analyze(title string, g *Graph, entry int, note string) {
	fmt.Println(strings.Repeat("=", 70))
	fmt.Printf("%s\n", title)
	fmt.Println(strings.Repeat("=", 70))
	if note != "" {
		fmt.Printf("说明：%s\n", note)
	}

	fmt.Println("\n邻接表（B 为基本块）：")
	for i := range g.Names {
		succ := make([]string, 0, len(g.Succs[i]))
		for _, s := range g.Succs[i] {
			succ = append(succ, g.nm(s))
		}
		if len(succ) == 0 {
			succ = []string{"(exit)"}
		}
		fmt.Printf("  %-4s -> %s\n", g.nm(i), strings.Join(succ, ", "))
	}

	rpo := reversePostorder(g, entry)
	fmt.Printf("\n逆后序 RPO：%s\n", g.show(rpo))

	idom := computeIdom(g, entry)
	fmt.Println("\n支配关系 与 支配树：")
	for i := range g.Names {
		if idom[i] == -1 {
			fmt.Printf("  %-4s 不可达\n", g.nm(i))
			continue
		}
		parent := "(根)"
		if idom[i] != i {
			parent = g.nm(idom[i])
		}
		fmt.Printf("  %-4s dom=%-22s idom=%-5s\n",
			g.nm(i), g.show(dominatorsOf(i, idom)), parent)
	}

	bes := backEdges(g, idom)
	fmt.Println()
	if len(bes) == 0 {
		fmt.Println("回边（h 支配 n）：无")
	} else {
		fmt.Println("回边（h 支配 n）：")
		for _, e := range bes {
			loop := naturalLoop(g, e[0], e[1])
			ents := loopEntries(g, loop)
			verdict := "单入口 ✓ 可归约循环"
			if len(ents) != 1 {
				verdict = "多入口 ✗"
			}
			fmt.Printf("  %s -> %s   自然循环 %s  外部入口 %s  %s\n",
				g.nm(e[0]), g.nm(e[1]), g.show(loopMembers(loop)),
				g.show(ents), verdict)
		}
	}

	ok, bad := checkReducible(g, entry, idom)
	fmt.Println()
	if ok {
		fmt.Println("可归约性：可归约 ✓（所有回退边都是回边）")
		fmt.Println("  → 可用 while/if 表达，循环头唯一")
	} else {
		fmt.Println("可归约性：不可归约 ✗")
		for _, e := range bad {
			fmt.Printf("  违规边（是 DFS 回退边但非支配性回边）：%s -> %s\n",
				g.nm(e[0]), g.nm(e[1]))
		}
		fmt.Println("  → 循环存在多个外部入口，结构化模板不适用；")
		fmt.Println("     实际反编译器在此输出 goto，或先做节点分裂（node splitting）")
	}
	fmt.Println()
}

func main() {
	// 图 A：单入口循环
	//   B0 -> B1 -> {B3, B2} ; B2 -> B1 ; B3 出口
	//   B2 -> B1 是回边，循环头 B1，唯一入口 B1 → 可归约
	gA := newGraph(
		[]string{"B0", "B1", "B2", "B3"},
		[][2]int{{0, 1}, {1, 3}, {1, 2}, {2, 1}},
	)
	analyze("图 A：单入口循环（可归约）", gA, 0,
		"0x02 处 jz 跳出、0x08 处 jmp 回跳，是结构化 while 的典型形态")

	// 图 B：多入口循环（不可归约）
	//   B0 -> {B2, B1} ; B1 -> B3 ; B2 -> B1 ; B3 -> B2
	//   循环 {B1,B2,B3} 有两个外部入口（B1 与 B2）→ 不可归约
	gB := newGraph(
		[]string{"B0", "B1", "B2", "B3"},
		[][2]int{{0, 2}, {0, 1}, {1, 3}, {2, 1}, {3, 2}},
	)
	analyze("图 B：多入口循环（不可归约）", gB, 0,
		"两个分支分别跳进循环体的不同位置，是 goto 或优化器造成的典型不可归约形态")

	fmt.Println(strings.Repeat("=", 70))
	fmt.Println("要点回顾：")
	fmt.Println("  · 支配树是枢纽：回边判定、循环头、follow 节点全部依赖 idom")
	fmt.Println("  · 回边 ≠ DFS 回边：定义要求 h 支配 n，DFS 祖先只是必要条件")
	fmt.Println("  · 可归约 ⟺ 所有回退边都是回边 ⟺ 循环只有唯一入口")
	fmt.Println("  · 朴素迭代 O(V^2)，生产实现用 Lengauer-Tarjan O(E·α(E,V))")
	fmt.Println(strings.Repeat("=", 70))
}
