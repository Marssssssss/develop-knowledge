// 量化路径模式 (Quantified Path Patterns) 最小实现 (Go 版).
// 语义同 Python 版, 依据 Neo4j Cypher Manual "Variable-length paths":
// {min,max} 展开为并集 / 组变量收集 / 关系唯一性 / 内联谓词剪枝.
package main

import "fmt"

type Rel struct {
	Type  string
	Src   string
	Dst   string
	Props map[string]float64
}

type Graph struct {
	Rel   map[int]Rel
	Next  int
	Out   map[string][]int // node -> rid
	In    map[string][]int
	Nodes map[string]bool
}

func NewGraph() *Graph {
	return &Graph{Rel: map[int]Rel{}, Out: map[string][]int{}, In: map[string][]int{}, Nodes: map[string]bool{}}
}

func (g *Graph) AddEdge(src, dst, etype string, distance float64) int {
	rid := g.Next
	g.Next++
	g.Rel[rid] = Rel{etype, src, dst, map[string]float64{"distance": distance}}
	g.Out[src] = append(g.Out[src], rid)
	g.In[dst] = append(g.In[dst], rid)
	g.Nodes[src], g.Nodes[dst] = true, true
	return rid
}

func (g *Graph) otherEnd(rid int, from string) string {
	r := g.Rel[rid]
	if r.Src == from {
		return r.Dst
	}
	return r.Src
}

type Bindings struct {
	Rels  []int    // 组变量 r
	Nodes []string // 组变量 m(不含起点)
	End   string   // 终点单例
}

// MatchQuantified 匹配 (()-[etype]-()){lo,hi}, direction: "out"/"in"/"both".
// inlinePred 为 nil 时不剪枝; 返回 true 表示提前停止.
func MatchQuantified(g *Graph, start, etype, direction string, lo, hi int,
	inlinePred func(rid int, r Rel, node string) bool, limit int) []Bindings {
	var results []Bindings
	var pathRels []int
	var pathNodes []string
	used := map[int]bool{} // 关系唯一性: 一条路径内关系不得重复
	var stop bool

	var adj func(node string) []int
	if direction == "out" {
		adj = func(n string) []int { return g.Out[n] }
	} else if direction == "in" {
		adj = func(n string) []int { return g.In[n] }
	} else {
		adj = func(n string) []int {
			ids := append([]int{}, g.Out[n]...)
			return append(ids, g.In[n]...)
		}
	}

	var dfs func(cur string, depth int)
	dfs = func(cur string, depth int) {
		if stop {
			return
		}
		if depth >= lo {
			nodes := make([]string, len(pathNodes))
			copy(nodes, pathNodes)
			rels := make([]int, len(pathRels))
			copy(rels, pathRels)
			results = append(results, Bindings{rels, nodes, cur})
			if limit > 0 && len(results) >= limit {
				stop = true
				return
			}
		}
		if depth == hi {
			return
		}
		for _, rid := range adj(cur) {
			r := g.Rel[rid]
			if r.Type != etype || used[rid] {
				continue // 类型过滤 + 关系唯一性
			}
			nxt := g.otherEnd(rid, cur)
			if inlinePred != nil && !inlinePred(rid, r, nxt) {
				continue // 内联谓词: 遍历时剪枝, 整棵子树被砍掉
			}
			used[rid] = true
			pathRels = append(pathRels, rid)
			pathNodes = append(pathNodes, nxt)
			dfs(nxt, depth+1)
			pathRels = pathRels[:len(pathRels)-1]
			pathNodes = pathNodes[:len(pathNodes)-1]
			used[rid] = false
			if stop {
				return
			}
		}
	}
	dfs(start, 0)
	return results
}

func buildRailway() *Graph {
	g := NewGraph()
	g.AddEdge("dk", "cj", "LINK", 2.0)  // 直连 1 跳
	g.AddEdge("dk", "x", "LINK", 0.5)   // 绕行 3 跳
	g.AddEdge("x", "y", "LINK", 0.5)
	g.AddEdge("y", "cj", "LINK", 0.5)
	g.AddEdge("x", "dk", "LINK", 0.9) // 环形支线: 验证关系唯一性
	return g
}

func main() {
	g := buildRailway()

	fmt.Println("== 1. {1,3} 展开: 1 跳 + 3 跳两条路径都命中 ==")
	for _, b := range MatchQuantified(g, "dk", "LINK", "out", 1, 3, nil, 0) {
		total := 0.0
		for _, rid := range b.Rels {
			total += g.Rel[rid].Props["distance"]
		}
		fmt.Printf("  dk -%d 跳-> %s  rels=%v 距离=%.1f\n", len(b.Rels), b.End, b.Rels, total)
	}

	fmt.Println("\n== 2. 无上界 + 环: 关系唯一性保证有限结果 ==")
	un := MatchQuantified(g, "dk", "LINK", "out", 1, 1<<30, nil, 0)
	fmt.Printf("  路径数 = %d (若无关系唯一性, 环 x->dk 会导致无限展开)\n", len(un))

	fmt.Println("\n== 3. 组变量绑定: nodes 不含起点单例 ==")
	b := MatchQuantified(g, "dk", "LINK", "out", 3, 3, nil, 0)[0]
	fmt.Printf("  nodes(组变量)=%v rels=%v end(单例)=%s\n", b.Nodes, b.Rels, b.End)

	fmt.Println("\n== 4. 路径爆炸与内联谓词剪枝 ==")
	g2 := NewGraph()
	for i := 0; i < 3; i++ {
		for a := 0; a < 3; a++ {
			for c := 0; c < 3; c++ {
				g2.AddEdge(fmt.Sprintf("L%dn%d", i, a), fmt.Sprintf("L%dn%d", i+1, c), "E", 0)
			}
		}
	}
	all := MatchQuantified(g2, "L0n0", "E", "out", 3, 3, nil, 0)
	fmt.Printf("  无谓词: 3 层全连接网格 3 跳路径数 = %d (指数爆炸)\n", len(all))
	pruned := MatchQuantified(g2, "L0n0", "E", "out", 3, 3, func(rid int, r Rel, node string) bool {
		return node != "L3n2" // 剪掉通往 L3n2 的分支
	}, 0)
	fmt.Printf("  内联谓词剪掉 L3n2 分支后 = %d 条 (遍历时剪枝, 非先枚举再过滤)\n", len(pruned))

	fmt.Println("\n== 5. 上界收紧: {1,1} 等价于固定长度 1 跳 ==")
	for _, b := range MatchQuantified(g, "dk", "LINK", "out", 1, 1, nil, 0) {
		fmt.Printf("  dk -> %s\n", b.End)
	}
}
