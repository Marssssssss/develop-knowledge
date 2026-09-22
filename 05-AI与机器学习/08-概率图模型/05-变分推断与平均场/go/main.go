package main

import (
	"fmt"
	"math"
)

func main() {
	// ---------- 离散：三元耦合 Ising ----------
	g := NewIsing([]float64{0.1, -0.2, 0.3},
		[][]float64{{0, 1.2, -0.4}, {1.2, 0, 0.8}, {-0.4, 0.8, 0}})
	m, hist := CaviIsing(g, 60, nil, nil)
	lz := g.LogZ()
	fmt.Printf("ising   logZ = %.10f  ELBO = %.10f  KL = %.10f\n",
		lz, hist[len(hist)-1], KlIsing(g, m))
	fmt.Printf("        m* = %v\n", m)
	fmt.Printf("        真边缘 = [%.6f %.6f %.6f]\n",
		g.Marginal(0), g.Marginal(1), g.Marginal(2))

	// ---------- 负控：解耦时平均场完全精确 ----------
	free := NewIsing([]float64{0.4, -0.7, 0.2},
		[][]float64{{0, 0, 0}, {0, 0, 0}, {0, 0, 0}})
	mf, histf := CaviIsing(free, 3, nil, nil)
	fmt.Printf("free    KL = %.3e （w=0 必须为 0）\n", KlIsing(free, mf))
	fmt.Printf("        一轮就收敛：ELBO[0] - ELBO[-1] = %.3e\n",
		histf[0]-histf[len(histf)-1])

	// ---------- 强排斥：q 被迫高估联合 ----------
	rep := NewIsing([]float64{0, 0}, [][]float64{{0, -5}, {-5, 0}})
	mr, _ := CaviIsing(rep, 400, nil, nil)
	fmt.Printf("repel   m = %.6f  真边缘 = %.6f\n", mr[0], rep.Marginal(0))
	fmt.Printf("        q(z=1,1) = %.6f  真值 = %.6f\n",
		mr[0]*mr[1], rep.Pair(0, 1))

	// ---------- 高斯：低估方差的闭式 ----------
	mu := []float64{0.5, -1.0}
	lam := [][]float64{{4.0, 1.5}, {1.5, 2.0}}
	sigma := Inv(lam)
	mg, vg, histg := CaviGauss(mu, lam, 80, nil)
	klg := GaussKl(mg, vg, mu, lam)
	rho := sigma[0][1] / math.Sqrt(sigma[0][0]*sigma[1][1])
	fmt.Printf("gauss   q 方差 = %v\n", vg)
	fmt.Printf("        真边缘方差 = [%.6f %.6f]（q 必然更小）\n",
		sigma[0][0], sigma[1][1])
	fmt.Printf("        KL = %.10f  −½ln(1−ρ²) = %.10f  (ρ = %.6f)\n",
		klg, -0.5*math.Log(1-rho*rho), rho)
	fmt.Printf("        ELBO + KL − logZ = %.3e\n",
		histg[len(histg)-1]+klg-GaussLogZ(lam))
}
