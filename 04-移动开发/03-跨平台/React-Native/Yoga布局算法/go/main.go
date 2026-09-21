package main

import (
	"fmt"
	"math"

	"yoga"
)

func nan() float64 { return math.NaN() }

func item(basis, grow, shrink, minMain, maxMain float64) *yoga.FlexItem {
	return &yoga.FlexItem{Basis: basis, Grow: grow, Shrink: shrink, MinMain: minMain, MaxMain: maxMain}
}

func show(label string, sizes []float64, leftover float64) {
	fmt.Printf("  %-26s sizes=[", label)
	for i, s := range sizes {
		if i > 0 {
			fmt.Print(", ")
		}
		fmt.Printf("%.3f", s)
	}
	fmt.Printf("] leftover=%.2f\n", leftover)
}

func main() {
	fmt.Println("== roundValueToPixelGrid ==")
	for _, v := range []float64{1.4, 1.5, 1.6, 2.5, -2.2, -2.6} {
		fmt.Printf("  round(%.1f) = %.1f\n", v, yoga.RoundValueToPixelGrid(v, 1.0, false, false))
	}
	fmt.Printf("  round(1.4, psf=2) = %.2f\n", yoga.RoundValueToPixelGrid(1.4, 2.0, false, false))
	fmt.Printf("  text node left 33.7 -> %.1f\n",
		yoga.RoundValueToPixelGrid(33.7, 1.0, false, true))

	fmt.Println("== FlexLine 分行（basis 40 ×3 / 可用 100 / gap 10）==")
	line := yoga.CalculateFlexLine(
		[]*yoga.FlexItem{item(40, 0, 1, nan(), nan()), item(40, 0, 1, nan(), nan()), item(40, 0, 1, nan(), nan())},
		100.0, 10.0, true)
	fmt.Printf("  行内项目数=%d 已消耗=%.1f\n", len(line.Items), line.SizeConsumed)

	fmt.Println("== 两遍分配（basis 20 ×3，grow 1，max 25 / 35 / 无）==")
	mk := func() []*yoga.FlexItem {
		return []*yoga.FlexItem{item(20, 1, 1, nan(), 25), item(20, 1, 1, nan(), 35), item(20, 1, 1, nan(), nan())}
	}
	for _, c := range []struct {
		name   string
		errata int
	}{{"原始总量(无 errata)", yoga.ErrataNone}, {"running totals(默认)", yoga.ErrataDefault}} {
		l := yoga.CalculateFlexLine(mk(), 1e9, 0, false) // 先把三项塞进同一行
		l.SizeConsumed = 60
		l.TotalGrow = 3
		sizes, leftover := yoga.DistributeFreeSpace(l, 100.0, c.errata)
		show(c.name, sizes, leftover)
	}

	fmt.Println("== 收缩（basis 60 ×2 / 可用 100）==")
	sh := yoga.CalculateFlexLine([]*yoga.FlexItem{item(60, 0, 1, nan(), nan()), item(60, 0, 1, nan(), nan())},
		1e9, 0, false)
	sh.SizeConsumed, sh.TotalShrinkScaled = 120, -120
	sizes, leftover := yoga.DistributeFreeSpace(sh, 100.0, yoga.ErrataDefault)
	show("shrink 1:1", sizes, leftover)

	sh2 := yoga.CalculateFlexLine([]*yoga.FlexItem{item(60, 0, 1, 55, nan()), item(60, 0, 1, 55, nan())},
		1e9, 0, false)
	sh2.SizeConsumed, sh2.TotalShrinkScaled = 120, -120
	sizes, leftover = yoga.DistributeFreeSpace(sh2, 100.0, yoga.ErrataDefault)
	show("两项都 min 55（溢出）", sizes, leftover)
}
