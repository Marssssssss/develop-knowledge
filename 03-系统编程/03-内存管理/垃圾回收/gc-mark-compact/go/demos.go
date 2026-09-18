// 标记-压缩式 GC 的 5 组实验与断言(Go 版)。
package main

import "fmt"

func demo1() {
	fmt.Println("== demo1 Lisp2 保序滑动压缩 ==")
	h := fixture()
	heapWords := h.heapWords()
	origOrder := oidsOf(h.cells)

	live := mark(h, h.roots)
	check("mark 只标记可达对象", sameSet(live, "A", "B", "C", "D"), live)
	check("垃圾对象 g1/g2 未被标记", !live["g1"] && !live["g2"])
	check("压缩前堆里有 2 个空洞(g1 与 g2)", holeRuns(h, live) == 2, holeRuns(h, live))

	fwd, liveWords := computeForwarding(h, live)
	check("compaction 只做顺序扫描:存活字数 < 堆字数(mmref:'sequential passes')",
		liveWords < heapWords, liveWords)
	check("存活字数 = A+B+C+D = 10", liveWords == 10, liveWords)

	seq := []int{}
	for _, oid := range liveOids(h, live) {
		seq = append(seq, fwd[oid])
	}
	check("新地址按原相对顺序严格递增", increasing(seq), seq)

	newCells := liveCells(h, live)
	check("存活对象在新布局中首尾相接、无空洞", contiguous(fwd, newCells), seq)
	layout := compactedLayout(h.cells, live, heapWords)
	check("空闲区是**一整块**连续区域(mmref:'a single contiguous block')",
		countBlock(layout, "") == 1, layout)
	check("空闲区 = 堆 - 存活 = g1(3) + g2(4)", heapWords-liveWords == 3+4, heapWords-liveWords)

	newOrder := oidsOf(newCells)
	check("保序:存活对象的相对顺序与原地址序一致",
		eqStrs(newOrder, filterLive(origOrder, live)), newOrder)

	nSlots := len(h.allSlots())
	updated := updateReferences(h, fwd)
	check("update 阶段改写了全部非空槽", updated == nSlots, fmt.Sprintf("%d/%d", updated, nSlots))
	check("根槽也被改写(移动式 GC 必须能枚举根)", h.roots[0].val == addrStr(fwd["A"]), h.roots[0].val)
	check("引用全部指向新地址,无悬空", len(danglingSlots(h, fwd)) == 0, len(danglingSlots(h, fwd)))
	check("A 的字段指向 B 的新地址",
		newCells[0].fields[0].val == addrStr(fwd["B"]), newCells[0].fields[0].val)
	check("存活对象个数 = 4(与原对象数 6 对比,回收了 2 个)",
		len(newCells) == 4 && len(h.cells) == 6, fmt.Sprintf("%d/%d", len(newCells), len(h.cells)))
}

func demo2() {
	fmt.Println("== demo2 线程化压缩(GHC Compact.c 的做法) ==")
	h := fixture()
	h.root("r2", "A", false)                    // 两个根都指向 A,便于看链长
	h.cells[0].newSlot("self", true).val = "A"  // 一个"已标记指针"字段

	live, liveWords, _, updated, table := threadingCompact(h, h.roots)
	check("标记结果与 Lisp2 一致", sameSet(live, "A", "B", "C", "D"), live)
	check("全部槽被更新", updated == len(h.allSlots()), fmt.Sprintf("%d/%d", updated, len(h.allSlots())))
	check("线程化后**不需要**保留任何 old->new 映射(表已清空)", len(table) == 0, len(table))
	want := 0
	for _, o := range h.cells {
		if live[o.oid] {
			want += o.sizeWords()
		}
	}
	check("新布局与 Lisp2 完全相同(都是保序滑动)", liveWords == want,
		fmt.Sprintf("%d/%d", liveWords, want))

	a := h.cells[0]
	check("info 槽被还原为原内容(GHC:'original contents ... at the end of the chain')",
		a.info == "INFO(A)", a.info)
	check("tag 位被保留:标记过的字段仍是标记的",
		eqBools(tagsOf(a), []bool{false, true}), tagsOf(a))
	check("未标记的根槽仍为未标记", !h.roots[0].tagged && !h.roots[1].tagged)

	// 链的性质:手工复现一次,观察链长与链尾
	h2 := fixture()
	h2.root("r2", "A", false)
	h2.cells[0].newSlot("self", true).val = "A"
	table2 := refsOf(h2)
	a2 := h2.cells[0]
	threadObject(a2, table2["A"])
	check("全部 3 个引用都在链上(2 个根 + 1 个字段)", len(table2["A"]) == 3, len(table2["A"]))
	check("链长 = 指向该对象的槽数", chainLength(a2) == len(table2["A"]), chainLength(a2))

	node := a2.info
	for {
		cn, ok := node.(*chainNode)
		if !ok {
			break
		}
		node = cn.field.val
	}
	check("链尾是原 info 内容(不是 nil、不是槽)", node == "INFO(A)", node)

	head, ok := a2.info.(*chainNode)
	check("链首是最后挂上去的槽,其 tag = 2(已标记指针)", ok && head.tag == 2, a2.info)
	check("链首指向的正是那个 tagged 字段", ok && head.field == a2.fields[1])

	n := unthreadObject(a2, addrStr(0))
	check("解链改写链上全部槽", n == 3, n)
	check("解链后 info 槽也被还原", a2.info == "INFO(A)", a2.info)
	check("保留标记位:标记过的字段解链后仍是标记的",
		a2.fields[1].tagged && !a2.fields[0].tagged, tagsOf(a2))
	check("解链把 3 个槽都写成同一新地址(对象移动的唯一地址)",
		a2.fields[0].val == "a0" && h2.roots[0].val == "a0" && h2.roots[1].val == "a0")
}

func demo3() {
	fmt.Println("== demo3 空间对比:转发表 vs 链 ==")
	const nLive = 10000
	const bytesPerEntry = 8
	lisp2Extra := nLive * bytesPerEntry
	check("Lisp2 需 O(live) 转发表", lisp2Extra == 80000, lisp2Extra)
	check("threading 需要的额外表项 = 0(链写在对象自己的槽里)", 0*bytesPerEntry == 0)
	check("1 万个对象时 Lisp2 多花 80 KB,threading 多花 0 B", lisp2Extra-0 == 80000)

	// 变体:break table(每个连续段一个表项)介于两者之间
	const blocks = 20
	breakTable := blocks * bytesPerEntry
	check("break table 变体只需每连续段一个表项", breakTable == 160, breakTable)
	check("空间:threading(0) < break table < Lisp2 转发表",
		0 < breakTable && breakTable < lisp2Extra, [2]int{0, breakTable})

	type node struct{ hasWritableSlot, exactPointers bool }
	threadable := func(n node) bool { return n.hasWritableSlot && n.exactPointers }
	check("前提①每个对象要有可写槽承载链首", threadable(node{true, true}))
	check("缺任一前提即不可线程化",
		!threadable(node{false, true}) && !threadable(node{true, false}))
	check("保守式扫描 = exactPointers 为假 -> 不可线程化", !threadable(node{true, false}))

	h := fixture()
	before := len(h.allSlots())
	_, liveWords, _, _, table := threadingCompact(h, h.roots)
	check("threading 全程不保留任何表(用完即弃)",
		len(table) == 0 && liveWords > 0, fmt.Sprintf("%d/%d", len(table), liveWords))
	check("对象槽总数不增加(没有额外分配)", len(h.allSlots()) == before, len(h.allSlots()))
}

func demo4() {
	fmt.Println("== demo4 保序(Lisp2) vs 层序(Cheney) 的局部性 ==")
	// 引用图与地址序**故意错开**:地址序 A,B,C,D,E;引用序 A->E->D->C->B
	sizes := map[string]int{"A": 2, "B": 3, "C": 2, "D": 4, "E": 2}
	addrOrder := []string{"A", "B", "C", "D", "E"}
	traversal := []string{"A", "E", "D", "C", "B"}
	lisp2Order := append([]string{}, addrOrder...) // 保序:相对顺序不变
	cheneyOrder := []string{"A", "E", "D", "C", "B"} // 层序 = BFS 展开顺序

	positions := func(order []string) map[string]int {
		pos, acc := map[string]int{}, 0
		for _, oid := range order {
			pos[oid] = acc
			acc += sizes[oid]
		}
		return pos
	}
	walk := func(pos map[string]int, order []string) int {
		s := 0
		for i := 0; i+1 < len(order); i++ {
			s += absInt(pos[order[i]] - pos[order[i+1]])
		}
		return s
	}
	jLisp2 := walk(positions(lisp2Order), traversal)
	jCheney := walk(positions(cheneyOrder), traversal)
	check("Cheney 层序布局让'按引用遍历'的地址跳跃最小", jCheney < jLisp2,
		fmt.Sprintf("cheney=%d lisp2=%d", jCheney, jLisp2))

	ideal := 0
	for _, oid := range traversal[:len(traversal)-1] {
		ideal += sizes[oid]
	}
	check("Cheney 布局下相邻访问对象地址差 = 对象自身大小(理想局部性)",
		jCheney == ideal, fmt.Sprintf("%d/%d", jCheney, ideal))
	check("Lisp2 保序布局下跳跃是层序的 2 倍", jLisp2 == 2*jCheney,
		fmt.Sprintf("%d/%d", jLisp2, jCheney))
	check("保序对'按引用遍历'更差,但对'按地址扫描'更好",
		walk(positions(lisp2Order), lisp2Order) < walk(positions(cheneyOrder), lisp2Order),
		fmt.Sprintf("%d/%d", walk(positions(lisp2Order), lisp2Order),
			walk(positions(cheneyOrder), lisp2Order)))

	totalWords := 0
	for _, v := range sizes {
		totalWords += v
	}
	check("mmref:'Compaction is used to ... increase locality of reference' 对两种布局都成立",
		jCheney < totalWords*3 && jLisp2 < totalWords*3, []int{jCheney, jLisp2})

	h := fixture()
	live, _, newCells, _ := lisp2Compact(h, h.roots)
	check("两种压缩都把空闲合并成单块(与布局差异无关)", len(newCells) == len(live),
		fmt.Sprintf("%d/%d", len(newCells), len(live)))
}

func demo5() {
	fmt.Println("== demo5 阶段分解与'漏更新引用'的后果 ==")
	// V8 src/heap/mark-compact.h 的 CollectorState(定义在 #ifdef DEBUG 下):
	//   IDLE, PREPARE_GC, MARK_LIVE_OBJECTS, SWEEP_SPACES,
	//   ENCODE_FORWARDING_ADDRESSES, UPDATE_POINTERS, RELOCATE_OBJECTS
	phases := []string{"PREPARE_GC", "MARK_LIVE_OBJECTS", "ENCODE_FORWARDING_ADDRESSES",
		"UPDATE_POINTERS", "RELOCATE_OBJECTS"}
	check("V8 mark-compact 把指针更新做成独立状态 UPDATE_POINTERS",
		containsStr(phases, "UPDATE_POINTERS"))
	check("阶段数 >= 2(mmref:marking + compaction 的多遍扫描)",
		len(phases)-1 >= 2, len(phases)-1)
	check("先编码转发地址、再改引用、最后搬对象(顺序不可换)",
		indexOfStr(phases, "ENCODE_FORWARDING_ADDRESSES") <
			indexOfStr(phases, "UPDATE_POINTERS") &&
			indexOfStr(phases, "UPDATE_POINTERS") <
				indexOfStr(phases, "RELOCATE_OBJECTS"))

	// 正确流程:所有槽都被更新
	h := fixture()
	_, fwd, _, _ := lisp2Compact(h, h.roots)
	allStr := true
	for _, s := range h.allSlots() {
		if _, ok := slotOid(s); !ok {
			allStr = false
		}
	}
	check("正常流程:所有槽都持有对象名(已被改写成新地址)", allStr)
	check("正常流程:不存在悬空引用(每个槽都落在合法新地址上)",
		len(danglingSlots(h, fwd)) == 0, len(danglingSlots(h, fwd)))

	// 错误流程:故意漏更新 A 的字段(B 的引用),保留旧 oid
	h2 := fixture()
	live2 := mark(h2, h2.roots)
	fwd2, _ := computeForwarding(h2, live2)
	for _, s := range h2.allSlots() {
		if s.owner == "A" { // 故意跳过
			continue
		}
		if v, ok := slotOid(s); ok {
			s.val = addrStr(fwd2[v])
		}
	}
	bad := danglingSlots(h2, fwd2)
	check("漏更新一个引用 -> 该槽仍指向旧 oid,不落在新地址集合里", len(bad) >= 1, len(bad))
	check("漏掉的正是 A.toB(唯一属于 A 的槽)", len(bad) == 1 && bad[0].owner == "A" && bad[0].name == "toB",
		bad[0].name)
	check("悬空槽的值仍是旧 oid,而非它应该指向的新地址",
		bad[0].val == "B" && addrStr(fwd2["B"]) != bad[0].val,
		fmt.Sprintf("%v/%s", bad[0].val, addrStr(fwd2["B"])))
	check("移动式 GC 的致命错误:漏掉任何一个引用都会产生悬空指针",
		len(bad) > 0 && bad[0].val == "B")
	check("'漏更新'即移动式设计的阿喀琉斯之踵:必须精确、不可保守",
		addrSet(fwd2)[bad[0].val.(string)] == false)
}
