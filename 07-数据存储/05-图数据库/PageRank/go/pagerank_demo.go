// PageRank 幂迭代 —— Go 版。
//
// 权威来源：
//   - Wikipedia "PageRank"
//     https://en.wikipedia.org/wiki/PageRank
//   - Greif/Callut SIAM Review 2010 "An Inner-Outer Iteration for Computing PageRank"
//     https://www.cs.ubc.ca/~greif/Publications/gggl2010.pdf
//
// 公式：PR(u) = (1-d)/N + d * Σ_{v→u} PR(v) / L(v)，d=0.85。
// 收敛：max|PR_{k+1} - PR_k| < 1e-6。
package main

import (
	"fmt"
	"math"
)

const (
	DAMPING = 0.85
	TOL     = 1e-6
	MAXITER = 100
)

func pageRank(edges [][]string) map[string]float64 {
	nodes := map[string]bool{}
	outDeg := map[string]int{}
	for _, e := range edges {
		u, v := e[0], e[1]
		nodes[u] = true
		nodes[v] = true
		outDeg[u]++
	}
	for n := range nodes {
		// 确保 outDeg 字典键存在
		if _, ok := outDeg[n]; !ok {
			outDeg[n] = 0
		}
	}
	N := len(nodes)
	pr := map[string]float64{}
	for n := range nodes {
		pr[n] = 1.0 / float64(N)
	}
	teleport := (1.0 - DAMPING) / float64(N)
	var diff float64
	for it := 0; it < MAXITER; it++ {
		newPR := map[string]float64{}
		for n := range nodes {
			newPR[n] = teleport
		}
		for _, e := range edges {
			u, v := e[0], e[1]
			if outDeg[u] > 0 {
				newPR[v] += DAMPING * pr[u] / float64(outDeg[u])
			}
		}
		// 悬挂节点贡献
		var dangling float64
		for n := range nodes {
			if outDeg[n] == 0 {
				dangling += pr[n]
			}
		}
		if dangling > 0 {
			share := DAMPING * dangling / float64(N)
			for n := range nodes {
				newPR[n] += share
			}
		}
		diff = 0.0
		for n := range nodes {
			if d := math.Abs(newPR[n] - pr[n]); d > diff {
				diff = d
			}
		}
		pr = newPR
		if diff < TOL {
			fmt.Printf("在第 %d 轮收敛, max|Δ|=%.2e\n", it+1, diff)
			return pr
		}
	}
	fmt.Printf("达到 MAXITER=%d, max|Δ|=%.2e（未严格收敛）\n", MAXITER, diff)
	return pr
}

func main() {
	// 经典 4 节点示例（Wikipedia "PageRank"）
	edges := [][]string{
		{"B", "A"}, {"B", "C"},
		{"C", "A"},
		{"D", "A"}, {"D", "B"}, {"D", "C"},
	}
	pr := pageRank(edges)
	// 排序
	type kv struct {
		k string
		v float64
	}
	out := []kv{}
	for k, v := range pr {
		out = append(out, kv{k, v})
	}
	// 简单冒泡排序（节点数 ≤ 10）
	for i := 0; i < len(out); i++ {
		for j := i + 1; j < len(out); j++ {
			if out[j].v > out[i].v {
				out[i], out[j] = out[j], out[i]
			}
		}
	}
	for _, kv := range out {
		fmt.Printf("  %s: PR = %.6f\n", kv.k, kv.v)
	}
	var sum float64
	for _, v := range pr {
		sum += v
	}
	fmt.Printf("sum(PR) = %.6f（理论上应等于 1）\n", sum)
}