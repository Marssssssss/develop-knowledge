package main

// Package main 连接树与三角化 —— 与 python/main.py 同题的 Go 实现（结构部分）。
//
// 依据（本轮实读）：
//   networkx/networkx@main : networkx/algorithms/chordal.py
//     - is_chordal：最大势搜索(MCS) + 「已编号邻居集必须是团」
//     - complete_to_chordal_graph：MCS-M（Berry et al. 2004）极小三角化
//     - chordal_graph_treewidth = 最大团大小 − 1（官方 doctest: barbell(4,6) → 3）
package main

import "sort"

// Graph 是无向图（保序）。
type Graph struct {
	Nodes []string
	Adj   map[string]map[string]bool
}

// NewGraph 建图。
func NewGraph(nodes []string, edges [][2]string) *Graph {
	g := &Graph{Nodes: append([]string{}, nodes...), Adj: map[string]map[string]bool{}}
	for _, n := range nodes {
		g.Adj[n] = map[string]bool{}
	}
	for _, e := range edges {
		g.AddEdge(e[0], e[1])
	}
	return g
}

// AddEdge 加边（忽略自环）。
func (g *Graph) AddEdge(u, v string) {
	if u == v {
		return
	}
	g.Adj[u][v] = true
	g.Adj[v][u] = true
}

// HasEdge 判相邻。
func (g *Graph) HasEdge(u, v string) bool { return g.Adj[u][v] }

// Degree 度数。
func (g *Graph) Degree(u string) int { return len(g.Adj[u]) }

// Edges 返回去重后的无向边。
func (g *Graph) Edges() [][2]string {
	out := [][2]string{}
	for _, u := range g.Nodes {
		for v := range g.Adj[u] {
			if u < v {
				out = append(out, [2]string{u, v})
			}
		}
	}
	return out
}

// Clone 深拷贝。
func (g *Graph) Clone() *Graph {
	c := &Graph{Nodes: append([]string{}, g.Nodes...), Adj: map[string]map[string]bool{}}
	for n, m := range g.Adj {
		nm := map[string]bool{}
		for k := range m {
			nm[k] = true
		}
		c.Adj[n] = nm
	}
	return c
}

// SubGraph 取子集诱导子图。
func (g *Graph) SubGraph(nodes []string) *Graph {
	keep := map[string]bool{}
	for _, n := range nodes {
		keep[n] = true
	}
	c := &Graph{Nodes: []string{}, Adj: map[string]map[string]bool{}}
	for _, n := range g.Nodes {
		if !keep[n] {
			continue
		}
		c.Nodes = append(c.Nodes, n)
		nm := map[string]bool{}
		for k := range g.Adj[n] {
			if keep[k] {
				nm[k] = true
			}
		}
		c.Adj[n] = nm
	}
	return c
}

// IsComplete 判断是否为完全图。

// BarbellGraph 两个 K_m1 用 m2 个结点的路相连。
func BarbellGraph(m1, m2 int) *Graph {
	nodes := []string{}
	edges := [][2]string{}
	for i := 0; i < m1; i++ {
		nodes = append(nodes, "L"+string(rune('0'+i)))
	}
	for i := 0; i < m2; i++ {
		nodes = append(nodes, "P"+string(rune('0'+i)))
	}
	for i := 0; i < m1; i++ {
		nodes = append(nodes, "R"+string(rune('0'+i)))
	}
	for pre := 'L'; ; pre = 'R' {
		for i := 0; i < m1; i++ {
			for j := i + 1; j < m1; j++ {
				edges = append(edges, [2]string{string(pre) + string(rune('0'+i)), string(pre) + string(rune('0'+j))})
			}
		}
		if pre == 'R' {
			break
		}
	}
	prev := "L0"
	for i := 0; i < m2; i++ {
		edges = append(edges, [2]string{prev, "P" + string(rune('0'+i))})
		prev = "P" + string(rune('0'+i))
	}
	edges = append(edges, [2]string{prev, "R0"})
	return NewGraph(nodes, edges)
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

func removeString(xs []string, x string) []string {
	for i, v := range xs {
		if v == x {
			return append(xs[:i], xs[i+1:]...)
		}
	}
	return xs
}
