package main

import "fmt"

func main() {
	sig := newSigmoidDefault(-0.5)
	fmt.Printf("sigmoid k=-0.5 : f(0)=%.4f f(0.25)=%.4f f(0.5)=%.4f f(0.75)=%.4f\n",
		sig.evaluate(0), sig.evaluate(0.25), sig.evaluate(0.5), sig.evaluate(0.75))

	lin := newLinearRanged(0, 10)
	fmt.Printf("linear 0..10   : f(5)=%.4f   inversed f(0)=%.4f\n",
		lin.evaluate(5), newLinearInversed().evaluate(0))

	scores := []float64{0.6, 0.8}
	weights := []float64{1, 1}
	fmt.Printf("measures       : sum=%.4f product=%.4f chebyshev=%.4f weighted=%.4f\n",
		weightedSum{}.calculate(scores, weights),
		weightedProduct{}.calculate(scores, weights),
		chebyshevDistance{}.calculate(scores, weights),
		weightedMeasure{}.calculate(scores, weights))

	fmt.Printf("composites     : allOrNothing(0.79,0.9)=%.4f sum(0.3,0.3)=%.4f product(0.5,0.5)=%.4f comp=%.5f\n",
		allOrNothing([]float64{0.79, 0.9}, 0.8),
		sumOfScorers([]float64{0.3, 0.3}, 0.8),
		productOfScorers([]float64{0.5, 0.5}, 0, false),
		productOfScorers([]float64{0.5, 0.5}, 0, true))

	choices := []choice{{"drink", sumOfScorers([]float64{0.3, 0.3}, 0.8)}, {"flee", 0.81}}
	picked := pickHighest(choices)
	label := "<none>"
	if picked != nil {
		label = picked.label
	}
	fmt.Printf("picker Highest : %s (drink 被阈值打成 0 后 0 分永不获胜)\n", label)

	s := &score{}
	_ = s.set(0.5)
	fmt.Printf("score          : set(0.5)=%.2f set(1.5) err=%v\n", s.get(), s.set(1.5))
}
