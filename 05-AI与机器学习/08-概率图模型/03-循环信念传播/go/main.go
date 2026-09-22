package main

import "fmt"

func main() {
	// 动态范围与压缩：d(ψ)²=81（η=0.1 的对称势），d(E)=2 ⇒ 163/83 ≈ 1.9639
	psi := SymmetricPotential(0.1)
	d := PotentialStrength(psi)
	fmt.Println("d(psi)  :", d, "d(psi)^2:", d*d)
	fmt.Println("contraction:", ContractionStep(d*d, 2.0), "< 2")

	// 单环：Theorem 11 的 g'(0) 恒 < 1
	for _, eta := range []float64{0.6, 0.75, 0.9, 0.99} {
		c := MakeCycle(6, eta)
		fmt.Printf("cycle6 eta=%.2f  thm11=%.4f  simon=%.4f\n",
			eta, Theorem11Derivative(c), SimonCondition(c))
	}

	// 多环 + 强耦合 ⇒ 周期 2 振荡
	nodes := []string{"A", "B", "C", "D"}
	edges := [][2]string{{"A", "B"}, {"B", "C"}, {"C", "A"}, {"B", "D"}, {"D", "C"}}
	ep := map[string][2][2]float64{
		edgeKey("A", "B"): {{0.010978397260510713, 1.0}, {1.0, 0.019413738807808292}},
		edgeKey("B", "C"): {{0.004712618849374387, 1.0}, {1.0, 0.003938346153881924}},
		edgeKey("A", "C"): {{0.0038705150091150552, 1.0}, {1.0, 0.002065030001216686}},
		edgeKey("B", "D"): {{0.012678507316066592, 1.0}, {1.0, 0.013142560290812521}},
		edgeKey("C", "D"): {{0.02892728734048402, 1.0}, {1.0, 0.013599781195054766}},
	}
	np := map[string][2]float64{
		"A": {0.5571823433712233, 1.203893155848943},
		"B": {1.716334307931209, 1.123364717787998},
		"C": {0.8346887594412482, 1.7620081689486722},
		"D": {0.2745322889884101, 1.5735495344778874},
	}
	osc := NewMRF(nodes, edges, np, ep)
	fmt.Println("osc thm11 :", Theorem11Derivative(osc))
	fmt.Println("osc simon :", SimonCondition(osc))
	msgs, hist, st := RunBP(osc, 300, 1e-12)
	fmt.Println("osc status:", st, "iters:", len(hist))
	truth := BruteForceMarginals(osc)
	fmt.Println("osc belief A:", Belief(osc, msgs, "A"), "truth:", truth["A"])
}
