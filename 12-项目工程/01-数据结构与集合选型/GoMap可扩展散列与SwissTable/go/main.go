package main

import "fmt"

const golden = uint64(0x9E3779B97F4A7C15)

func mix(i int) uint64 { return uint64(i) * golden }

func main() {
	fmt.Println("== 常量 ==")
	fmt.Printf("  MapGroupSlots=%d maxAvgGroupLoad=%d maxTableCapacity=%d\n",
		mapGroupSlots, maxAvgGroupLoad, maxTableCapacity)
	fmt.Printf("  ctrlEmpty=0x%02X ctrlDeleted=0x%02X\n", ctrlEmpty, ctrlDeleted)

	fmt.Println("\n== h1 / h2 ==")
	for _, h := range []uint64{0x00, 0x7F, 0x80, 0xFF, 0x1FF, ^uint64(0)} {
		fmt.Printf("  hash=0x%016X -> h1=%d h2=0x%02X\n", h, h1(h), h2(h))
	}

	fmt.Println("\n== 单表容量与 growthLeft ==")
	for _, c := range []int{8, 16, 64, 256, 1024} {
		fmt.Printf("  capacity=%-6d growthLeft=%-6d (%.3f)\n",
			c, maxGrowthLeft(c), float64(maxGrowthLeft(c))/float64(c))
	}

	fmt.Println("\n== NewMap(hint) 的目录推导 ==")
	for _, hint := range []int{8, 9, 100, 1000, 10000, 100000} {
		lay := newMapLayout(hint)
		if lay.small {
			fmt.Printf("  hint=%-8d 单组小图（dirLen=0，8 槽全可用）\n", hint)
		} else {
			fmt.Printf("  hint=%-8d target=%-8d dirLen=%-4d globalDepth=%d globalShift=%d 每表 %d 槽\n",
				hint, lay.targetCapacity, lay.dirLen, lay.globalDepth, lay.globalShift, lay.tableCapacity)
		}
	}

	fmt.Println("\n== 三角探测（组下标）==")
	for _, groups := range []int{8, 16} {
		fmt.Printf("  组数 %3d 从 h1=0 起: %v\n", groups, probeGroups(0, groups-1, 8))
	}

	fmt.Println("\n== 生命周期：小图 -> grow -> split -> 目录翻倍 ==")
	m := newGoMap(0)
	prevCap, prevDir, prevDepth := mapGroupSlots, 0, 0
	for i := 1; i <= 2600; i++ {
		m.put(fmt.Sprintf("k%d", i), mix(i))
		cap, dirLen, depth := m.totalCapacity(), len(m.dir), m.globalDepth
		if m.small {
			cap, dirLen = mapGroupSlots, 0
		}
		if cap != prevCap || dirLen != prevDir || depth != prevDepth {
			fmt.Printf("  第 %4d 个 capacity=%-5d dirLen=%-4d globalDepth=%d 表数=%d\n",
				i, cap, dirLen, depth, len(m.tables()))
			prevCap, prevDir, prevDepth = cap, dirLen, depth
		}
	}
	fmt.Printf("  splits=%d 目录翻倍=%d grows=%d\n", m.splits, m.dirDoublings, m.grows)

	fmt.Println("\n== 墓碑：只有『整组都满』时删除才产生墓碑 ==")
	t1 := newTable(8, 0, 0)
	for i := 1; i <= 7; i++ {
		t1.uncheckedPut(fmt.Sprintf("g%d", i), uint64(i)*0x400)
	}
	t1.delete("g1", 0x400)
	fmt.Printf("  单组表(8槽) 删 1 个 -> 墓碑=%d growthLeft=%d（单组表恒留空槽，永不产生墓碑）\n",
		t1.tombstones(), t1.growthLeft)

	t2 := newTable(16, 0, 0)
	for j := 1; j <= 8; j++ {
		t2.uncheckedPut(fmt.Sprintf("h%d", j), uint64(j)) // h1 = 0，全落组 0
	}
	gl := t2.growthLeft
	t2.delete("h1", 1)
	fmt.Printf("  16 槽表 组0 填满后删 1 个 -> 墓碑=%d growthLeft %d -> %d\n",
		t2.tombstones(), gl, t2.growthLeft)
	t2.put("new", 0x7F01)
	fmt.Printf("  再插 1 个复用墓碑 -> 墓碑=%d growthLeft=%d\n", t2.tombstones(), t2.growthLeft)

	fmt.Println("\n== 1024 槽表满了之后：split 而不是继续翻倍 ==")
	t3 := newTable(1024, 0, 3)
	gm := &goMap{globalDepth: 3, dir: []*table{t3}}
	for i := 0; i < 896; i++ {
		t3.uncheckedPut(fmt.Sprintf("f%d", i), mix(i))
	}
	fmt.Printf("  拆分前: dirLen=%d globalDepth=%d capacity=%d used=%d growthLeft=%d\n",
		len(gm.dir), gm.globalDepth, gm.totalCapacity(), t3.used, t3.growthLeft)
	t3.rehash(gm)
	fmt.Printf("  拆分后: dirLen=%d globalDepth=%d capacity=%d 表数=%d splits=%d 目录翻倍=%d\n",
		len(gm.dir), gm.globalDepth, gm.totalCapacity(), len(gm.tables()), gm.splits, gm.dirDoublings)
	fmt.Printf("  分裂位 localDepthMask(4) = 0x%X\n", localDepthMask(4))
}
