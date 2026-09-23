package main

import (
	"fmt"

	"queuemodel"
)

func main() {
	b, _ := queuemodel.ErlangB(1.0, 2)
	c, _ := queuemodel.ErlangC(1.0, 2)
	c2, _ := queuemodel.ErlangCViaB(1.0, 2)
	fmt.Printf("Erlang B(1,2)=%.6f C(1,2)=%.6f (via B %.6f)\n", b, c, c2)

	for _, n := range []int{2, 3, 4, 8} {
		m, err := queuemodel.NewMMC(8, 5, n)
		if err != nil {
			fmt.Println("skip:", err)
			continue
		}
		wq, _ := m.Wq()
		w, _ := m.W()
		cp, _ := m.WaitingProb()
		fmt.Printf("c=%d rho=%.3f C=%.4f Wq=%.2fms W=%.2fms\n",
			n, m.Rho(), cp, wq*1000, w*1000)
	}

	wq, _ := queuemodel.MG1Wq(500, 1000, 0.0)
	fmt.Printf("M/D/1 Wq=%.4fms\n", wq*1000)

	k, _ := queuemodel.NewMM1K(500, 1000, 5)
	fmt.Printf("M/M/1/5: p_block=%.6f lambda_a=%.3f throughput=%.3f L=%.4f\n",
		k.PBlock(), k.LambdaA(), k.Throughput(), k.L())
}
