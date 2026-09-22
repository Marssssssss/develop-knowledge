package main

import "fmt"

func main() {
	h := IceCream()
	obs := []int{2, 0, 2} // 3 1 3

	_, prob := Forward(h, obs)
	fmt.Printf("forward         P(3 1 3) = %.6f\n", prob)

	_, cs, logP := ForwardScaled(h, obs)
	fmt.Printf("forward_scaled  c = %v  logP = %.6f  (= log of %.6f)\n", cs, logP, prob)

	total, bestPath, best := BruteForce(h, obs)
	fmt.Printf("brute_force     P = %.6f  best path = %v  p = %.6f\n", total, bestPath, best)

	score, path := Viterbi(h, obs)
	fmt.Printf("viterbi         logp = %.6f  path = %v\n", score, path)

	g, _ := GammaUnscaled(h, obs)
	fmt.Printf("posterior(map)  path = %v\n", PosteriorDecode(g))

	// SLP3 Eq A.7：P(3 1 3 | hot hot cold) = P(3|hot)P(1|hot)P(3|cold) = .4×.2×.1
	fmt.Printf("Eq A.7          P(3 1 3 | hot hot cold) = %.3f\n",
		h.B[0][2]*h.B[0][0]*h.B[1][2])

	logs, trained := BaumWelch(h, []int{0, 1, 2, 1, 0, 2, 2, 1}, 12)
	fmt.Printf("baum_welch      logL[0] = %.6f -> logL[-1] = %.6f\n", logs[0], logs[len(logs)-1])
	fmt.Printf("                A[0] = %v  B[0] = %v\n", trained.A[0], trained.B[0])
}
