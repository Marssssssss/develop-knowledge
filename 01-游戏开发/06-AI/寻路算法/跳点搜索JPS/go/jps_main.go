package main

import "fmt"

func main() {
	// 图 2(b)：向右侧直行，正上方有障碍 ⇒ 右上(3) 成为 forced
	g := newGrid(5, 5, [][2]int{{2, 1}})
	x := [2]int{2, 2}
	p := [2]int{1, 2}
	fmt.Printf("straight + obstacle above : forced=%v (论文 Figure 2(b) 的 n=3)\n",
		forcedNeighbours(g, x, p, [2]int{1, 0}))

	// 图 2(d)：向右上对角，正左方有障碍 ⇒ 左上(1) 成为 forced
	g2 := newGrid(5, 5, [][2]int{{1, 2}})
	fmt.Printf("diagonal + obstacle left  : forced=%v (论文 Figure 2(d) 的 n=1)\n",
		forcedNeighbours(g2, x, [2]int{1, 3}, [2]int{1, -1}))

	// 对角移动的 natural neighbours：d 本身 + 两个正交分量
	natural := []int{}
	for k := range naturalNeighbours([2]int{1, -1}) {
		natural = append(natural, k)
	}
	fmt.Printf("diagonal natural          : %v (论文 Figure 2(c) 的 2/3/5)\n", natural)

	// 30x30 空旷地图：JPS 一次跳到终点
	big := newGrid(30, 30, nil)
	path, expanded := jpsSearch(big, [2]int{0, 0}, [2]int{29, 29})
	cost := 0.0
	for i := 1; i < len(path); i++ {
		cost += octile(path[i-1], path[i])
	}
	fmt.Printf("30x30 open field          : jumps=%d expanded=%d cost=%.4f\n", len(path)-1, expanded, cost)
}
