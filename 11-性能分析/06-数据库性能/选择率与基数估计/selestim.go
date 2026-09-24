// selestim.go — 与 selestim.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"fmt"
	"math"
)

func scaleCardinality(relpages, reltuples, currentPages float64) float64 {
	if currentPages == relpages {
		return reltuples
	}
	return reltuples * currentPages / relpages
}

// selectivityRangeUnique:桶内线性插值 + 前面整桶。
func selectivityRangeUnique(histogram []float64, value float64) float64 {
	n := float64(len(histogram) - 1)
	for i := 0; i < len(histogram)-1; i++ {
		lo, hi := histogram[i], histogram[i+1]
		if value >= lo && value <= hi {
			frac := 0.0
			if hi > lo {
				frac = (value - lo) / (hi - lo)
			}
			return (float64(i) + frac) / n
		}
	}
	if value > histogram[len(histogram)-1] {
		return 1
	}
	return 0
}

func selectivityEqNotInMCV(mcvFreqs []float64, nDistinct float64) float64 {
	sum := 0.0
	for _, f := range mcvFreqs {
		sum += f
	}
	return (1 - sum) / (nDistinct - float64(len(mcvFreqs)))
}

func combineIndependent(sels ...float64) float64 {
	r := 1.0
	for _, s := range sels {
		r *= s
	}
	return r
}

func eqjoinselUnique(nf1, nf2, n1, n2 float64) float64 {
	return (1 - nf1) * (1 - nf2) / math.Max(n1, n2)
}

func main() {
	hist := []float64{0, 993, 1997, 3050, 4040, 5036, 5957, 7057, 8029, 9016, 9995}
	sel := selectivityRangeUnique(hist, 1000)
	fmt.Printf("range unique1<1000: %.6f rows=%.0f (doc 0.100697/1007)\n",
		sel, math.Round(10000*sel))

	mcv := []float64{0.00333333}
	for i := 0; i < 9; i++ {
		mcv = append(mcv, 0.003)
	}
	selEq := selectivityEqNotInMCV(mcv, 676)
	fmt.Printf("eq not-in-mcv: %.7f rows=%.0f (doc 0.0014559/15)\n",
		selEq, math.Round(10000*selEq))

	fmt.Printf("independent: %.7f (doc 0.0001466)\n",
		combineIndependent(0.100697, 0.0014559))

	fmt.Printf("eqjoinsel: %.4f join rows=%.0f (doc 0.0001/50)\n",
		eqjoinselUnique(0, 0, 10000, 10000),
		math.Round(50*10000*eqjoinselUnique(0, 0, 10000, 10000)))
}
