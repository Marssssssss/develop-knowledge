// 分代式 GC 的 demo1~demo3:minor GC 流程 / 写屏障 / 晋升阈值(Go 版)。
package main

import "fmt"

func demo1() {
	fmt.Println("== demo1 一次 minor GC 的完整流程 ==")
	h := defaultHeap()
	a := h.alloc("A", 2)
	b := h.alloc("B", 2)
	h.roots = append(h.roots, a)
	h.store(a, "toB", b) // young -> young 不需要写屏障
	var temps []*obj
	for i := 0; i < 4; i++ {
		temps = append(temps, h.alloc(fmt.Sprintf("t%d", i), 1))
	}
	check("分配都落在 eden", len(h.eden) == 6, len(h.eden))
	check("写屏障只记 老->新,故此时没有脏卡", len(h.cards) == 0, len(h.cards))

	h.minor()
	check("minor GC 会计次", h.minorGCs == 1)
	check("eden 被整块清空", len(h.eden) == 0, len(h.eden))
	check("from-survivor 也清空", len(h.surv[1-h.cur]) == 0, len(h.surv[1-h.cur]))
	check("存活者 A/B 被复制进 survivor",
		eqStrs(oidsOf(h.surv[h.cur]), []string{"A", "B"}), describeObjs(h.surv[h.cur]))
	check("复制后年龄 +1", a.age == 1 && b.age == 1, fmt.Sprintf("%d/%d", a.age, b.age))
	check("未达晋升阈值,仍属新生代", a.gen == 0 && b.gen == 0)
	res := h.resident()
	gone := true
	for _, t := range temps {
		if res[t] {
			gone = false
		}
	}
	check("垃圾 t0..t3 被回收(不在任何空间里)", gone)
	check("成本代理量 = 被扫描的新生代字数(2+2+4)", h.tracedWords == 8, h.tracedWords)

	h.minor()
	check("第二次 minor 后年龄到 2,达到 MaxTenuringThreshold",
		a.age == 2 && b.age == 2, fmt.Sprintf("%d/%d", a.age, b.age))
	check("达到阈值 -> 晋升到老年代",
		eqStrs(oidsOf(h.old), []string{"A", "B"}), describeObjs(h.old))
	check("晋升后 gen 置为 1", a.gen == 1 && b.gen == 1)
	check("survivor 重新变空(对象都走了)", len(h.surv[h.cur]) == 0, len(h.surv[h.cur]))
	check("晋升计数 = 2", h.promoted == 2, h.promoted)
	check("survivor 空间在两个半区之间轮换", h.cur == 0, h.cur)

	// 第三次:老年代对象再指向新生代 -> 这一次必须靠写屏障
	c := h.alloc("C", 1)
	h.store(a, "toC", c)
	check("老->新 引用被写屏障记进脏卡", h.cards["A.toC"], len(h.cards))
	check("记忆集里出现该槽", len(h.remember) == 1, len(h.remember))
	h.minor()
	check("被记忆集保护的 C 存活", c.age == 1 && h.resident()[c], c.age)
	check("本轮没有悬空引用", len(h.dangling()) == 0, describeDangling(h.dangling()))
}

func demo2() {
	fmt.Println("== demo2 写屏障/记忆集:分代 GC 的正确性前提 ==")

	hw := defaultHeap()
	forceOld(hw, hw.alloc("O", 1)) // O 已在老年代
	hw.roots = append(hw.roots, hw.old[0])
	cw := hw.alloc("C", 1)
	hw.store(hw.old[0], "child", cw)
	hw.minor()

	hn := newHeap(2, false, true, youngCapacity, oldCapacity)
	forceOld(hn, hn.alloc("O", 1))
	hn.roots = append(hn.roots, hn.old[0])
	cn := hn.alloc("C", 1)
	hn.rawStore(hn.old[0], "child", cn) // 无屏障写
	hn.minor()

	check("有屏障:老->新 引用进脏卡/记忆集",
		len(hw.cards) == 1 && len(hw.remember) == 1, len(hw.cards))
	check("无屏障:脏卡与记忆集都是空的",
		len(hn.cards) == 0 && len(hn.remember) == 0, len(hn.cards))
	check("有屏障:C 作为额外根被复制,存活", hw.resident()[cw] && cw.age == 1, cw.age)
	check("无屏障:C 不是根,被当成垃圾回收掉", !hn.resident()[cn])
	check("无屏障时老年代字段仍指向它 -> 悬空引用",
		len(hn.dangling()) == 1, describeDangling(hn.dangling()))
	check("悬空三元组 = (owner=O, field=child, target=C)",
		len(hn.dangling()) == 1 && hn.dangling()[0] == danglingRef{"O", "child", "C"},
		describeDangling(hn.dangling()))
	check("有屏障时悬空集合为空(这正是写屏障存在的理由)", len(hw.dangling()) == 0)

	// 对照:全堆收集**不需要**记忆集
	h3 := newHeap(2, false, true, youngCapacity, oldCapacity)
	o3 := forceOld(h3, h3.alloc("O", 1))
	h3.roots = append(h3.roots, o3) // 老年代对象本身是根
	c3 := h3.alloc("C", 1)
	h3.rawStore(o3, "child", c3)
	h3.major()
	check("major 不需要记忆集(majorGCs 计次)",
		h3.majorGCs == 1 && len(h3.remember) == 0, len(h3.remember))
	check("major 扫全堆,故 C 无需脏卡也能存活", h3.resident()[c3])
	check("major 后无悬空", len(h3.dangling()) == 0, describeDangling(h3.dangling()))
}

func demo3() {
	fmt.Println("== demo3 晋升阈值(MaxTenuringThreshold)的取舍 ==")
	copies := map[int]int{}
	oldW := map[int]int{}
	for _, th := range []int{1, 2, 3} {
		h := newHeap(th, true, true, youngCapacity, oldCapacity)
		l := h.alloc("L", 3)
		h.roots = append(h.roots, l)
		n := 0
		for l.gen == 0 && n < 5 {
			h.minor()
			n++
		}
		copies[th] = n
		oldW[th] = h.oldWords()
	}
	check("阈值=1:第一次 minor 直接晋升", copies[1] == 1, copies[1])
	check("阈值=2:第二次 minor 才晋升", copies[2] == 2, copies[2])
	check("阈值=3:第三次 minor 才晋升", copies[3] == 3, copies[3])
	check("晋升前的复制次数 == 阈值(阈值越高,新生代内搬运越多)",
		copies[1] < copies[2] && copies[2] < copies[3],
		fmt.Sprintf("%d/%d/%d", copies[1], copies[2], copies[3]))
	check("阈值越低,老年代增长越快", oldW[1] >= oldW[3], fmt.Sprintf("%d/%d", oldW[1], oldW[3]))
	check("本模型里三个阈值最终都把它送进老年代",
		oldW[1] >= 3 && oldW[2] >= 3 && oldW[3] >= 3)

	// 阈值的另一面:survivor 峰值占用
	peak := map[int]int{}
	for _, th := range []int{2, 3} {
		h := newHeap(th, true, true, youngCapacity, oldCapacity)
		l := h.alloc("L", 2)
		h.roots = append(h.roots, l)
		p := 0
		for l.gen == 0 {
			h.minor()
			w := 0
			for _, o := range h.surv[h.cur] {
				w += o.words
			}
			if w > p {
				p = w
			}
		}
		peak[th] = p
	}
	check("阈值越高,survivor 峰值占用越大(存活者被扣留得更久)",
		peak[2] <= peak[3], fmt.Sprintf("%d/%d", peak[2], peak[3]))
	check("模型的默认晋升阈值 == tenuringThreshold",
		defaultHeap().tenuring == tenuringThreshold && tenuringThreshold == 2)
}
