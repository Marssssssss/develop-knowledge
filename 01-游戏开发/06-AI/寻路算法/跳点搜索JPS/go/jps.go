// Package main 是 JPS（Jump Point Search）的 Go 转写，按 Harabor & Grastien (AAAI 2011) 原文实现。
//
// 对应论文：
//   §Neighbour Pruning Rules 的式 (1)（直线，<=）与式 (2)（对角，<）
//   Definition 1（forced neighbour）、Definition 2（jump point）、Definition 3（turning point）
//   Algorithm 1（Identify Successors）、Algorithm 2（Function jump）
//
// 语言差异显式落地：
//   - 论文用 1..8 给邻居编号（图 2 的 numpad 布局），这里保留同一套编号以便对照 Figure 2；
//   - 「绕开 x 的最短路」用 3x3 邻域内的 BFS 精确计算，走不到用 +Inf 表示，
//     这正是 forced 判据成立与否的依据（Rust/Python 侧同样如此）；
//   - 直线代价 1、对角代价 sqrt(2)。
package main

import (
	"container/heap"
	"math"
)

var sqrt2 = math.Sqrt(2.0)

// 图 2 的邻居编号（numpad 布局，y 轴向下）
var nb = map[int][2]int{
	1: {-1, -1}, 2: {0, -1}, 3: {1, -1},
	4: {-1, 0}, 5: {1, 0},
	6: {-1, 1}, 7: {0, 1}, 8: {1, 1},
}

var idx = map[[2]int]int{}

func init() {
	for i, d := range nb {
		idx[d] = i
	}
}

func octile(a, b [2]int) float64 {
	dx, dy := abs(a[0]-b[0]), abs(a[1]-b[1])
	lo, hi := dx, dy
	if dy < lo {
		lo = dy
	}
	if dx > hi {
		hi = dx
	}
	return sqrt2*float64(lo) + float64(hi-lo)
}

func abs(v int) int {
	if v < 0 {
		return -v
	}
	return v
}

// ---------------------------------------------------------------- 网格

type grid struct {
	w, h     int
	blocked  map[[2]int]bool
}

func newGrid(w, h int, blocked [][2]int) *grid {
	b := map[[2]int]bool{}
	for _, p := range blocked {
		b[p] = true
	}
	return &grid{w: w, h: h, blocked: b}
}

func (g *grid) walkable(p [2]int) bool {
	if p[0] < 0 || p[1] < 0 || p[0] >= g.w || p[1] >= g.h {
		return false
	}
	return !g.blocked[p]
}

func (g *grid) neighbours(x [2]int) []int {
	out := []int{}
	for _, i := range []int{1, 2, 3, 4, 5, 6, 7, 8} {
		if g.walkable([2]int{x[0] + nb[i][0], x[1] + nb[i][1]}) {
			out = append(out, i)
		}
	}
	return out
}

// ---------------------------------------------------------------- 剪枝规则

func naturalNeighbours(d [2]int) map[int]bool {
	di := idx[d]
	if d[0] != 0 && d[1] != 0 {
		return map[int]bool{di: true, idx[[2]int{d[0], 0}]: true, idx[[2]int{0, d[1]}]: true}
	}
	return map[int]bool{di: true}
}

// pathWithoutX：len(<p(x),...,n>\x)，只用 neighbours(x) 且不经过 x
func pathWithoutX(g *grid, x, p, n [2]int) float64 {
	nodes := [][2]int{x}
	for _, i := range []int{1, 2, 3, 4, 5, 6, 7, 8} {
		q := [2]int{x[0] + nb[i][0], x[1] + nb[i][1]}
		if g.walkable(q) {
			nodes = append(nodes, q)
		}
	}
	has := func(t [2]int) bool {
		for _, q := range nodes {
			if q == t {
				return true
			}
		}
		return false
	}
	if !has(p) || !has(n) {
		return math.Inf(1)
	}
	dist := map[[2]int]float64{p: 0}
	queue := [][2]int{p}
	for len(queue) > 0 {
		cur := queue[0]
		queue = queue[1:]
		if cur == n {
			return dist[cur]
		}
		for _, other := range nodes {
			if other == x || other == cur {
				continue
			}
			dx, dy := abs(other[0]-cur[0]), abs(other[1]-cur[1])
			if dx > 1 || dy > 1 {
				continue
			}
			cost := 1.0
			if dx == 1 && dy == 1 {
				cost = sqrt2
			}
			nd := dist[cur] + cost
			if cur, ok := dist[other]; !ok || nd < cur {
				dist[other] = nd
				queue = append(queue, other)
			}
		}
	}
	return math.Inf(1)
}

// forcedNeighbours：Definition 1
func forcedNeighbours(g *grid, x, p [2]int, d [2]int) []int {
	natural := naturalNeighbours(d)
	out := []int{}
	for _, i := range g.neighbours(x) {
		if natural[i] {
			continue
		}
		n := [2]int{x[0] + nb[i][0], x[1] + nb[i][1]}
		withX := octile(p, x) + octile(x, n)
		if withX < pathWithoutX(g, x, p, n) {
			out = append(out, i)
		}
	}
	return out
}

// ---------------------------------------------------------------- Algorithm 2

func jump(g *grid, x [2]int, d [2]int, goal [2]int) ([2]int, bool) {
	n := [2]int{x[0] + d[0], x[1] + d[1]}
	if !g.walkable(n) {
		return n, false
	}
	if n == goal { // 条件 1
		return n, true
	}
	if len(forcedNeighbours(g, n, x, d)) > 0 { // 条件 2
		return n, true
	}
	if d[0] != 0 && d[1] != 0 { // 条件 3：先试两个正交方向
		for _, di := range [][2]int{{d[0], 0}, {0, d[1]}} {
			if _, ok := jump(g, n, di, goal); ok {
				return n, true
			}
		}
	}
	return jump(g, n, d, goal)
}

// identifySuccessors：Algorithm 1
func identifySuccessors(g *grid, x [2]int, parent *[2]int, goal [2]int) [][2]int {
	out := [][2]int{}
	for _, i := range []int{1, 2, 3, 4, 5, 6, 7, 8} {
		n := [2]int{x[0] + nb[i][0], x[1] + nb[i][1]}
		if !g.walkable(n) {
			continue
		}
		if parent != nil {
			tx, ty := x[0]-parent[0], x[1]-parent[1]
			travel := [2]int{sign(tx), sign(ty)}
			natural := naturalNeighbours(travel)
			if !natural[i] && !contains(forcedNeighbours(g, x, *parent, travel), i) {
				continue
			}
		}
		if jp, ok := jump(g, x, nb[i], goal); ok {
			out = append(out, jp)
		}
	}
	return out
}

func sign(v int) int {
	if v > 0 {
		return 1
	}
	if v < 0 {
		return -1
	}
	return 0
}

func contains(list []int, v int) bool {
	for _, x := range list {
		if x == v {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- 搜索（跳点图上的 A*）

type item struct {
	node [2]int
	f    float64
	seq  int
}

type pqueue []item

func (q pqueue) Len() int            { return len(q) }
func (q pqueue) Less(i, j int) bool  { return q[i].f < q[j].f }
func (q pqueue) Swap(i, j int)       { q[i], q[j] = q[j], q[i] }
func (q *pqueue) Push(v interface{}) { *q = append(*q, v.(item)) }
func (q *pqueue) Pop() interface{} {
	old := *q
	v := old[len(old)-1]
	*q = old[:len(old)-1]
	return v
}

func jpsSearch(g *grid, start, goal [2]int) ([][2]int, int) {
	gScore := map[[2]int]float64{start: 0}
	parent := map[[2]int]*[2]int{start: nil}
	pq := &pqueue{}
	heap.Init(pq)
	heap.Push(pq, item{start, octile(start, goal), 0})
	seq := 1
	expanded := 0
	for pq.Len() > 0 {
		cur := heap.Pop(pq).(item)
		x := cur.node
		if x == goal {
			path := [][2]int{}
			for p := &x; p != nil; p = parent[*p] {
				path = append([][2]int{*p}, path...)
			}
			return path, expanded
		}
		expanded++
		for _, y := range identifySuccessors(g, x, parent[x], goal) {
			ng := gScore[x] + octile(x, y)
			if cur, ok := gScore[y]; !ok || ng < cur {
				gScore[y] = ng
				px := x // 父节点是当前弹出的跳点，不是 y 自己
				parent[y] = &px
				heap.Push(pq, item{y, ng + octile(y, goal), seq})
				seq++
			}
		}
	}
	return nil, expanded
}
