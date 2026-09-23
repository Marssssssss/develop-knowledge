package main

import (
	"fmt"

	"little"
)

func main() {
	m, err := little.NewMM1(500, 1000)
	if err != nil {
		panic(err)
	}
	fmt.Printf("rho=%.2f L=%.4f Lq=%.4f W=%.6f Wq=%.6f\n",
		m.Rho(), m.L(), m.Lq(), m.W(), m.Wq())
	// Little 定律在 M/M/1 上是代数恒等式
	fmt.Printf("lambda*W = %.12f, L = %.12f\n", m.Lambda*m.W(), m.L())

	if _, err := little.NewMM1(1000, 1000); err != nil {
		fmt.Println("rho=1 rejected:", err)
	}

	wasted, useful, _ := little.DeadQueue(10000, 500, 2.0)
	fmt.Printf("dead queue: wasted=%d useful=%d\n", wasted, useful)

	for _, rho := range []float64{0.5, 0.8, 0.9, 0.99} {
		mm, _ := little.NewMM1(rho*1000, 1000)
		fmt.Printf("rho=%.2f multiplier=%.4f W=%.4fs\n",
			rho, mm.WaitMultiplier(), mm.W())
	}
	fmt.Println("pool:", little.PoolSize(500, 0.2, 1.0))
}
