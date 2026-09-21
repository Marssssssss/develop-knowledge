package main

// demo 518 的 Go 实验台,与 python/main.py 的 E1/E3/E5/E7/E8 同构。
// 运行:`go run .`

import (
	"fmt"
	"math"
)

func f6(v float64) string { return fmt.Sprintf("%10.6f", v) }

// Google 在 SRE workbook 给的默认档位(sloth 源码注释 + windows YAML 实读)
var google = []struct {
	name string
	pct  float64
	long float64
}{
	{"page_quick", 2.0, 1 * hourSeconds},
	{"page_slow", 5.0, 6 * hourSeconds},
	{"ticket_quick", 10.0, 1 * daySeconds},
	{"ticket_slow", 10.0, 3 * daySeconds},
}

func main() {
	// E1 错误预算
	m, _ := BudgetMinutes(0.999, 30*daySeconds)
	fmt.Println("E1 99.9%/30d budget minutes:", f6(m))

	// E3 各档位燃烧率
	for _, period := range []float64{30 * daySeconds, 28 * daySeconds} {
		fmt.Printf("\nE3 period %.0fd:\n", period/daySeconds)
		for _, g := range google {
			s, _ := BurnRateFactor(g.pct, period, g.long)
			fmt.Printf("  %-14s pct=%-5.1f long=%-6.0f speed=%s\n",
				g.name, g.pct, g.long, f6(s))
		}
	}

	// E4 交叉验证:由燃烧率反推错误率,长窗内恰好花掉 pct%
	fmt.Println("\nE4 cross-check (consumed should equal pct/100):")
	for _, g := range google {
		br, _ := BurnRateFactor(g.pct, 30*daySeconds, g.long)
		er := br * 0.001
		c, _ := BudgetConsumed(er, 0.999, g.long, 30*daySeconds)
		fmt.Printf("  %-14s br=%-10s er=%-12s consumed=%s\n",
			g.name, f6(br), f6(er), f6(c))
	}

	// E5 TTE
	fmt.Println("\nE5 time to exhaustion (hours):")
	for _, g := range google {
		br, _ := BurnRateFactor(g.pct, 30*daySeconds, g.long)
		tte := TimeToExhaustion(br, 30*daySeconds)
		fmt.Printf("  %-14s %s\n", g.name, f6(tte/hourSeconds))
	}
	fmt.Println("  burn=0 ->", TimeToExhaustion(0, 30*daySeconds) == math.Inf(1))

	// E7 三种预算口径
	good := append(repeat(1, 11), 1000)
	total := append(repeat(2, 11), 1000)
	r7, _ := AllMethods(good, total, 0.99)
	fmt.Println("\nE7 occurrences:", f6(r7["occurrences"]),
		"timeslices:", f6(r7["timeslices"]),
		"ratio_timeslices:", f6(r7["ratio_timeslices"]))

	// E8a 收敛条件:切片比值只取 0 或 1
	g8 := append(repeat(100, 99), 0)
	t8 := repeat(100, 100)
	a8, _ := AllMethods(g8, t8, 0.99)
	fmt.Println("E8a all three:", f6(a8["occurrences"]), f6(a8["timeslices"]),
		f6(a8["ratio_timeslices"]))
	a82, _ := AllMethods(repeat(99, 100), t8, 0.99)
	fmt.Println("E8a2 spread out:", f6(a82["occurrences"]), f6(a82["timeslices"]))

	// 异常路径
	if _, err := BurnRateFactor(2.0, 30*daySeconds, 0); err != nil {
		fmt.Println("X zero window rejected:", err)
	}
	if _, _, err := Timeslices(good, total, 1.5); err != nil {
		fmt.Println("X bad target rejected:", err)
	}
	if _, ok := Occurrences([]int{0}, []int{0}); !ok {
		fmt.Println("X zero total -> no value")
	}
}

func repeat(v, n int) []int {
	out := make([]int, n)
	for i := range out {
		out[i] = v
	}
	return out
}
