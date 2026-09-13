// 图遍历 BFS / DFS / Dijkstra —— Go 版（最小堆 + 邻接表）。
//
// 权威来源：
//   - Wikipedia "Dijkstra's algorithm"
//     https://en.wikipedia.org/wiki/Dijkstra%27s_algorithm
//   - Czech Technical University Programming for Engineers L8 Recursion
//     https://cw.fel.cvut.cz/b252/_media/courses/be5b33pge/lectures/l8_recursion.pdf
//
// 与 Python 版使用同一 7 节点加权示例（节点用 int）。
package main

import (
	"container/heap"
	"fmt"
)

// ───── BFS（无权、按层） ─────
func bfs(graph map[int][]int, start int) []int {
	visited := map[int]bool{start: true}
	order := []int{}
	q := []int{start}
	for len(q) > 0 {
		u := q[0]
		q = q[1:]
		order = append(order, u)
		for _, v := range graph[u] {
			if !visited[v] {
				visited[v] = true
				q = append(q, v)
			}
		}
	}
	return order
}

// BFS 求无权图最短路径
func bfsShortestPath(graph map[int][]int, start, goal int) []int {
	if start == goal {
		return []int{start}
	}
	parent := map[int]int{start: -1}
	q := []int{start}
	for len(q) > 0 {
		u := q[0]
		q = q[1:]
		for _, v := range graph[u] {
			if _, ok := parent[v]; !ok {
				parent[v] = u
				if v == goal {
					// 回溯
					path := []int{}
					cur := v
					for cur != -1 {
						path = append([]int{cur}, path...)
						if cur == start { break }
						cur = parent[cur]
					}
					return path
				}
				q = append(q, v)
			}
		}
	}
	return nil
}

// ───── DFS（递归） ─────
func dfs(graph map[int][]int, start int) []int {
	visited := map[int]bool{}
	order := []int{}
	var walk func(u int)
	walk = func(u int) {
		visited[u] = true
		order = append(order, u)
		for _, v := range graph[u] {
			if !visited[v] {
				walk(v)
			}
		}
	}
	walk(start)
	return order
}

// ───── Dijkstra：最小堆 ─────
type item struct {
	node int
	dist int // 用 int 表示权重，方便演示
}
type minHeap []item

func (h minHeap) Len() int            { return len(h) }
func (h minHeap) Less(i, j int) bool  { return h[i].dist < h[j].dist }
func (h minHeap) Swap(i, j int)       { h[i], h[j] = h[j], h[i] }
func (h *minHeap) Push(x interface{}) { *h = append(*h, x.(item)) }
func (h *minHeap) Pop() interface{} {
	old := *h; n := len(old); x := old[n-1]; *h = old[:n-1]; return x
}

// Dijkstra 接受权重为 int 的加权图
func dijkstra(graph map[int][]Edge, start int) map[int]int {
	dist := map[int]int{start: 0}
	h := &minHeap{}
	heap.Init(h)
	heap.Push(h, item{start, 0})
	for h.Len() > 0 {
		it := heap.Pop(h).(item)
		u := it.node
		if it.dist > dist[u] { continue }
		for _, e := range graph[u] {
			nd := dist[u] + e.Weight
			if cur, ok := dist[e.To]; !ok || nd < cur {
				dist[e.To] = nd
				heap.Push(h, item{e.To, nd})
			}
		}
	}
	return dist
}

type Edge struct{ To, Weight int }

func main() {
	// 无权示例
	UG := map[int][]int{
		1: {2, 3}, 2: {5}, 3: {2, 4}, 4: {2, 5}, 5: {},
	}
	fmt.Println("BFS from 1 :", bfs(UG, 1))
	fmt.Println("DFS from 1 :", dfs(UG, 1))
	fmt.Println("BFS 最短 1->5 :", bfsShortestPath(UG, 1, 5))

	// 加权示例
	WG := map[int][]Edge{
		1: {{2, 8}, {3, 1}},
		2: {{5, 5}},
		3: {{2, 4}, {4, 2}},
		4: {{2, 1}, {5, 3}},
		5: {},
	}
	fmt.Println("Dijkstra(1) :", dijkstra(WG, 1))
	fmt.Println("复杂度：BFS/DFS 都是 O(V+E) 时间 O(V) 空间；Dijkstra O((V+E) log V)")
}