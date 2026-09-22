package main

import "fmt"

func main() {
	nodes := []string{"c", "d", "g", "i", "s", "j", "l", "h"}
	directed := [][2]string{
		{"c", "d"}, {"d", "g"}, {"i", "g"}, {"i", "s"}, {"s", "j"},
		{"g", "l"}, {"l", "j"}, {"j", "h"}, {"g", "h"},
	}
	card := map[string]int{"c": 2, "d": 2, "g": 3, "i": 2, "s": 2, "j": 2, "l": 2, "h": 2}
	moral := Moralize(nodes, directed)

	// 官方 doctest：WeightedMinFill(Asia).get_elimination_order(["c","d","g","l","s"]) == [c d l s g]
	sub := []string{"c", "d", "g", "l", "s"}
	fmt.Println("weightedminfill :", EliminationOrder(moral, card, sub, CostWeightedMinFill))
	fmt.Println("minfill         :", EliminationOrder(moral, card, sub, CostMinFill))
	fmt.Println("minneighbors    :", EliminationOrder(moral, card, sub, CostMinNeighbors))
	fmt.Println("minweight       :", EliminationOrder(moral, card, sub, CostMinWeight))

	// 官方 doctest：induced_width(A→B,C→B,C→D,B→E; [C,D,A,B,E]) == 3
	scopes := [][]string{{"A"}, {"B", "A", "C"}, {"C"}, {"D", "C"}, {"E", "B"}}
	fmt.Println("induced_width   :", InducedWidth(scopes, []string{"C", "D", "A", "B", "E"}))
	chain := [][]string{{"A"}, {"A", "B"}, {"B", "C"}, {"C", "D"}}
	fmt.Println("chain A..D      :", InducedWidth(chain, []string{"A", "B", "C", "D"}))
	fmt.Println("chain from mid  :", InducedWidth(chain, []string{"B", "C", "D", "A"}))

	fmt.Println("moral chordal   :", IsChordal(moral))
	// 沿消元顺序补齐 fill-in 边（不删点）后成为弦图
	tri := moral.Clone()
	for _, n := range EliminationOrder(moral, card, nil, CostMinFill) {
		for _, e := range FillInEdges(tri, n) {
			tri.AddEdge(e[0], e[1])
		}
	}
	fmt.Println("triangulated    :", IsChordal(tri))

	// 数边 vs 加权：只消 {x, y}，两者给出相反的顺序
	gn := []string{"x", "y", "p", "q", "m", "n", "o"}
	ge := [][2]string{{"x", "p"}, {"x", "q"}, {"x", "y"}, {"y", "m"}, {"y", "n"}, {"y", "o"}}
	gc := map[string]int{"x": 10, "y": 10, "p": 10, "q": 10, "m": 2, "n": 2, "o": 2}
	gs := NewGraph(gn, ge)
	fmt.Println("minfill   x,y   :", EliminationOrder(gs, gc, []string{"x", "y"}, CostMinFill))
	fmt.Println("wminfill  x,y   :", EliminationOrder(gs, gc, []string{"x", "y"}, CostWeightedMinFill))
	fmt.Println("fill x->y       :", FillCount(gs, []string{"x", "y"}))
	fmt.Println("fill y->x       :", FillCount(gs, []string{"y", "x"}))
}
