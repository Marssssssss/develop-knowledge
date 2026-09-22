package main

import "fmt"

func main() {
	// 官方 doctest：barbell_graph(4, 6) 的 treewidth == 3
	bb := BarbellGraph(4, 6)
	fmt.Println("barbell chordal :", IsChordal(bb))
	fmt.Println("barbell treewidth:", Treewidth(bb))

	c4 := NewGraph([]string{"1", "2", "3", "4"},
		[][2]string{{"1", "2"}, {"2", "3"}, {"3", "4"}, {"4", "1"}})
	fmt.Println("4-cycle chordal :", IsChordal(c4))
	h, alpha := CompleteToChordalGraph(c4)
	fmt.Println("triangulated    :", IsChordal(h), "added edges:", len(h.Edges())-len(c4.Edges()))
	fmt.Println("alpha           :", alpha)

	// 三角链 {A,B,C} — {C,D,E}
	tri := NewGraph([]string{"A", "B", "C", "D", "E"},
		[][2]string{{"A", "B"}, {"A", "C"}, {"B", "C"}, {"C", "D"}, {"C", "E"}, {"D", "E"}})
	cliques := ChordalGraphCliques(tri)
	fmt.Println("cliques         :", cliques)
	edges := BuildJunctionTree(cliques)
	fmt.Println("jt edges        :", edges)
	fmt.Println("RIP             :", CheckRunningIntersection(cliques, edges))

	// LS 校准（势函数取固定数值，便于与 Python 侧对照结构而非数值）
	p1 := NewFactor([]string{"A", "B", "C"}, map[string]float64{
		"0|0|0": 0.2, "0|0|1": 0.3, "0|1|0": 0.4, "0|1|1": 0.5,
		"1|0|0": 0.6, "1|0|1": 0.7, "1|1|0": 0.8, "1|1|1": 0.9,
	})
	p2 := NewFactor([]string{"C", "D", "E"}, map[string]float64{
		"0|0|0": 0.9, "0|0|1": 0.8, "0|1|0": 0.7, "0|1|1": 0.6,
		"1|0|0": 0.5, "1|0|1": 0.4, "1|1|0": 0.3, "1|1|1": 0.2,
	})
	jt := &JunctionTree{Cliques: cliques, Potentials: []Factor{p1, p2}, Edges: edges}
	jt.Calibrate(true)
	fmt.Println("marginal C      :", jt.Marginal("C").Table)
	jt.Calibrate(false)
	fmt.Println("marginal C (bad):", jt.Marginal("C").Table)
}
