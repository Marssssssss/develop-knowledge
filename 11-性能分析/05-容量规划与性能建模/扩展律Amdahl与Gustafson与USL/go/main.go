package main

import (
	"fmt"

	"scaling"
)

func main() {
	// hpc101 的数值例：P = 0.9, N = 100 -> Gustavson 90.1
	g, _ := scaling.Gustafson(0.9, 100)
	fmt.Printf("Gustafson(0.9,100) = %.4f\n", g)

	// Amdahl 在同一 P 下的天花板
	fmt.Printf("Amdahl limit (P=0.9) = %.1f, S(N=100) = %.4f\n",
		scaling.AmdahlLimit(0.9), mustAmdahl(0.9, 100))

	// β=0, γ=1 的 USL 必须逐点等于 Amdahl
	for _, N := range []float64{2, 8, 64, 1024} {
		a, _ := scaling.AmdahlSerial(0.1, N)
		u, _ := scaling.USLCapacity(N, 0.1, 0.0, 1.0)
		fmt.Printf("N=%-6.0f Amdahl(f=0.1)=%.6f USL(alpha=0.1)=%.6f\n", N, a, u)
	}

	// UVA：99% 并行才够撑到 ~100 进程
	s99, _ := scaling.Amdahl(0.99, 100)
	fmt.Printf("P=0.99 N=100: S=%.2f efficiency=%.3f\n", s99, scaling.Efficiency(s99, 100))
	fmt.Printf("target 50x on 100 procs needs P=%.5f\n", scaling.ParallelFractionFor(50, 100))
}

func mustAmdahl(P, N float64) float64 {
	v, err := scaling.Amdahl(P, N)
	if err != nil {
		panic(err)
	}
	return v
}
