package main

import "fmt"

func main() {
	fmt.Println("== tableSizeFor(cap) = -1 >>> numberOfLeadingZeros(cap-1) ==")
	for _, cap := range []int32{0, 1, 2, 3, 5, 7, 8, 9, 16, 17, 63, 64, 65, 1000, maximumCapacity} {
		fmt.Printf("  %12d -> %d\n", cap, tableSizeFor(cap))
	}
	fmt.Println("  注意 nlz(0)=32，Java 移位量 & 31 ⇒ -1>>>32 即 -1>>>0 = -1 ⇒ 返回 1")

	fmt.Println("\n== hash 扰动 h ^ (h >>> 16) ==")
	for _, h := range []int32{0x0000FFFF, 0x00010000, 0x12345678, -65536} {
		fmt.Printf("  0x%08X -> 0x%08X\n", uint32(h), uint32(spread(h)))
	}

	fmt.Println("\n== resize 的 newCap / newThr（loadFactor=0.75）==")
	var cap, thr int32
	for i := 0; i < 6; i++ {
		cap, thr = nextCapacityAfterResize(cap, thr, 0.75)
		fmt.Printf("  cap=%-12d thr=%d\n", cap, thr)
	}
	c, t := nextCapacityAfterResize(maximumCapacity, 1<<29, 0.75)
	fmt.Printf("  触顶: cap=%d thr=%d (Integer.MAX_VALUE)\n", c, t)

	fmt.Println("\n== new HashMap<>(0)：cap=1 时 thr=(int)0.75=0 ⇒ 连着抬 ==")
	m := newHashMap(0, 0.75)
	fmt.Printf("  初始: cap=%d thr=%d\n", m.capacity(), m.threshold)
	for i := 1; i <= 8; i++ {
		m.put(fmt.Sprintf("k%d", i), int32(i), nil)
		fmt.Printf("  第 %2d 次 put: cap=%-4d thr=%-4d size=%d\n", i, m.capacity(), m.threshold, m.size)
	}

	fmt.Printf("\n== 树化两道门：TREEIFY_THRESHOLD=%d 与 MIN_TREEIFY_CAPACITY=%d ==\n",
		treeifyThreshold, minTreeifyCapacity)
	m2 := newHashMap(16, 0.75)
	m2.put("t0", 0, nil)
	for i := 1; i <= 8; i++ {
		m2.put(fmt.Sprintf("t%d", i), int32(i*64), nil)
	}
	fmt.Printf("  16 槽表 + 9 个同槽 key: cap=%d 树桶=%d resizes=%d\n",
		m2.capacity(), m2.treeBuckets(), m2.resizes)

	m3 := newHashMap(64, 0.75)
	m3.put("u0", 0, nil)
	for i := 1; i <= 8; i++ {
		m3.put(fmt.Sprintf("u%d", i), int32(i*64), nil)
	}
	fmt.Printf("  64 槽表 + 9 个同槽 key: cap=%d 树桶=%d treeifies=%d\n",
		m3.capacity(), m3.treeBuckets(), m3.treeifies)

	fmt.Printf("\n== TreeNode.split 的三种结局（UNTREEIFY_THRESHOLD=%d）==\n", untreeifyThreshold)
	// 1) 7 | 7：两侧都 > 6，两次 treeify
	m4 := newHashMap(64, 0.75)
	m4.put("z", 0, nil)
	var seven []*node
	for i := 0; i < 7; i++ {
		seven = append(seven, &node{hash: int32(1 + i), key: fmt.Sprintf("lo%d", i)})
	}
	for i := 0; i < 7; i++ {
		seven = append(seven, &node{hash: int32(64 + 1 + i), key: fmt.Sprintf("hi%d", i)})
	}
	m4.table[5] = &bin{nodes: seven, isTree: true}
	m4.treeifies, m4.untreeify = 0, 0
	m4.resize()
	fmt.Printf("  [7|7] cap=%d lo 是树=%v hi 是树=%v treeify=%d\n",
		m4.capacity(), m4.table[5].isTree, m4.table[69].isTree, m4.treeifies)

	// 2) 9 | 0：整棵树没被拆开 ⇒ 零次 treeify
	m5 := newHashMap(64, 0.75)
	m5.put("z", 0, nil)
	var nine []*node
	for i := 0; i < 9; i++ {
		nine = append(nine, &node{hash: int32(1 + i), key: fmt.Sprintf("p%d", i)})
	}
	m5.table[7] = &bin{nodes: nine, isTree: true}
	m5.treeifies, m5.untreeify = 0, 0
	m5.resize()
	fmt.Printf("  [9|0] cap=%d 仍是树=%v hi 为空=%v treeify=%d\n",
		m5.capacity(), m5.table[7].isTree, m5.table[71] == nil, m5.treeifies)

	// 3) 8 | 1：hi 侧 <= 6 ⇒ untreeify
	m6 := newHashMap(64, 0.75)
	m6.put("z", 0, nil)
	var eightOne []*node
	for i := 0; i < 8; i++ {
		eightOne = append(eightOne, &node{hash: int32(1 + i), key: fmt.Sprintf("q%d", i)})
	}
	eightOne = append(eightOne, &node{hash: 65, key: "r0"})
	m6.table[9] = &bin{nodes: eightOne, isTree: true}
	m6.treeifies, m6.untreeify = 0, 0
	m6.resize()
	fmt.Printf("  [8|1] lo 是树=%v hi 是树=%v untreeify=%d\n",
		m6.table[9].isTree, m6.table[73].isTree, m6.untreeify)
}
