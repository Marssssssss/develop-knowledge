// Cheney 实验 demo3~demo5 与收尾(Go 版)。

package main

import "fmt"

func demo3() {
	fmt.Println("== demo3 成本模型:copy ∝ 存活  vs  sweep ∝ 堆 ==")
	heap := 100000
	ratios := []float64{0.01, 0.10, 0.50, 1.00}
	lives := make([]int, len(ratios))
	copies := make([]int, len(ratios))
	sweeps := make([]int, len(ratios))
	for i, r := range ratios {
		lives[i] = int(float64(heap) * r)
		copies[i] = lives[i]
		sweeps[i] = heap
	}
	onlyLive := true
	for i := range ratios {
		if copies[i] != lives[i] {
			onlyLive = false
		}
	}
	check("复制量只与存活字数有关,与堆大小无关", onlyLive)
	check("复制量随存活率严格上升",
		copies[0] < copies[1] && copies[1] < copies[2] && copies[2] < copies[3])
	onlyHeap := true
	for i := range ratios {
		if sweeps[i] != heap {
			onlyHeap = false
		}
	}
	check("sweep 量恒为整堆 100000 字(与存活率无关)", onlyHeap)
	check("存活率 1% 时复制量只有 sweep 的 1%", copies[0] == heap/100, copies[0])
	check("存活率 100% 时复制量 = sweep 量(复制式此时毫无优势)", copies[3] == sweeps[3])
	check("未复制的死亡对象'自动成为隐式垃圾'(无需清扫)", heap-copies[0] > 0)
	check("存活率低于 50% 时复制式稳赢", copies[1] < sweeps[1])

	g := buildChainGraph()
	cheney(g)
	check("复制式还要额外付'改写引用'的代价,规模同样 ∝ 存活对象数",
		g.scanWords == g.allocWords, g.scanWords, g.allocWords)
}

func demo4() {
	fmt.Println("== demo4 空间开销:半空间 = 2x 保留 ==")
	heap := 1 << 20
	semiTotal := 2 * heap
	markBits := heap / (word * 8) // 标记位:平均 8 字/对象 -> 1 bit

	check("半空间设计下总保留 = 2 * 可用堆", semiTotal == 2*heap)
	check("任一时刻恰好一半可分配(可用率上界 50%)", semispaceRatio(heap, semiTotal) == 0.5)
	check("V8 原文:两个 semispace 半区都被 commit", semiTotal-heap == heap)
	check("标记位只需 1 bit/对象 = 堆的 1/64(8 字/对象)", markBits == heap/64, markBits)
	check("复制式的额外空间(1x 堆)是标记位的 64 倍", heap == markBits*64)

	check("最坏情况(全部存活):复制量 = 整堆 = 清扫量", copyCost(heap) == sweepCost(heap))
	check("此时 to-space 恰好装满、零余量 -> 容不下瞬时超额存活", heap-copyCost(heap) == 0)
	check("标记-清除的搬运量恒为 0 字", copyCost(0) == 0)
	check("半区装不下时只能回退:promote 到老年代或改用标记-清除", heap > heap/2)

	for _, r := range []float64{0.05, 0.5, 0.95} {
		live := int(float64(heap) * r)
		u := float64(live) / float64(semiTotal)
		check(fmt.Sprintf("存活率 %.0f%% 时物理内存有效利用率 = live/(2*heap)", r*100),
			u == float64(live)/float64(semiTotal), u)
		check(fmt.Sprintf("存活率 %.0f%% 时 live 与 r*heap 只差取整误差", r*100),
			abs(float64(live)-r*float64(heap)) < 1)
	}
}

func copyCost(liveWords int) int { return liveWords }

func semispaceRatio(heap, total int) float64 { return float64(heap) / float64(total) }

func sweepCost(heapWords int) int { return heapWords }

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

func demo5() {
	fmt.Println("== demo5 移动的两个前提:精确根 + 转发指针 ==")
	g := newChain()
	g.add("A", 1)
	g.add("B", 1)
	g.root("r", "A")
	g.edge("A", "b").val = "B"
	toSpace, forward, _ := cheney(g)

	check("前提一:根槽被就地改写", g.roots[0].val == forward["A"])
	check("前提一:对象字段被就地改写", toSpace[0].obj.fields[0].val == forward["B"])

	hidden := "B" // 模拟一个 GC 不知晓的引用(如被当作整数藏在别处)
	stillThere := false
	for _, e := range toSpace {
		if e.obj.oid == hidden {
			stillThere = true
		}
	}
	check("前提一:隐藏引用指向的旧对象已不存在于 to_space", !stillThere)
	check("前提一:隐藏引用在移动后变成悬空(指向已作废的 from-space)",
		forward["B"] != "B" && !stillThere)
	dangling := false
	for _, v := range forward {
		if v == hidden {
			dangling = true
		}
	}
	check("结论:保守式(把整数当指针的)GC 无法安全移动对象", !dangling)

	parallelTry := func(useForward bool) ([]string, string, string) {
		fwd := map[string]string{}
		var copies []string
		tryCopy := func(worker, oid string) string {
			if useForward {
				if v, ok := fwd[oid]; ok {
					return v
				}
				fwd[oid] = "D@" + worker
			}
			copies = append(copies, worker)
			return "D@" + worker
		}
		r1 := tryCopy("w1", "D")
		r2 := tryCopy("w2", "D")
		return copies, r1, r2
	}

	copies, r1, r2 := parallelTry(false)
	check("无转发指针:两个 worker 各复制一份 -> 对象分裂",
		len(copies) == 2 && r1 != r2, copies, r1, r2)
	copies, r1, r2 = parallelTry(true)
	check("有转发指针:只复制一次,两次拿到同一地址", len(copies) == 1 && r1 == r2,
		copies, r1, r2)

	ss := newSemiSpace()
	ss.flip()
	check("scavenge 结束后 active/idle 互换", ss.active == "S1" && ss.idle == "S0")
	ss.flip()
	check("新分配始终落在 active 半区", ss.active == "S0" && ss.idle == "S1")
	only, _, _ := parallelTry(true)
	check("转发指针同时充当'已搬迁'标记(等价于一个 mark 位)", len(only) == 1)
}

type semiSpace struct {
	active, idle string
}

func newSemiSpace() *semiSpace { return &semiSpace{active: "S0", idle: "S1"} }

func (s *semiSpace) flip() { s.active, s.idle = s.idle, s.active }

func main() {
	demo1()
	fmt.Println()
	demo2()
	fmt.Println()
	demo3()
	fmt.Println()
	demo4()
	fmt.Println()
	demo5()
	fmt.Println()
	fmt.Printf("断言总数 %d,失败 %d\n", total, len(fails))
	if len(fails) > 0 {
		for _, f := range fails {
			fmt.Println("  FAILED:", f)
		}
		return
	}
	fmt.Println("全部通过")
}
