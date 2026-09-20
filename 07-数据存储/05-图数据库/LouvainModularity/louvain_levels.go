// Package main 第二阶段与多 level 迭代（Louvain 主体）。
//
// 与 louvain.go 同包：图结构与模块度/ΔQ 在 louvain.go，两阶段迭代与聚合在这里。
package main

import "sort"

// OneLevel 是第一阶段：反复局部移动直到局部极大。
func OneLevel(g *Graph, part map[int]int, maxIterations int,
	tolerance float64) (map[int]int, float64, int) {
	out := map[int]int{}
	for k, v := range part {
		out[k] = v
	}
	prevQ := Modularity(g, out)
	iters := 0
	for it := 0; it < maxIterations; it++ {
		iters++
		moved := false
		for _, i := range g.Nodes {
			own := out[i]
			rest := map[int][]int{}
			for c := range groupBy(out) {
				rest[c] = []int{}
			}
			for n, c := range out {
				if n != i {
					rest[c] = append(rest[c], n)
				}
			}
			cands := map[int]bool{own: true}
			for _, j := range g.Neighbors(i) {
				cands[out[j]] = true
			}
			baseline := DeltaQAdd(g, out, i, rest[own])
			bestC, bestGain := own, baseline
			keys := []int{}
			for c := range cands {
				keys = append(keys, c)
			}
			sort.Ints(keys)
			for _, c := range keys {
				gain := DeltaQAdd(g, out, i, rest[c])
				if gain > bestGain+1e-9 {
					bestGain, bestC = gain, c
				}
			}
			if bestC != own {
				out[i] = bestC
				moved = true
			}
		}
		newQ := Modularity(g, out)
		if !moved || abs(newQ-prevQ) < tolerance {
			prevQ = newQ
			break
		}
		prevQ = newQ
	}
	return out, prevQ, iters
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

// Aggregate 是第二阶段：把社区聚合成超节点，内部边变成自环。
func Aggregate(g *Graph, part map[int]int) *Graph {
	groups := groupBy(part)
	ids := []int{}
	for c := range groups {
		ids = append(ids, c)
	}
	sort.Ints(ids)
	sg := NewGraph()
	for _, c := range ids {
		sg.addNode(c)
	}
	for ai := 0; ai < len(ids); ai++ {
		for bi := ai; bi < len(ids); bi++ {
			a, b := ids[ai], ids[bi]
			w := 0.0
			if a == b {
				for xi := 0; xi < len(groups[a]); xi++ {
					for yi := xi + 1; yi < len(groups[a]); yi++ {
						w += g.A(groups[a][xi], groups[a][yi])
					}
					w += g.selfLoop[groups[a][xi]]
				}
			} else {
				for _, x := range groups[a] {
					for _, y := range groups[b] {
						w += g.A(x, y)
					}
				}
			}
			if w > 0 {
				sg.AddEdge(a, b, w)
			}
		}
	}
	return sg
}

// Louvain 跑完整的多 level 迭代，返回划分、最终 Q、每 level 的 Q、level 数。
func Louvain(g *Graph, maxLevels, maxIterations int,
	tolerance float64) (map[int]int, float64, []float64, int) {
	curG := g
	mapping := map[int]int{}
	for _, n := range g.Nodes {
		mapping[n] = n
	}
	curPart := map[int]int{}
	for _, n := range curG.Nodes {
		curPart[n] = n
	}
	final := map[int]int{}
	for n := range mapping {
		final[n] = n
	}
	qPerLevel := []float64{}
	levels := 0
	for lv := 0; lv < maxLevels; lv++ {
		newPart, q, _ := OneLevel(curG, curPart, maxIterations, tolerance)
		qPerLevel = append(qPerLevel, q)
		levels++
		for n := range final {
			final[n] = newPart[mapping[n]]
		}
		if len(groupBy(newPart)) == len(curG.Nodes) {
			break
		}
		curG = Aggregate(curG, newPart)
		curPart = map[int]int{}
		for _, c := range curG.Nodes {
			curPart[c] = c
		}
		for n := range mapping {
			mapping[n] = newPart[mapping[n]]
		}
	}
	return final, Modularity(g, final), qPerLevel, levels
}

// RingOfCliques 构造论文里的 ring of cliques。
func RingOfCliques(nCliques, cliqueSize int) *Graph {
	g := NewGraph()
	for c := 0; c < nCliques; c++ {
		base := c * cliqueSize
		for i := 0; i < cliqueSize; i++ {
			for j := i + 1; j < cliqueSize; j++ {
				g.AddEdge(base+i, base+j, 1.0)
			}
		}
	}
	for c := 0; c < nCliques; c++ {
		g.AddEdge(c*cliqueSize, ((c+1)%nCliques)*cliqueSize, 1.0)
	}
	return g
}
