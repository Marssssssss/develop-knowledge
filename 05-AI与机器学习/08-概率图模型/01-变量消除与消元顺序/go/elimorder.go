// Package main 变量消除与消元顺序启发式 —— 与 python/main.py 同题的 Go 实现。
//
// 依据（本轮实读）：
//   pgmpy/pgmpy@dev : pgmpy/inference/EliminationOrder.py（贪心框架 + 七类代价）
//   pgmpy/pgmpy@dev : pgmpy/inference/ExactInference.py（induced_width = 最大团 − 1）
//   networkx/networkx@main : networkx/algorithms/chordal.py（弦图判定 = MCS + 前缀团检查）
package main

import "sort"

// Graph 是保序的无向图：nodes 的插入序即平局裁决顺序（与 pgmpy 用 networkx 插入序一致）。
type Graph struct {
	nodes []string
	adj   map[string]map[string]bool
	idx   map[string]int
}

// NewGraph 按 nodes 的给定顺序建图（重复边无害）。
func NewGraph(nodes []string, edges [][2]string) *Graph {
	g := &Graph{nodes: append([]string{}, nodes...), adj: map[string]map[string]bool{}, idx: map[string]int{}}
	for i, n := range nodes {
		g.adj[n] = map[string]bool{}
		g.idx[n] = i
	}
	for _, e := range edges {
		g.AddEdge(e[0], e[1])
	}
	return g
}

// AddEdge 加一条无向边（自环被忽略）。
func (g *Graph) AddEdge(u, v string) {
	if u == v {
		return
	}
	g.adj[u][v] = true
	g.adj[v][u] = true
}

// HasEdge 判断 u、v 是否相邻。
func (g *Graph) HasEdge(u, v string) bool {
	return g.adj[u][v]
}

// Neighbors 按结点插入序返回邻居 —— fill-in 边的配对顺序由此决定。
func (g *Graph) Neighbors(node string) []string {
	out := []string{}
	for _, n := range g.nodes {
		if g.adj[node][n] {
			out = append(out, n)
		}
	}
	return out
}

// Degree 返回度数。
func (g *Graph) Degree(node string) int {
	return len(g.adj[node])
}

// RemoveNode 删点及其所有边。
func (g *Graph) RemoveNode(node string) {
	for n := range g.adj[node] {
		delete(g.adj[n], node)
	}
	delete(g.adj, node)
	for i, n := range g.nodes {
		if n == node {
			g.nodes = append(g.nodes[:i], g.nodes[i+1:]...)
			break
		}
	}
}

// Clone 深拷贝（消除顺序会改图）。
func (g *Graph) Clone() *Graph {
	c := &Graph{nodes: append([]string{}, g.nodes...), adj: map[string]map[string]bool{}, idx: g.idx}
	for n, s := range g.adj {
		m := map[string]bool{}
		for k := range s {
			m[k] = true
		}
		c.adj[n] = m
	}
	return c
}

// Moralize 贝叶斯网 → 道德图：骨架 + 每个结点全体父结点两两连边。
func Moralize(nodes []string, directed [][2]string) *Graph {
	g := NewGraph(nodes, directed)
	parents := map[string][]string{}
	for _, e := range directed {
		parents[e[1]] = append(parents[e[1]], e[0])
	}
	for _, ps := range parents {
		for i := 0; i < len(ps); i++ {
			for j := i + 1; j < len(ps); j++ {
				g.AddEdge(ps[i], ps[j])
			}
		}
	}
	return g
}

// FillInEdges 返回删除 node 后需要补的边：邻居两两组合中尚无边的一对。
func FillInEdges(g *Graph, node string) [][2]string {
	nb := g.Neighbors(node)
	out := [][2]string{}
	for i := 0; i < len(nb); i++ {
		for j := i + 1; j < len(nb); j++ {
			if !g.HasEdge(nb[i], nb[j]) {
				out = append(out, [2]string{nb[i], nb[j]})
			}
		}
	}
	return out
}

// CostFunc 是某个启发式对「消掉 node」的估价。
type CostFunc func(g *Graph, card map[string]int, node string) float64

func weightOf(card map[string]int, nodes []string) float64 {
	w := 1.0
	for _, n := range nodes {
		if c, ok := card[n]; ok {
			w *= float64(c)
		}
	}
	return w
}

// CostMinFill 数边：要补几条边。
func CostMinFill(g *Graph, card map[string]int, node string) float64 {
	return float64(len(FillInEdges(g, node)))
}

// CostMinNeighbors 度数。
func CostMinNeighbors(g *Graph, card map[string]int, node string) float64 {
	return float64(g.Degree(node))
}

// CostMinWeight 邻居基数乘积。
func CostMinWeight(g *Graph, card map[string]int, node string) float64 {
	return weightOf(card, g.Neighbors(node))
}

// CostWeightedMinFill 加权数边：每条要补的边按两端基数乘积计价。
func CostWeightedMinFill(g *Graph, card map[string]int, node string) float64 {
	total := 0.0
	for _, e := range FillInEdges(g, node) {
		total += weightOf(card, []string{e[0], e[1]})
	}
	return total
}

// EliminationOrder 贪心取当前代价最小的结点；平局取 remaining 中靠前者。
// nodes 为空表示消全部结点；非空时只做集合筛选，顺序仍由图结点序决定。
func EliminationOrder(g *Graph, card map[string]int, nodes []string, cost CostFunc) []string {
	work := g.Clone()
	remaining := []string{}
	if len(nodes) == 0 {
		remaining = append(remaining, work.nodes...)
	} else {
		keep := map[string]bool{}
		for _, n := range nodes {
			keep[n] = true
		}
		for _, n := range work.nodes {
			if keep[n] {
				remaining = append(remaining, n)
			}
		}
	}
	order := []string{}
	for len(remaining) > 0 {
		best := remaining[0]
		for _, n := range remaining[1:] {
			if cost(work, card, n) < cost(work, card, best) {
				best = n
			}
		}
		order = append(order, best)
		for i, n := range remaining {
			if n == best {
				remaining = append(remaining[:i], remaining[i+1:]...)
				break
			}
		}
		for _, e := range FillInEdges(work, best) {
			work.AddEdge(e[0], e[1])
		}
		work.RemoveNode(best)
	}
	return order
}

// FillCount 给定顺序实际补出的边总数（不删点的三角化口径）。
func FillCount(g *Graph, order []string) int {
	work := g.Clone()
	total := 0
	for _, node := range order {
		add := FillInEdges(work, node)
		total += len(add)
		for _, e := range add {
			work.AddEdge(e[0], e[1])
		}
		work.RemoveNode(node)
	}
	return total
}

// InducedWidth pgmpy 口径：团 = CPD 作用域 ∪ 每步消元产生的中间作用域；
// 已含被消变量的因子不再参与后续消元（每个因子只计一次）。
func InducedWidth(scopes [][]string, order []string) int {
	cliques := map[string][]string{}
	key := func(c []string) string {
		s := append([]string{}, c...)
		sort.Strings(s)
		out := ""
		for _, n := range s {
			out += n + ","
		}
		return out
	}
	working := map[string][][]string{}
	for _, phi := range scopes {
		cliques[key(phi)] = phi
		for _, v := range phi {
			working[v] = append(working[v], phi)
		}
	}
	eliminated := map[string]bool{}
	for _, v := range order {
		kept := [][]string{}
		for _, f := range working[v] {
			skip := false
			for _, x := range f {
				if eliminated[x] {
					skip = true
				}
			}
			if !skip {
				kept = append(kept, f)
			}
		}
		phi := []string{}
		seen := map[string]bool{}
		for _, f := range kept {
			for _, x := range f {
				if x != v && !seen[x] {
					seen[x] = true
					phi = append(phi, x)
				}
			}
		}
		cliques[key(phi)] = phi
		delete(working, v)
		for _, x := range phi {
			working[x] = append(working[x], phi)
		}
		eliminated[v] = true
	}
	// 团集合 → 图 → 最大团
	nodes := []string{}
	seen := map[string]bool{}
	edges := [][2]string{}
	for _, c := range cliques {
		sort.Strings(c)
		for _, n := range c {
			if !seen[n] {
				seen[n] = true
				nodes = append(nodes, n)
			}
		}
		for i := 0; i < len(c); i++ {
			for j := i + 1; j < len(c); j++ {
				edges = append(edges, [2]string{c[i], c[j]})
			}
		}
	}
	sort.Strings(nodes)
	ig := NewGraph(nodes, edges)
	best := 0
	for _, c := range MaximalCliques(ig) {
		if len(c) > best {
			best = len(c)
		}
	}
	return best - 1
}
