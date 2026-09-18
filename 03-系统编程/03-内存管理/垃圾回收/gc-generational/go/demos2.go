// 分代式 GC 的 demo4~demo5:成本对比 / CPython 三代判定(Go 版)。
package main

import "fmt"

func demo4() {
	fmt.Println("== demo4 minor vs major 的成本(实测被扫描字数) ==")
	const (
		youngCap  = 12   // 两边共用同一套堆预算
		oldCap    = 2400 //
		longObjs  = 500  // 长期存活集 = 500 × 4 = 2000 字
		longWords = 4
		churnN    = 4000 // 之后产生的临时对象(1 字/个)
	)

	makeHeap := func(generational bool) *genHeap {
		return newHeap(2, true, generational, youngCap, oldCap)
	}
	seed := func(h *genHeap) {
		for i := 0; i < longObjs; i++ {
			h.roots = append(h.roots, h.alloc(fmt.Sprintf("L%d", i), longWords))
			h.step()
		}
	}
	churn := func(h *genHeap) int {
		t0 := h.tracedWords
		for i := 0; i < churnN; i++ {
			h.alloc(fmt.Sprintf("c%d", i), 1)
			h.step()
		}
		return h.tracedWords - t0
	}

	gen, nogen := makeHeap(true), makeHeap(false)
	seed(gen)
	seed(nogen)
	check("长期存活集建好后绝大部分已晋升进老年代", gen.oldWords() >= 1900, gen.oldWords())
	check("字数守恒:极少数尾部对象还留在新生代等下一轮晋升",
		gen.oldWords()+gen.youngWords() == longObjs*longWords,
		fmt.Sprintf("%d/%d", gen.oldWords(), gen.youngWords()))
	check("非分代基线没有老年代,存活集就堆在堆里",
		nogen.oldWords() == 0 && nogen.youngWords() == longObjs*longWords,
		fmt.Sprintf("%d/%d", nogen.oldWords(), nogen.youngWords()))

	seedGen, seedNogen := gen.tracedWords, nogen.tracedWords
	minorBefore := gen.minorGCs
	genCost, nogenCost := churn(gen), churn(nogen)
	churnMinors := gen.minorGCs - minorBefore

	check("两边都真的跑了收集", genCost > 0 && nogenCost > 0,
		fmt.Sprintf("%d/%d", genCost, nogenCost))
	check("分代:churn 期间一次全量收集都没有,全是 minor",
		gen.majorGCs == 0 && churnMinors > churnN/youngCap-20,
		fmt.Sprintf("%d/%d", gen.majorGCs, churnMinors))
	check("非分代基线:只做全量收集,次数 ≥ 8",
		nogen.minorGCs == 0 && nogen.majorGCs >= 8,
		fmt.Sprintf("%d/%d", nogen.minorGCs, nogen.majorGCs))
	check("基线每次全量收集都必须扫过 2000 字的存活集",
		nogenCost > 2000*nogen.majorGCs, fmt.Sprintf("%d/%d", nogenCost, nogen.majorGCs))
	check("分代每次 minor 的扫描量 ≈ 新生代容量",
		8*churnMinors <= genCost && genCost <= 16*churnMinors,
		fmt.Sprintf("%d/%d", genCost, churnMinors))
	ratio := float64(nogenCost) / float64(genCost)
	check("基线 / 分代 的扫描量比 > 4", ratio > 4, fmt.Sprintf("%.2f", ratio))
	fmt.Printf("     [info] churn 期扫描量:分代 %d 字 vs 基线 %d 字(%.1fx);预热期:分代 %d 字 vs 基线 %d 字\n",
		genCost, nogenCost, ratio, seedGen, seedNogen)
	check("预热期分代并不便宜(晋升要复制,这段成本被排除在对比之外)", seedGen > 0, seedGen)

	// minor 的成本只与新生代有关,**与老年代无关**
	h := makeHeap(true)
	h.roots = append(h.roots, forceOld(h, h.alloc("O", 8)))
	for i := 0; i < 3; i++ {
		forceOld(h, h.alloc(fmt.Sprintf("P%d", i), 8))
	}
	check("老年代已有 4 个对象(32 字)", h.oldWords() == 32, h.oldWords())
	h.alloc("y", 1)
	t0 := h.tracedWords
	h.minor()
	minorCost := h.tracedWords - t0
	t1 := h.tracedWords
	h.major()
	majorCost := h.tracedWords - t1
	check("minor 只扫新生代,老年代再大也不影响它", minorCost == 1, minorCost)
	check("major 必须扫全堆(老年代 32 字也逃不掉)", majorCost == 32, majorCost)
	check("同一堆上 major 的扫描量是 minor 的 32 倍", majorCost == 32*minorCost,
		fmt.Sprintf("%d/%d", majorCost, minorCost))
}

func demo5() {
	fmt.Println("== demo5 CPython 三代 GC 的判定逻辑对照 ==")
	s := newGenState(5, 2, 2)
	for i := 0; i < 5; i++ {
		s.recordAlloc(1)
	}
	check("count == threshold0 时还不触发(条件是严格大于)", !s.enabled(), s.counts[0])
	s.recordAlloc(1)
	check("超过 threshold0 才触发", s.enabled(), s.counts[0])
	s.recordDealloc(3)
	check("释放会抵消分配", s.counts[0] == 3, s.counts[0])
	s.recordDealloc(99)
	check("count 不会变负(CPython: if (count > 0) count--)", s.counts[0] == 0, s.counts[0])

	for i := 0; i < 3; i++ {
		s.recordAlloc(6)
		s.collect(0)
	}
	check("每收一次 gen0,gen1 的 count +1", s.counts[1] == 3, s.counts[1])
	s.recordAlloc(6)
	check("gen0 被检查次数 > threshold1 后,才会收到 gen1",
		s.selectGeneration() == 1, s.counts)

	// 25% long_lived 启发式:全量收集被推迟
	s2 := newGenState(5, 2, 2)
	s2.counts = [numGenerations]int{5, 2, 3} // 只有最老一代超阈值,才轮得到这条启发式
	s2.longLivedTotal, s2.longLivedPending = 100, 24
	check("pending(24) < total/4(25) -> 连最老一代也跳过,本次不收集",
		s2.selectGeneration() == -1, s2.selectGeneration())
	s2.longLivedPending = 25
	check("pending 到达 total/4 才允许收最老一代",
		s2.selectGeneration() == 2, s2.selectGeneration())
	s2.longLivedPending = 0 // 先把启发式挪开,再看中间代
	s2.counts[1] = 3
	check("中间代超阈值时不受该启发式限制,退而收 gen1",
		s2.selectGeneration() == 1, s2.selectGeneration())

	// collect() 的返回值语义 + 晋升 + garbage
	s3 := newGenState(2000, 10, 10)
	s3.track("dead1", false, false)
	s3.track("dead2", false, false)
	s3.track("alive", true, false)
	s3.track("finalized", false, true)
	rv := s3.collect(0)
	check("gc.collect() 返回值 = collected + uncollectable", rv == 2+0+1, rv)
	check("不可回收对象被移到 garbage 列表",
		len(s3.garbage) == 1 && s3.garbage[0].oid == "finalized")
	check("存活对象晋升到第 1 代",
		len(s3.objs[1]) == 1 && s3.objs[1][0].oid == "alive")
	check("被收集的代清空", len(s3.objs[0]) == 0, len(s3.objs[0]))
	check("stats 记账 = collections/collected/uncollectable",
		s3.stats[0] == genStats{1, 2, 1}, s3.stats[0])
	check("代数是三代", numGenerations == 3)

	// 最老代的两种行为:留在原地 / 刚升入最老代要记 long_lived_pending
	s4 := newGenState(2000, 10, 10)
	o4 := s4.track("long", true, false)
	s4.collect(2) // 一次全量收集
	check("收最老代时 long_lived_total 被重置为该代大小",
		s4.longLivedTotal == 1 && s4.longLivedPending == 0,
		fmt.Sprintf("%d/%d", s4.longLivedTotal, s4.longLivedPending))
	s4.collect(2)
	check("已是最老代的对象留在原地,不再晋升", o4.gen == 2, o4.gen)
	check("全量收集后 counts[2] 也被清零",
		s4.counts == [numGenerations]int{0, 0, 0}, s4.counts)

	s5 := newGenState(2000, 10, 10)
	o5 := s5.track("mid", true, false)
	s5.collect(0)
	check("gen0 收集后存活对象晋升到 gen1", o5.gen == 1, o5.gen)
	pendingBefore := s5.longLivedPending
	s5.collect(1)
	check("收 gen1 时把升入最老代的存活数计入 long_lived_pending",
		o5.gen == 2 && s5.longLivedPending == pendingBefore+1,
		fmt.Sprintf("%d/%d", o5.gen, s5.longLivedPending))

	fmt.Println("     [info] 真实解释器的 gc.get_threshold()/get_stats() 实测断言在 Python 版里(Go 无对应物)")
}
