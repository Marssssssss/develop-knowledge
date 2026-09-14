// cfg_graph.go —— 图结构、逆后序（RPO）与迭代式支配者求解
//
// 自 dominator.go 拆出（OPTIMIZATION.md §1.1「单源代码文件 ≤ 300 行」）：
// 本文件放"图 + 支配树"基础设施，回边/自然循环/可归约性/分析与 main
// 在 dominator.go，两者同属 package main。
//
// 参考：Nystrom (Cornell) §2.1.1-2.1.2（Lengauer-Tarjan O(E·α(E,V))）
package main

import (
	"sort"
	"strings"
)

// Graph：节点用 0..n-1 编号，Names 仅用于展示
type Graph struct {
	Names []string
	Succs [][]int
	Preds [][]int
}

func newGraph(names []string, edges [][2]int) *Graph {
	g := &Graph{
		Names: names,
		Succs: make([][]int, len(names)),
		Preds: make([][]int, len(names)),
	}
	for _, e := range edges {
		u, v := e[0], e[1]
		g.Succs[u] = append(g.Succs[u], v)
		g.Preds[v] = append(g.Preds[v], u)
	}
	return g
}

func (g *Graph) nm(n int) string { return g.Names[n] }

func (g *Graph) show(set []int) string {
	parts := make([]string, 0, len(set))
	for _, n := range set {
		parts = append(parts, g.nm(n))
	}
	return "{" + strings.Join(parts, ", ") + "}"
}

// ------------------------------------------------------- 逆后序（RPO）

func reversePostorder(g *Graph, entry int) []int {
	seen := make([]bool, len(g.Names))
	order := make([]int, 0, len(g.Names))
	var dfs func(n int)
	dfs = func(n int) {
		seen[n] = true
		for _, s := range g.Succs[n] {
			if !seen[s] {
				dfs(s)
			}
		}
		order = append(order, n)
	}
	dfs(entry)
	// 反转即逆后序
	for i, j := 0, len(order)-1; i < j; i, j = i+1, j-1 {
		order[i], order[j] = order[j], order[i]
	}
	return order
}

// --------------------------------------------------- 迭代式支配者求解

// intersect 沿支配树同时上溯，遇到的第一个公共节点即 idom 候选
func intersect(a, b int, idom []int, rpoIdx []int) int {
	for a != b {
		for rpoIdx[a] > rpoIdx[b] {
			a = idom[a]
		}
		for rpoIdx[b] > rpoIdx[a] {
			b = idom[b]
		}
	}
	return a
}

// computeIdom 返回每个节点的立即支配者；-1 表示未定义（不可达或根）
func computeIdom(g *Graph, entry int) []int {
	rpo := reversePostorder(g, entry)
	rpoIdx := make([]int, len(g.Names))
	for i := range rpoIdx {
		rpoIdx[i] = -1
	}
	for i, n := range rpo {
		rpoIdx[n] = i
	}

	idom := make([]int, len(g.Names))
	for i := range idom {
		idom[i] = -1
	}
	idom[entry] = entry

	changed := true
	for changed {
		changed = false
		for _, n := range rpo {
			if n == entry {
				continue
			}
			first := true
			newIdom := -1
			for _, p := range g.Preds[n] {
				if idom[p] == -1 {
					continue
				}
				if first {
					newIdom = p
					first = false
					continue
				}
				newIdom = intersect(newIdom, p, idom, rpoIdx)
			}
			if newIdom != -1 && idom[n] != newIdom {
				idom[n] = newIdom
				changed = true
			}
		}
	}
	return idom
}

// dominatorsOf 从 n 沿 idom 上溯到根，得到 n 的全部支配者（含自身）
func dominatorsOf(n int, idom []int) []int {
	out := []int{n}
	cur := n
	for idom[cur] != cur && idom[cur] != -1 {
		cur = idom[cur]
		out = append(out, cur)
	}
	sort.Ints(out)
	return out
}

func contains(xs []int, v int) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}
