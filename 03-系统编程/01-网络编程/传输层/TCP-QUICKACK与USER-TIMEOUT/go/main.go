package main

import "fmt"

func main() {
	fmt.Println("== Go 侧自检 ==")
	n, fails := selfcheck()
	fmt.Printf("  assertions=%d  fails=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL:", f)
	}

	fmt.Println("\n== ato 演化(与 python/main.py 第 ② 表同构) ==")
	e := newDelackEngine(1460, 65536)
	prev := 0
	for _, t := range []int{0, 1, 3, 8, 20, 41, 71, 101, 201, 500} {
		ato := e.OnDataRecv(t)
		fmt.Printf("  t=%-4d m=%-4d ato=%d\n", t, t-prev, ato)
		prev = t
	}

	fmt.Println("\n== model_timeout(boundary, RTO_MIN, RTO_MAX) ==")
	for _, b := range []int{0, 1, 5, 9, 10, 15} {
		ms := ModelTimeout(b, tcpRtoMin, tcpRtoMax)
		fmt.Printf("  boundary=%-3d %-8d ms (%.1f s)\n", b, ms, float64(ms)/1000)
	}

	fmt.Println("\n== TCP_USER_TIMEOUT 钳制 ==")
	for _, el := range []int{0, 5000, 9900, 10000, 99999} {
		fmt.Printf("  elapsed=%-6d -> 装填 %d ms\n",
			el, ClampRtoToUserTimeout(10000, tcpRtoMin, el))
	}
}
