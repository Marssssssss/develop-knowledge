// aoi_grid.go — AOI 九宫格 (grid-based Area Of Interest) 最小实现与自检
//
// 运行: go run aoi_grid.go
//
// 与 python/aoi_grid.py、c/aoi_grid.c 逻辑一一对应:
//   - 格子归属决定视野(3x3);
//   - 跨格时按 old_nb/new_nb 差集分发 Leave/Enter/Move;
//   - 同格内微动只发 Move;
//   - 广播量对比 + 视野边缘抖动的量化。
//
// 世界坐标直接用「格」为单位(1.0 = 一格), 因此 cell = (int)x, (int)y。
package main

import (
	"fmt"
	"math/rand"
	"os"
)

const viewR = 1 // 视野半径(格); 1 -> 3x3 九宫格

var failures int

func check(cond bool, what string) {
	if !cond {
		fmt.Printf("  [FAIL] %s\n", what)
		failures++
	}
}

func has(s []int, v int) bool {
	for _, x := range s {
		if x == v {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- grid

type grid struct {
	w, h   int
	cells  [][]int // 每个格子里登记的实体 id
	px, py []float64
	pcell  []int

	nEnter, nLeave, nMove int
}

func newGrid(w, h, n int) *grid {
	g := &grid{w: w, h: h, cells: make([][]int, w*h),
		px: make([]float64, n), py: make([]float64, n), pcell: make([]int, n)}
	for i := range g.pcell {
		g.pcell[i] = -1
	}
	return g
}

func (g *grid) idxOf(cx, cy int) int { return cy*g.w + cx }

func (g *grid) cellOf(eid int) (int, int) {
	return int(g.px[eid]), int(g.py[eid])
}

// nb 返回半径 r 的方形邻域格子索引, 出界裁剪(地图边界即视野边界)。
func (g *grid) nb(cx, cy, r int) []int {
	out := make([]int, 0, (2*r+1)*(2*r+1))
	for yy := cy - r; yy <= cy+r; yy++ {
		if yy < 0 || yy >= g.h {
			continue
		}
		for xx := cx - r; xx <= cx+r; xx++ {
			if xx < 0 || xx >= g.w {
				continue
			}
			out = append(out, g.idxOf(xx, yy))
		}
	}
	return out
}

func (g *grid) remove(idx, eid int) {
	for i, v := range g.cells[idx] {
		if v == eid {
			g.cells[idx] = append(g.cells[idx][:i], g.cells[idx][i+1:]...)
			return
		}
	}
}

// broadcast 把消息发给 idxs 这些格子里的实体(不含 self), 返回条数。
func (g *grid) broadcast(idxs []int, self int) int {
	n := 0
	for _, i := range idxs {
		for _, v := range g.cells[i] {
			if v != self {
				n++
			}
		}
	}
	return n
}

// enter 实体进场: 登记进格子, 并通知九宫格内的既有实体。
func (g *grid) enter(eid int, x, y float64) {
	g.px[eid], g.py[eid] = x, y
	cx, cy := int(x), int(y)
	idx := g.idxOf(cx, cy)
	g.pcell[eid] = idx
	g.cells[idx] = append(g.cells[idx], eid)
	g.nEnter += g.broadcast(g.nb(cx, cy, viewR), eid)
}

// move 返回本次移动的三类格子集合基数; nc == -1 表示未跨格。
func (g *grid) move(eid int, x, y float64) (nl, ne, nc int) {
	ox, oy := g.cellOf(eid)
	oldIdx := g.pcell[eid]
	g.px[eid], g.py[eid] = x, y
	nx, ny := int(x), int(y)

	if nx == ox && ny == oy {
		g.nMove += g.broadcast(g.nb(ox, oy, viewR), eid) // 未跨格: 只同步位置
		return 0, 0, -1
	}

	oldNb, newNb := g.nb(ox, oy, viewR), g.nb(nx, ny, viewR)

	g.remove(oldIdx, eid) // 先摘旧格
	nIdx := g.idxOf(nx, ny)
	g.pcell[eid] = nIdx
	g.cells[nIdx] = append(g.cells[nIdx], eid) // 再插新格

	for _, c := range oldNb {
		if !has(newNb, c) { // old - new -> Leave
			g.nLeave += g.broadcast([]int{c}, eid)
			nl++
		}
	}
	for _, c := range newNb {
		if !has(oldNb, c) { // new - old -> Enter
			g.nEnter += g.broadcast([]int{c}, eid)
			ne++
		}
	}
	for _, c := range newNb {
		if has(oldNb, c) { // new & old -> Move
			g.nMove += g.broadcast([]int{c}, eid)
			nc++
		}
	}
	return nl, ne, nc
}

// ---------------------------------------------------------------- 场景

func scenarioDiffRule() {
	g := newGrid(20, 20, 4096)
	eid := 5*20 + 5
	g.enter(eid, 5.5, 5.5)
	g.nEnter, g.nLeave, g.nMove = 0, 0, 0

	nl, ne, nc := g.move(eid, 6.5, 5.5)
	fmt.Printf("[自检 1] 水平跨 1 格: Leave=%d Enter=%d Common=%d (期望 3/3/6)\n", nl, ne, nc)
	check(nl == 3 && ne == 3 && nc == 6, "水平跨格的差集基数")
	check(g.nEnter+g.nLeave+g.nMove == 0, "场上无他人时不应发出消息")

	nl, ne, nc = g.move(eid, 7.5, 6.5)
	fmt.Printf("[自检 1] 对角跨 1 格: Leave=%d Enter=%d Common=%d (期望 5/5/4)\n", nl, ne, nc)
	check(nl == 5 && ne == 5 && nc == 4, "对角跨格的差集基数")
}

func scenarioRouting() {
	g := newGrid(20, 20, 4096)
	eid := 5*20 + 5
	g.enter(eid, 5.5, 5.5)  // (5,5) -> 移到 (6,5)
	g.enter(1000, 4.5, 5.5) // (4,5) 属于 leave 组
	g.enter(1001, 7.5, 5.5) // (7,5) 属于 enter 组
	g.enter(1002, 6.5, 5.5) // (6,5) 属于 common 组
	g.nEnter, g.nLeave, g.nMove = 0, 0, 0

	nl, ne, nc := g.move(eid, 6.5, 5.5)
	fmt.Printf("[自检 2] 三类格子路由: Leave=%d Enter=%d Move=%d (期望 1/1/1)\n", nl, ne, nc)
	check(nl == 1 && ne == 1 && nc == 1, "三类格子各自路由一个观察者")
	check(len(g.cells[g.idxOf(6, 5)]) == 2, "eid 已进入 (6,5), 该格含 eid + 观察者")
	check(len(g.cells[g.idxOf(4, 5)]) == 1 && len(g.cells[g.idxOf(7, 5)]) == 1,
		"leave/enter 两组格子里的观察者未被打扰")
}

func scenarioDenseCell() {
	g := newGrid(6, 6, 16)
	for i := 0; i < 5; i++ {
		g.enter(i, 2.1+float64(i)*0.1, 2.1) // 5 个实体全在格子 (2,2)
	}
	g.nEnter, g.nLeave, g.nMove = 0, 0, 0
	g.move(0, 2.15, 2.1) // 同格内微动
	fmt.Printf("[自检 3] 同格 5 实体微动: Move=%d (期望 4 = 格内其他人)\n", g.nMove)
	check(g.nMove == 4 && g.nEnter == 0 && g.nLeave == 0, "同格广播给格内所有人")
}

func scenarioBroadcast() {
	const n, steps = 300, 400
	g := newGrid(40, 30, n)
	rnd := rand.New(rand.NewSource(7))
	for i := 0; i < n; i++ {
		g.enter(i, rnd.Float64()*39.9, rnd.Float64()*29.9)
	}
	g.nEnter, g.nLeave, g.nMove = 0, 0, 0

	naive := 0
	for s := 0; s < steps; s++ {
		for i := 0; i < n; i++ {
			x := clamp(g.px[i]+rnd.Float64()*1.2-0.6, 0, 39.9)
			y := clamp(g.py[i]+rnd.Float64()*1.2-0.6, 0, 29.9)
			g.move(i, x, y)
			naive += n - 1
		}
	}
	total := g.nEnter + g.nLeave + g.nMove
	fmt.Printf("[广播量] %d 实体 x %d 步 @ 40x30 格:\n", n, steps)
	fmt.Printf("         朴素全广播 %d 条 vs AOI %d 条 (E=%d L=%d M=%d), 降幅 %.2f%%\n",
		naive, total, g.nEnter, g.nLeave, g.nMove,
		100*(1-float64(total)/float64(naive)))
	fmt.Printf("         平均每实体每步 %.3f 条 (朴素 = %.3f)\n",
		float64(total)/steps/n, float64(naive)/steps/n)
	check(total < naive/5, "AOI 广播量应远低于朴素全广播")
	check(g.nMove > g.nEnter && g.nEnter > 0, "Move 占比最高且 Enter 非零")
}

func clamp(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

func main() {
	fmt.Println("== AOI 九宫格 (grid-based Area Of Interest) ==")
	fmt.Println()
	scenarioDiffRule()
	scenarioRouting()
	scenarioDenseCell()
	scenarioBroadcast()

	if failures > 0 {
		fmt.Printf("\n存在失败项 (failures=%d)\n", failures)
		os.Exit(1)
	}
	fmt.Println("\n全部自检通过。")
}
