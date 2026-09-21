package main

// demo 520 的 Go 实验台,与 python/main.py 的 E1/E6 同构。
// 运行:`go run .`

import (
	"fmt"
	"math/rand"
)

func f3(v float64) string { return fmt.Sprintf("%9.3f", v) }

const (
	base = 5.0
	capV = 2000.0
	rtt  = 10.0
	slot = 1.0
)

func main() {
	// E1 expo 与封顶
	e, _ := Make("expo", base, capV, rand.New(rand.NewSource(1)))
	fmt.Println("E1 expo(n):")
	for n := 0; n <= 10; n++ {
		fmt.Printf("  n=%-3d %s\n", n, f3(e.Next(n)))
	}

	// E2 各策略在 n=3 下的区间(expo=40)
	fmt.Println("\nE2 ranges at n=3 (20000 samples):")
	rng := rand.New(rand.NewSource(7))
	for _, name := range []string{"expo", "equal", "full", "decorr"} {
		lo, hi, tot := math.MaxFloat64, -math.MaxFloat64, 0.0
		for i := 0; i < 20000; i++ {
			b, _ := Make(name, base, capV, rng)
			v := b.Next(3)
			if v < lo {
				lo = v
			}
			if v > hi {
				hi = v
			}
			tot += v
		}
		fmt.Printf("  %-8s min=%-10s max=%-10s mean=%s\n", name, f3(lo), f3(hi), f3(tot/20000))
	}

	// E6 100 个客户端时各策略的工作量
	fmt.Println("\nE6 100 clients (seed 20260921):")
	fmt.Printf("  %-10s %-10s %-12s\n", "strategy", "calls", "time(ms)")
	for _, name := range []string{"none", "expo", "equal", "full", "decorr"} {
		calls, tm, err := Simulate(100, name, rtt, base, capV, slot, 20260921)
		if err != nil {
			fmt.Println("error:", err)
			return
		}
		fmt.Printf("  %-10s %-10d %s\n", name, calls, f3(tm))
	}

	// 异常路径
	if _, err := Make("nope", base, capV, rng); err != nil {
		fmt.Println("\nX unknown strategy rejected:", err)
	}
	if _, _, err := Simulate(10, "nope", rtt, base, capV, slot, 1); err != nil {
		fmt.Println("X simulate rejects unknown strategy:", err)
	}
}
