// HNSW (Hierarchical Navigable Small World) 向量检索演示 —— Go 标准库版
//
// 参考：Malkov & Yashunin, "Efficient and robust approximate nearest neighbor
// search using Hierarchical Navigable Small World graphs", arXiv:1603.09320v4
//
// 多层图结构：高层稀疏"高速公路"，中层中等，底层（L0）最密；
// 插入时按指数衰减概率分配最高层；搜索自顶向下贪心传递。
package main

import (
	"fmt"
	"math"
	"math/rand"
	"sort"
)

const (
	M               = 4
	M0              = 2 * M
	EFConstruction  = 8
	EFSearch        = 16
	defaultSeed     = 42
)

// ML = 1/log(M) —— paper §4.1 的层数指数衰减因子
var ML = 1.0 / math.Log(float64(M))

// Node 含向量值、各层邻居、出现的最高层
type Node struct {
	ID        int
	Vec       []float64
	Level     int             // 出现的最高层 (0..Level)
	Neighbors [][]int         // Neighbors[lc] = 第 lc 层的邻居列表
}

// Vector 工具：欧氏距离
func euclid(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		d := a[i] - b[i]
		s += d * d
	}
	return math.Sqrt(s)
}

// assignLevel: paper alg.1 第 4 行
//   l = floor(−log(uniform) · mL)
func assignLevel(rng *rand.Rand) int {
	u := rng.Float64()
	if u <= 0 {
		u = 1e-12
	}
	return int(math.Floor(-math.Log(u) * ML))
}

// searchLayer: paper Algorithm 2
// 返回在 lc 层距 q 最近的 ≤ ef 个节点 id
func searchLayer(q []float64, entryPoints []int, ef, lc int, nodes map[int]*Node) []int {
	visited := make(map[int]bool, len(entryPoints)*8)
	// W = dynamic list (按距离降序)，W[0] = 最远
	W := make([][2]float64, 0, ef)
	for _, ep := range entryPoints {
		visited[ep] = true
		W = append(W, [2]float64{euclid(q, nodes[ep].Vec), float64(ep)})
	}
	sort.Slice(W, func(i, j int) bool { return W[i][0] > W[j][0] })

	// C = candidates (按距离升序)
	C := make([][2]float64, len(W))
	copy(C, W)
	sort.Slice(C, func(i, j int) bool { return C[i][0] < C[j][0] })

	for len(C) > 0 {
		c := C[0]
		C = C[1:]
		df := W[0][0]
		if c[0] > df && len(W) >= ef {
			break
		}
		cID := int(c[1])
		if lc >= len(nodes[cID].Neighbors) {
			continue
		}
		for _, e := range nodes[cID].Neighbors[lc] {
			if visited[e] {
				continue
			}
			visited[e] = true
			de := euclid(q, nodes[e].Vec)
			df = W[0][0]
			if de < df || len(W) < ef {
				C = append(C, [2]float64{de, float64(e)})
				W = append(W, [2]float64{de, float64(e)})
				sort.Slice(W, func(i, j int) bool { return W[i][0] > W[j][0] })
				sort.Slice(C, func(i, j int) bool { return C[i][0] < C[j][0] })
				if len(W) > ef {
					W = W[1:] // 移除最远（已在降序头部）
				}
			}
		}
	}
	out := make([]int, 0, len(W))
	for _, x := range W {
		out = append(out, int(x[1]))
	}
	return out
}

// knnSearch: paper Algorithm 5 K-NN-SEARCH
func knnSearch(q []float64, k int, nodes map[int]*Node, entry int) []int {
	ep := entry
	top := nodes[entry].Level
	for lc := top; lc > 0; lc-- {
		W := searchLayer(q, []int{ep}, 1, lc, nodes)
		if len(W) > 0 {
			ep = W[0]
		}
	}
	W := searchLayer(q, []int{ep}, EFSearch, 0, nodes)
	sort.Slice(W, func(i, j int) bool { // 按距离（对每个再算一次）
		return euclid(q, nodes[W[i]].Vec) < euclid(q, nodes[W[j]].Vec)
	})
	if len(W) > k {
		W = W[:k]
	}
	return W
}

// insert: paper Algorithm 1（精简版）
func insert(vid int, vec []float64, rng *rand.Rand, nodes map[int]*Node, entry int) (int, int) {
	level := assignLevel(rng)
	n := &Node{
		ID:   vid,
		Vec:  vec,
		Level: level,
	}
	n.Neighbors = make([][]int, level+1)
	for i := range n.Neighbors {
		n.Neighbors[i] = []int{}
	}
	nodes[vid] = n

	if entry < 0 {
		return 0, vid
	}

	top := nodes[entry].Level
	ep := entry
	// phase 1：自顶向下，每层 ef=1 找 entry
	for lc := top; lc > level; lc-- {
		W := searchLayer(vec, []int{ep}, 1, lc, nodes)
		if len(W) > 0 {
			ep = W[0]
		}
	}
	// phase 2：层 0..level 找邻居集，双向连边
	for lc := level; lc >= 0; lc-- {
		W := searchLayer(vec, []int{ep}, EFConstruction, lc, nodes)
		cap := M
		if lc == 0 {
			cap = M0
		}
		// 排序后取前 cap 个作为邻居
		sort.Slice(W, func(i, j int) bool {
			return euclid(vec, nodes[W[i]].Vec) < euclid(vec, nodes[W[j]].Vec)
		})
		if len(W) > cap {
			W = W[:cap]
		}
		n.Neighbors[lc] = append(n.Neighbors[lc], W...)
		for _, nbID := range W {
			nb := nodes[nbID]
			if lc >= len(nb.Neighbors) {
				continue
			}
			nb.Neighbors[lc] = append(nb.Neighbors[lc], vid)
			// 裁剪
			cap2 := M
			if lc == 0 {
				cap2 = M0
			}
			if len(nb.Neighbors[lc]) > cap2 {
				sort.Slice(nb.Neighbors[lc], func(i, j int) bool {
					return euclid(nb.Vec, nodes[nb.Neighbors[lc][i]].Vec) <
						euclid(nb.Vec, nodes[nb.Neighbors[lc][j]].Vec)
				})
				nb.Neighbors[lc] = nb.Neighbors[lc][:cap2]
			}
		}
	}
	// 最高层变化时换 entry
	if level > top {
		return 0, vid
	}
	return 0, entry
}

func bruteForce(q []float64, k int, nodes map[int]*Node) []int {
	type d struct {
		id int
		d  float64
	}
	dists := make([]d, 0, len(nodes))
	for id, n := range nodes {
		dists = append(dists, d{id, euclid(q, n.Vec)})
	}
	sort.Slice(dists, func(i, j int) bool { return dists[i].d < dists[j].d })
	out := make([]int, 0, k)
	for i := 0; i < k && i < len(dists); i++ {
		out = append(out, dists[i].id)
	}
	return out
}

func recallAtK(pred, truth []int) float64 {
	set := map[int]bool{}
	for _, v := range truth {
		set[v] = true
	}
	hits := 0
	for _, v := range pred {
		if set[v] {
			hits++
		}
	}
	return float64(hits) / float64(len(truth))
}

func main() {
	rng := rand.New(rand.NewSource(defaultSeed))
	nodes := map[int]*Node{}
	entry := -1

	pts := [][]float64{
		{0.1, 0.1}, {0.2, 0.4}, {0.3, 0.0}, {0.4, 0.3},
		{0.5, 0.5}, {0.6, 0.2}, {0.7, 0.4}, {0.8, 0.1},
		{0.9, 0.5}, {1.0, 0.3}, {0.4, 0.7}, {0.5, 0.9},
		{0.6, 0.8}, {0.8, 0.7}, {0.9, 0.8}, {1.0, 0.9},
		{0.3, 0.6}, {0.2, 0.8}, {0.1, 0.5}, {0.0, 0.3},
	}

	fmt.Println("Demo 1 · 逐点插入 + 分配层")
	fmt.Printf("  M=%d  M0=%d  efConstruction=%d  efSearch=%d  mL=%.3f\n",
		M, M0, EFConstruction, EFSearch, ML)
	fmt.Printf("  %3s %14s %5s %s\n", "id", "vec", "level", "top-layer neighbors")
	for i, p := range pts {
		_, e := insert(i, p, rng, nodes, entry)
		entry = e
		n := nodes[i]
		var ns []int
		if n.Level >= 0 && n.Level < len(n.Neighbors) {
			ns = n.Neighbors[n.Level]
		}
		fmt.Printf("  %3d %v %5d neighbors=%v @ L%d\n", i, p, n.Level, ns, n.Level)
	}

	queries := [][]float64{{0.45, 0.45}, {0.0, 0.0}, {0.95, 0.85}, {0.15, 0.65}}
	total := 0.0
	for qid, q := range queries {
		pred := knnSearch(q, 3, nodes, entry)
		truth := bruteForce(q, 3, nodes)
		r := recallAtK(pred, truth)
		total += r
		fmt.Printf("\n  q[%d] = %v (entry id = %d)\n", qid, q, entry)
		printDists := func(arr []int) {
			for _, id := range arr {
				fmt.Printf("    id=%d dist=%.3f | ", id, euclid(q, nodes[id].Vec))
			}
			fmt.Println()
		}
		fmt.Print("    暴力 top-3: ")
		printDists(truth)
		fmt.Print("    HNSW  top-3: ")
		printDists(pred)
		fmt.Printf("    recall@3 = %.2f\n", r)
	}
	fmt.Printf("\n  平均 recall@3 = %.3f\n", total/float64(len(queries)))

	fmt.Println("\nDemo 3 · 层级分布（高层稀疏 = 长程跳转）")
	counts := map[int]int{}
	for _, n := range nodes {
		counts[n.Level]++
	}
	for lvl := 0; lvl <= 5; lvl++ {
		if cnt, ok := counts[lvl]; ok {
			fmt.Printf("  L%d: %3d node(s) %s\n", lvl, cnt, bar(cnt))
		}
	}
	fmt.Println("  (节点数指数衰减 ⇒ 高层 = 高速公路)")
}

func bar(n int) string {
	s := ""
	for i := 0; i < n; i++ {
		s += "█"
	}
	return s
}
