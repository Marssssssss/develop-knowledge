package main

import (
	"fmt"
	"math"

	"uslmodel"
)

func main() {
	ray := uslmodel.Model{Gamma: 21.848843, Alpha: 0.057771, Beta: 0.0}
	nmax, xmax := ray.Peak()
	nopt, xopt := ray.Optimal()
	fmt.Printf("raytracer: Nmax=%v Xmax=%.1f Xlim=%.1f Nopt=%.2f Xopt=%.1f\n",
		nmax, xmax, ray.Limit(), nopt, xopt)
	fmt.Printf("  C(1e7)=%.6f  1/alpha=%.6f\n", ray.C(1e7), 1.0/ray.Alpha)

	spec := uslmodel.Model{Gamma: 89.9952382, Alpha: 0.0277285, Beta: 0.0001044}
	nmax, xmax = spec.Peak()
	nopt, xopt = spec.Optimal()
	fmt.Printf("specsdm91: Nmax=%.2f Xmax=%.1f Xlim=%.1f Nopt=%.2f Xopt=%.1f\n",
		nmax, xmax, spec.Limit(), nopt, xopt)

	half := uslmodel.Model{Gamma: 89.9952382, Alpha: 0.0277285, Beta: 0.00005}
	nmax, _ = half.Peak()
	fmt.Printf("  beta halved -> Nmax=%.2f\n", nmax)

	// 数值扫描校验峰值公式
	best, bestX := 0.0, -1.0
	for N := 1.0; N <= 400.0; N += 0.01 {
		if v, err := spec.X(N); err == nil && v > bestX {
			best, bestX = N, v
		}
	}
	fmt.Printf("  numeric argmax=%.2f (formula %.2f)\n", best, nmax)
	fmt.Printf("  retrograde: X(216)=%.1f < X(%.0f)=%.1f\n",
		mustX(spec, 216), nmax, mustX(spec, math.Round(nmax)))
}

func mustX(m uslmodel.Model, N float64) float64 {
	v, err := m.X(N)
	if err != nil {
		panic(err)
	}
	return v
}
