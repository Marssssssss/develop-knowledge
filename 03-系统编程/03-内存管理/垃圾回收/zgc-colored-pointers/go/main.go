// Package main —— ZGC 着色指针的演示入口（并发重定位 + 自愈 barrier，对照 Shenandoah）。
package main

import "fmt"

const heapBase = 1 << 20 // 演示用堆基址（64 KiB 对齐）

func demoLayout() {
	fmt.Printf("load  mask = 0x%04X\n", ZPointerLoadMetadataMask)
	fmt.Printf("mark  mask = 0x%04X\n", ZPointerMarkMetadataMask)
	fmt.Printf("store mask = 0x%04X\n", ZPointerStoreMetadataMask)
	fmt.Println("load shift table =", ZPointerLoadShiftTable)
	fmt.Printf("uncolor(color(0x%X, Remapped01)) = 0x%X\n",
		heapBase, Uncolor(Color(heapBase, ZPointerRemapped01)))
}

func demoZGC() {
	g := NewZGlobalsPointers()
	h := NewZHeap(g)
	a := heapBase + (1 << ZAddressShift)
	b := heapBase + (1 << ZAddressShift)*2
	h.Fields["f"] = Color(a, ZPointerRemapped00)
	fmt.Printf("初始颜色: 0x%X, 当前 remap 位 0x%X\n", h.Fields["f"], g.Remapped)

	h.Forwarding[a] = b // 并发重定位
	g.FlipYoungRelocateStart()
	fmt.Printf("翻转后 remap 位 0x%X -> 字段颜色过期\n", g.Remapped)

	v1 := h.LoadBarrier("f")
	fmt.Printf("第 1 次 load: 0x%X (慢路径 %d 次), 字段被改写为 0x%X\n",
		v1, h.SlowPaths, h.Fields["f"])
	v2 := h.LoadBarrier("f")
	fmt.Printf("第 2 次 load: 0x%X (慢路径仍为 %d 次)\n", v2, h.SlowPaths)
}

func demoShenandoah() {
	s := &ShenandoahHeap{Brooks: map[int]int{}, Fields: map[string]int{}}
	s.Fields["f"] = heapBase + 64
	s.Brooks[heapBase+64] = heapBase + 128
	for i := 0; i < 5; i++ {
		s.LoadReferenceBarrier("f")
	}
	fmt.Printf("Shenandoah LRB: %d 次 load -> %d 次额外对象头读（恒 1:1）\n",
		s.Loads, s.ExtraReads)
}

func demoRemapCycle() {
	g := NewZGlobalsPointers()
	seq := []int{g.Remapped}
	for i := 0; i < 2; i++ {
		g.FlipYoungRelocateStart()
		seq = append(seq, g.Remapped)
		g.FlipOldRelocateStart()
		seq = append(seq, g.Remapped)
	}
	fmt.Print("remap 循环: ")
	for _, v := range seq {
		fmt.Printf("0x%X ", v)
	}
	fmt.Println()
}

func main() {
	demoLayout()
	demoRemapCycle()
	demoZGC()
	demoShenandoah()
}
