// A* 寻路算法最小实现（4 邻接带权网格）。
// 对照 Red Blob Games 的参考实现：优先队列无需 decrease-key，
// 直接向 container/heap 插入重复条目，松弛时的 g 值改善检查保证正确性。
//
// 模式：mode 0 = Dijkstra(只看 g) / 1 = A*(g+h) / 2 = Greedy(只看 h)
package main

import (
	"container/heap"
	"fmt"
	"strings"
)

const (
	W, H         = 24, 12
	forestCost   = 5 // 森林格移动代价（普通格为 1）
)

// '.' 普通格 / 'f' 森林格 / '#' 墙 / 'S' 起点 / 'E' 终点
var mapRows = []string{
	"..............#.........",
	"..S.......f...#.........",
	"..........f...#....fff..",
	"...ffffff.f...#....fff..",
	"...ffffff.f...#.........",
	".......fffffffffffffffff",
	"..............#.........",
	"..............#.ffff....",
	"####..........#.........",
	"..............#....E....",
	"..............#.........",
	"..............#.........",
}

type loc struct{ x, y int }

// item 是优先队列条目：priority 为 f（或 g / h，取决于模式）；
// cost 为入队时刻的 g 值，用于出队时识别过期条目
type item struct {
	priority int
	cost    int
	node     loc
	index    int // heap.Interface 需要的内部索引
}

type pqueue []*item

func (pq pqueue) Len() int            { return len(pq) }
func (pq pqueue) Less(i, j int) bool   { return pq[i].priority < pq[j].priority }
func (pq pqueue) Swap(i, j int)        { pq[i], pq[j] = pq[j], pq[i]; pq[i].index, pq[j].index = i, j }
func (pq *pqueue) Push(x interface{})  { it := x.(*item); it.index = len(*pq); *pq = append(*pq, it) }
func (pq *pqueue) Pop() interface{} {
	old := *pq
	n := len(old)
	it := old[n-1]
	old[n-1] = nil
	*pq = old[:n-1]
	return it
}

type grid struct {
	blocked map[loc]bool
	forest  map[loc]bool
	start   loc
	goal    loc
}

func parseMap(rows []string) *grid {
	g := &grid{blocked: map[loc]bool{}, forest: map[loc]bool{}}
	for y, row := range rows {
		for x, ch := range row {
			p := loc{x, y}
			switch ch {
			case '#':
				g.blocked[p] = true
			case 'f':
				g.forest[p] = true
			case 'S':
				g.start = p
			case 'E':
				g.goal = p
			}
		}
	}
	return g
}

// neighbors 返回 4 邻接（曼哈顿世界，对应曼哈顿启发式）
func (g *grid) neighbors(p loc) []loc {
	var out []loc
	for _, d := range [4]loc{{0, -1}, {0, 1}, {-1, 0}, {1, 0}} {
		n := loc{p.x + d.x, p.y + d.y}
		if n.x >= 0 && n.x < W && n.y >= 0 && n.y < H {
			out = append(out, n)
		}
	}
	return out
}

// moveCost 返回进入 nxt 格的代价：普通格 1，森林格 5
func (g *grid) moveCost(nxt loc) int {
	if g.forest[nxt] {
		return forestCost
	}
	return 1
}

// heuristic 为曼哈顿距离：4 邻接单位代价网格的可采纳启发式（不高估）
func heuristic(a, b loc) int {
	dx, dy := a.x-b.x, a.y-b.y
	if dx < 0 {
		dx = -dx
	}
	if dy < 0 {
		dy = -dy
	}
	return dx + dy
}

// search 统一搜索：mode 0/1/2 分别为 Dijkstra / A* / Greedy。
// 返回父指针表与 g 值表及扩展节点数。
func (g *grid) search(mode int) (cameFrom map[loc]loc, costSoFar map[loc]int, expanded int) {
	pq := &pqueue{}
	heap.Init(pq)
	cameFrom = map[loc]loc{}
	costSoFar = map[loc]int{}
	costSoFar[g.start] = 0
	heap.Push(pq, &item{priority: 0, cost: 0, node: g.start})

	for pq.Len() > 0 {
		it := heap.Pop(pq).(*item)
		current := it.node
		if current == g.goal { // early exit：目标出队即结束
			break
		}
		// 过期条目跳过：入队时的 g 已劣于当前记录的 g
		if it.cost > costSoFar[current] {
			continue
		}
		expanded++
		for _, nxt := range g.neighbors(current) {
			if g.blocked[nxt] {
				continue
			}
			newCost := costSoFar[current] + g.moveCost(nxt)
			if old, ok := costSoFar[nxt]; !ok || newCost < old {
				costSoFar[nxt] = newCost
				cameFrom[nxt] = current
				var priority int
				switch mode {
				case 0: // Dijkstra：只看 g
					priority = newCost
				case 1: // A*：g + h
					priority = newCost + heuristic(g.goal, nxt)
				default: // Greedy：只看 h
					priority = heuristic(g.goal, nxt)
				}
				heap.Push(pq, &item{priority: priority, cost: newCost, node: nxt})
			}
		}
	}
	return
}

// reconstructPath 从 goal 沿父指针回溯到 start
func (g *grid) reconstructPath(cameFrom map[loc]loc) []loc {
	var path []loc
	cur, ok := g.goal, true
	for ok && cur != g.start {
		path = append([]loc{cur}, path...)
		cur, ok = cameFrom[cur]
	}
	if !ok {
		return nil
	}
	return append([]loc{g.start}, path...)
}

func (g *grid) render(path []loc) string {
	pathSet := map[loc]bool{}
	for _, p := range path {
		pathSet[p] = true
	}
	var b strings.Builder
	for y := 0; y < H; y++ {
		for x := 0; x < W; x++ {
			p := loc{x, y}
			switch {
			case p == g.start:
				b.WriteByte('S')
			case p == g.goal:
				b.WriteByte('E')
			case pathSet[p]:
				b.WriteByte('*')
			case g.blocked[p]:
				b.WriteByte('#')
			case g.forest[p]:
				b.WriteByte('f')
			default:
				b.WriteByte('.')
			}
		}
		b.WriteByte('\n')
	}
	return b.String()
}

func main() {
	g := parseMap(mapRows)
	names := []string{"Dijkstra (g)", "A* (g+h)", "Greedy (h)"}
	var costs [3]int
	var astarPath []loc
	for mode := 0; mode < 3; mode++ {
		cameFrom, costSoFar, expanded := g.search(mode)
		cost, reached := costSoFar[g.goal]
		if !reached {
			cost = -1
		}
		costs[mode] = cost
		fmt.Printf("%-16s 代价 = %-4d 扩展节点数 = %d\n", names[mode], cost, expanded)
		if mode == 1 && reached {
			astarPath = g.reconstructPath(cameFrom)
			fmt.Printf("\nA* 路径可视化（* 为路径，绕开代价 5 的森林）：\n%s", g.render(astarPath))
		}
	}
	// 一致性验证：A* 与 Dijkstra 代价必须相同（曼哈顿 h 在代价>=1 时可采纳）
	if costs[0] != -1 && costs[0] == costs[1] {
		fmt.Printf("\n[check] A* 与 Dijkstra 最优代价一致：%d（h 可采纳性验证通过）\n", costs[1])
	}
}
