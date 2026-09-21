package main

// demo 517 的 Go 实验台,与 python/main.py 的 E1/E3/E5/E6/E7 同构。
// 运行:`go run .`

import (
	"fmt"
	"time"
)

func f4(v float64) string { return fmt.Sprintf("%9.4f", v) }

func main() {
	// E1 左开右闭
	series := []Sample{{0, 10}, {30, 40}, {60, 70}}
	sel := SelectRange(series, 60, 60)
	fmt.Println("E1 selected:", sel)

	// E2 increase == rate * range
	s2 := []Sample{{0, 0}, {15, 100}, {30, 200}, {45, 300}, {60, 400}}
	sel2 := SelectRange(s2, 60, 60)
	r, _ := Rate(sel2, 60, 60)
	inc, _ := Increase(sel2, 60, 60, true)
	fmt.Println("E2 rate:", f4(r), "increase:", f4(inc), "rate*60:", f4(r*60))

	// E3 外推使整数增量给出非整数结果
	s3 := []Sample{{10, 100}, {25, 101}, {37, 102}, {40, 103}}
	inc3, _ := Increase(SelectRange(s3, 40, 30), 40, 30, true)
	fmt.Println("E3 increase:", f4(inc3))

	// E5 计数器回绕
	rst := []Sample{{0, 100}, {30, 130}, {45, 10}, {60, 60}}
	selr := SelectRange(rst, 60, 60)
	p, _ := Increase(selr, 60, 60, true)
	n, _ := Increase(selr, 60, 60, false)
	fmt.Println("E5 counter:", f4(p), "gauge:", f4(n))

	// E6 rate 与 irate
	s6 := []Sample{{0, 0}, {10, 10}, {20, 20}, {58, 30}, {60, 200}}
	sel6 := SelectRange(s6, 60, 60)
	r6, _ := Rate(sel6, 60, 60)
	i6, _ := IRate(sel6)
	fmt.Println("E6 rate:", f4(r6), "irate:", f4(i6))

	// E7 窗口长度
	start := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	d1m, _ := ParseDuration("1M")
	for _, probe := range []time.Time{
		time.Date(2026, 2, 10, 0, 0, 0, 0, time.UTC),
		time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC),
	} {
		lo, hi, _ := CalendarWindow(probe, start, d1m)
		fmt.Printf("E7 calendar 1M @%s : %d days\n",
			probe.Format("2006-01-02"), int(hi.Sub(lo).Hours()/24))
	}
	d30, _ := ParseDuration("30d")
	lo, hi, _ := RollingWindow(time.Date(2026, 7, 10, 0, 0, 0, 0, time.UTC), d30)
	fmt.Printf("E7 rolling 30d      : %d days\n", int(hi.Sub(lo).Hours()/24))

	// E9 rawType
	a, _ := SLIFromRaw(0.003, "success")
	b, _ := SLIFromRaw(0.003, "failure")
	fmt.Println("E9 success:", f4(a), "failure:", f4(b))

	// 异常路径
	if _, err := ParseDuration("10x"); err != nil {
		fmt.Println("X bad duration rejected:", err)
	}
	if _, _, err := RollingWindow(time.Now(), d1m); err != nil {
		fmt.Println("X rolling rejects calendar:", err)
	}
	if _, _, err := CalendarWindow(time.Now(), start, d30); err != nil {
		fmt.Println("X calendar rejects fixed:", err)
	}
	fmt.Println("X month clamp 2026-01-31 +1M =",
		AddMonths(time.Date(2026, 1, 31, 0, 0, 0, 0, time.UTC), 1).Format("2006-01-02"))
}
