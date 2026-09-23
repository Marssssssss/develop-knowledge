package main

import (
	"fmt"

	"overload"
)

func main() {
	shed, err := overload.NewShedder(map[string]float64{
		overload.Sheddable: 0.6, overload.SheddablePlus: 0.7,
		overload.Critical: 0.85, overload.CriticalPlus: 0.95,
	})
	if err != nil {
		panic(err)
	}
	for _, u := range []float64{0.5, 0.65, 0.75, 0.9, 0.99} {
		fmt.Printf("util=%.2f rejected=%v invariant=%v\n",
			u, shed.RejectedSet(u), shed.CheckInvariant(u))
	}

	for _, K := range []float64{1.1, 2.0, 3.0} {
		s, a, br, lr, _ := overload.ClientThrottleEquilibrium(1000, 200, K)
		fmt.Printf("K=%.1f sent=%.1f accepted=%.1f backendRej=%.1f localRej=%.1f ratio=%.2f\n",
			K, s, a, br, lr, br/a)
	}

	g, _ := overload.EnvoyGradient(10, 10.5, 0.0)
	g2, _ := overload.EnvoyGradient(10, 10.5, 0.1)
	fmt.Printf("gradient no-buffer=%.4f with-buffer=%.4f\n", g, g2)
	fmt.Printf("limit 100 -> %.2f\n", overload.EnvoyUpdate(100, g2, 1, 1e9))

	v, _ := overload.NewVegasLimit(100, 1000, 1.0, func() float64 { return 0.5 })
	v.Update(10, 100)
	fmt.Printf("vegas alpha=%d beta=%d threshold=%d\n", v.Alpha(), v.Beta(), v.Threshold())
	q, _ := overload.QueueSize(100, 10, 20)
	fmt.Printf("queueSize(100,10,20)=%d\n", q)
	n, _ := v.Update(10, 100)
	fmt.Printf("after rtt=10 (q=0) -> limit=%d\n", n)
	n, _ = v.Update(100, 60)
	fmt.Printf("after rtt=100,inflight=60 -> limit=%d\n", n)
}
