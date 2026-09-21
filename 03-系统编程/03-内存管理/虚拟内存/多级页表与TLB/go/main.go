// Package main —— 多级页表 / TLB / 缺页中断的演示入口。
package main

import (
	"errors"
	"fmt"
)

// errEINVAL 对应 man page 里的 EINVAL。
var errEINVAL = errors.New("EINVAL: WP 与 RWP 不能同时注册")

func demoAddress() {
	pt := &PageTable{Levels: 4}
	va := BuildVA([]int{1, 2, 3, 4}, 0x123, 4)
	idx, n := pt.Walk(va)
	fmt.Printf("va = 0x%016X, indices = %v, walk accesses = %d\n", va, idx, n)
	fmt.Println("canonical(0x0000800000000000) =", IsCanonical(0x0000800000000000, 4))
	fmt.Println("canonical(0xffff800000000000) =", IsCanonical(0xffff800000000000, 4))

	p5 := &PageTable{Levels: 5}
	_, n5 := p5.Walk(BuildVA([]int{1, 2, 3, 4, 5}, 0, 5))
	fmt.Println("5-level walk accesses =", n5)
	fmt.Println("VA 5L/4L =", VaLimit5L/VaLimit4L, " PA 5L/4L =", PaLimit5L/PaLimit4L)
}

func demoTLB() {
	fmt.Println("TLB reach(512, 4KiB) =", TLBReach(512, PageSize))
	fmt.Println("TLB reach(512, 2MiB) / reach(512, 4KiB) =",
		TLBReach(512, HugePageSize)/TLBReach(512, PageSize))

	t := NewTLB(4)
	t.Lookup(7)
	t.Downgrade(7) // mprotect 降权但没 shootdown
	t.Lookup(7, true)
	fmt.Println("stale allowed before shootdown =", t.StaleAllowed)
	t.Shootdown(7)
	fmt.Println("write after shootdown hits =", t.Lookup(7, true))
}

func demoFaults() {
	v := NewVMA(2, "rw")
	fmt.Println("first touch      ->", v.Access(0, false))
	v.Resident[0] = true
	fmt.Println("read again       ->", v.Access(0, false))

	p := NewVMA(2, "rw")
	p.Resident = []bool{true, true}
	p.Data = []int{11, 22}
	c := p.ForkCow()
	fmt.Println("child write (COW)->", c.Access(0, true))
	fmt.Println("child write again->", c.Access(0, true))

	w := NewVMA(2, "rw")
	w.Resident = []bool{true, true}
	if err := w.RegisterUffd(UffdModeWP); err != nil {
		fmt.Println("register WP err:", err)
	}
	fmt.Println("write under WP   ->", w.Access(0, true))

	x := NewVMA(2, "rw")
	fmt.Println("register WP|RWP  ->", x.RegisterUffd(UffdModeWP|UffdModeRWP))

	d := NewVMA(2, "rw")
	d.Resident = []bool{true, true}
	d.Data = []int{7, 8}
	d.MadviseDontneed()
	fmt.Println("after MADV_DONTNEED mincore =", d.Mincore(), " data =", d.Data)
}

func main() {
	demoAddress()
	demoTLB()
	demoFaults()
}
