// Package main 实现 Louvain 社区发现与模块度（modularity）最小模型。
//
// 依据 Blondel et al., Fast unfolding of communities in large networks
// (arXiv:0803.0476v2) 原文：
//   - 式(1) Q = 1/(2m) Σ_ij [A_ij − k_i k_j/(2m)] δ(c_i, c_j)
//   - 式(2) ΔQ = [(Σin + k_i,in)/(2m) − ((Σtot + k_i)/(2m))²]
//     − [Σin/(2m) − (Σtot/(2m))² − (k_i/(2m))²]
//   - 两阶段迭代：局部移动到局部极大 → 把社区聚合成超节点（内部边变自环）。
//
// 自环约定：聚合图的自环在邻接矩阵里记为 A_ii = 2·s_i，只有这样才能同时满足
// m = 全部边权之和（自环按一次计）与「聚合前后 Q 不变」。
//
// 同时参考 Neo4j GDS Louvain 文档的参数语义：maxLevels / maxIterations /
// tolerance / includeIntermediateCommunities / consecutiveIds。
package main

import "sort"

// Graph 是无向加权图，自环单独存。
type Graph struct {
	Nodes    []int
	w        map[[2]int]float64
	selfLoop map[int]float64
}

// NewGraph 返回空图。
func NewGraph() *Graph {
	return &Graph{w: map[[2]int]float64{}, selfLoop: map[int]float64{}}
}

func (g *Graph) addNode(n int) {
	for _, x := range g.Nodes {
		if x == n {
			return
		}
	}
	g.Nodes = append(g.Nodes, n)
	sort.Ints(g.Nodes)
}

// AddEdge 加一条无向边；a == b 时记为自环。
func (g *Graph) AddEdge(a, b int, w float64) {
	g.addNode(a)
	g.addNode(b)
	if a == b {
		g.selfLoop[a] += w
		return
	}
	k := [2]int{a, b}
	if a > b {
		k = [2]int{b, a}
	}
	g.w[k] += w
}

// A 是邻接取值；i == j 时返回 2·s_i（自环约定）。
func (g *Graph) A(i, j int) float64 {
	if i == j {
		return 2.0 * g.selfLoop[i]
	}
	k := [2]int{i, j}
	if i > j {
		k = [2]int{j, i}
	}
	return g.w[k]
}

// AOnce 是反例口径：自环只记一次。仅用于反向断言。
func (g *Graph) AOnce(i, j int) float64 {
	if i == j {
		return g.selfLoop[i]
	}
	return g.A(i, j)
}

// K 是 k_i = Σ_j A_ij。
func (g *Graph) K(i int) float64 {
	s := 0.0
	for _, j := range g.Nodes {
		s += g.A(i, j)
	}
	return s
}

// M 是 m = (1/2) Σ_ij A_ij。
func (g *Graph) M() float64 {
	s := 0.0
	for _, i := range g.Nodes {
		for _, j := range g.Nodes {
			s += g.A(i, j)
		}
	}
	return s / 2.0
}

// MOnce 用 AOnce 计算 m（反例口径）。
func (g *Graph) MOnce() float64 {
	s := 0.0
	for _, i := range g.Nodes {
		for _, j := range g.Nodes {
			s += g.AOnce(i, j)
		}
	}
	return s / 2.0
}

// Neighbors 返回 i 的邻居。
func (g *Graph) Neighbors(i int) []int {
	out := []int{}
	for _, j := range g.Nodes {
		if j != i && g.A(i, j) > 0 {
			out = append(out, j)
		}
	}
	return out
}

func groupBy(part map[int]int) map[int][]int {
	out := map[int][]int{}
	for n, c := range part {
		out[c] = append(out[c], n)
	}
	for c := range out {
		sort.Ints(out[c])
	}
	return out
}

func sigmaIn(g *Graph, members []int) float64 {
	s := 0.0
	for _, x := range members {
		for _, y := range members {
			s += g.A(x, y)
		}
	}
	return s
}

func sigmaTot(g *Graph, members []int) float64 {
	s := 0.0
	for _, x := range members {
		s += g.K(x)
	}
	return s
}

// Modularity 计算 Q = Σ_C [Σin(C)/(2m) − (Σtot(C)/(2m))²]。
func Modularity(g *Graph, part map[int]int) float64 {
	m := g.M()
	if m == 0 {
		return 0.0
	}
	q := 0.0
	for _, members := range groupBy(part) {
		q += sigmaIn(g, members)/(2*m) - pow2(sigmaTot(g, members)/(2*m))
	}
	return q
}

func pow2(x float64) float64 { return x * x }

// DeltaQAdd 实现式(2)：把孤立节点 i 移入社区 target（不含 i）的增益。
func DeltaQAdd(g *Graph, part map[int]int, i int, target []int) float64 {
	m := g.M()
	sin := sigmaIn(g, target)
	stot := sigmaTot(g, target)
	ki := g.K(i)
	kiIn := 0.0
	for _, x := range target {
		kiIn += g.A(i, x) + g.A(x, i)
	}
	after := (sin+kiIn)/(2*m) - pow2((stot+ki)/(2*m))
	before := sin/(2*m) - pow2(stot/(2*m)) - pow2(ki/(2*m))
	return after - before
}
