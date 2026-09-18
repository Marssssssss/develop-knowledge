// Cheney 半空间复制式 GC —— 5 组实验(Go 版)。
//
// 算法在 cheney.go;本文件放实验与断言。运行: go run .
package main

import "fmt"

var (
	fails []string
	total int
)

func check(label string, cond bool, detail ...interface{}) {
	total++
	if cond {
		fmt.Println("  [ok]", label)
		return
	}
	fails = append(fails, label)
	fmt.Println("  [FAIL]", label, detail)
}

func demo1() {
	fmt.Println("== demo1 Cheney 复制全过程 ==")
	g := buildChainGraph()
	toSpace, forward, trace := cheney(g)

	check("只有可达对象被复制(5 个)", g.copied == 5, g.copied)
	check("共享的 D 被命中转发指针而非二次复制", g.forwardHits >= 1, g.forwardHits)
	check("转发表恰好覆盖 5 个存活对象", len(forward) == 5, len(forward))
	_, hasX := forward["X"]
	_, hasY := forward["Y"]
	_, hasZ := forward["Z"]
	check("垃圾对象 X/Y/Z 都不在转发表里", !hasX && !hasY && !hasZ)

	order := make([]string, 0, len(toSpace))
	for _, e := range toSpace {
		order = append(order, oldOf(forward, e.obj.oid))
	}
	want := []string{"A", "B", "C", "D", "E"}
	same := len(order) == len(want)
	for i := range want {
		if !same || order[i] != want[i] {
			same = false
			break
		}
	}
	check("复制顺序严格是 BFS 展开顺序", same, order)
	check("根 A 第一个被复制", order[0] == "A")
	check("链尾 E 最后一个被复制", order[len(order)-1] == "E")
	check("D 出现在 B、C 之后(层序而非深序)",
		indexOfStr(order, "D") > indexOfStr(order, "B") && indexOfStr(order, "D") > indexOfStr(order, "C"))

	contiguous := toSpace[0].off == 0
	for i := 0; i+1 < len(toSpace); i++ {
		if toSpace[i].off+toSpace[i].obj.words() != toSpace[i+1].off {
			contiguous = false
		}
	}
	check("to_space 中对象首尾相接、无空洞", contiguous)
	sum := 0
	for _, e := range toSpace {
		sum += e.obj.words()
	}
	check("alloc 最终值 = 存活对象总字数", g.allocWords == sum, g.allocWords, sum)

	hasCopy, hasFwd, hasScan := false, false, false
	scanCount := 0
	monotone := true
	for _, t := range trace {
		switch t.kind {
		case "copy":
			hasCopy = true
		case "forward":
			hasFwd = true
		case "scan":
			hasScan = true
			scanCount++
		}
		if t.next < t.scan {
			monotone = false
		}
	}
	check("三个阶段交错执行(与 V8 描述一致,非三段分离)", hasCopy && hasFwd && hasScan)
	check("每个被复制的对象恰好 scan 一次", scanCount == g.copied, scanCount)
	check("scan 指针始终不超过 alloc 指针", monotone)
	fwdAfterScan := true
	for _, t := range trace {
		if t.kind == "forward" && t.scan == 0 {
			fwdAfterScan = false
		}
	}
	check("转发指针命中发生在 scan 阶段(说明是'再次遇到')", fwdAfterScan)

	check("根槽被就地改写为转发地址", g.roots[0].val == forward["A"])
	aNew := findNew(toSpace, forward["A"])
	check("对象内部引用也指向新地址(精确根可枚举所有引用)", aNew.fields[0].val == forward["B"])
	allNew := true
	for _, e := range toSpace {
		if e.obj.oid == "A" || e.obj.oid == "B" {
			allNew = false
		}
	}
	check("所有新对象 oid 与旧对象不同(不是原地改)", allNew)
}

func findNew(toSpace []spaceEntry, oid string) *object {
	for _, e := range toSpace {
		if e.obj.oid == oid {
			return e.obj
		}
	}
	return nil
}

func indexOfStr(s []string, want string) int {
	for i, x := range s {
		if x == want {
			return i
		}
	}
	return -1
}

// freeBlocks 按 mark-sweep 的口径算空闲区间(相邻空闲合并)。
func freeBlocks(allWords []int, live map[int]bool) []int {
	var out []int
	cur := 0
	for i, w := range allWords {
		if live[i] {
			if cur > 0 {
				out = append(out, cur)
				cur = 0
			}
		} else {
			cur += w
		}
	}
	if cur > 0 {
		out = append(out, cur)
	}
	return out
}

func demo2() {
	fmt.Println("== demo2 紧凑化:碎片消除 ==")
	g := newChain()
	names := []string{"o0", "o1", "o2", "o3", "o4", "o5"}
	for _, n := range names {
		g.add(n, 1)
	}
	g.root("r", "o0")
	g.edge("o0", "next").val = "o2"
	g.edge("o2", "next").val = "o4"

	allWords := make([]int, len(names))
	heapWords := 0
	live := map[int]bool{0: true, 2: true, 4: true}
	liveWords := 0
	for i, n := range names {
		allWords[i] = g.objs[n].words()
		heapWords += allWords[i]
		if live[i] {
			liveWords += allWords[i]
		}
	}

	toSpace, _, _ := cheney(g)
	check("只复制了 3 个存活对象", g.copied == 3, g.copied)
	last := toSpace[len(toSpace)-1]
	check("to_space 占用恰为存活字数(零碎片)",
		last.off+last.obj.words() == liveWords, last.off+last.obj.words(), liveWords)
	contiguous := true
	for i := 0; i+1 < len(toSpace); i++ {
		if toSpace[i].off+toSpace[i].obj.words() != toSpace[i+1].off {
			contiguous = false
		}
	}
	check("存活对象在 to_space 中首尾相接", contiguous)

	gaps := freeBlocks(allWords, live)
	check("mark-sweep 留下 3 段互不相邻的空洞", len(gaps) == 3, gaps)
	total := 0
	maxGap := 0
	for _, x := range gaps {
		total += x
		if x > maxGap {
			maxGap = x
		}
	}
	check("空洞总字数 = 堆 - 存活", total == heapWords-liveWords, total, heapWords-liveWords)
	check("mark-sweep 的最大连续空闲 = 单个死亡对象(粒度最细)",
		maxGap == allWords[1] && allWords[1] == allWords[3] && allWords[3] == allWords[5],
		maxGap, allWords)
	check("Cheney 的最大连续空闲 = 整个堆尾,严格大于 mark-sweep 的最好情况",
		heapWords-liveWords > maxGap, heapWords-liveWords, maxGap)
	check("mark-sweep 碎片率 > 40%", float64(heapWords-liveWords)/float64(heapWords) > 0.4)
	check("Cheney 侧空闲恰好 1 段且等于全部空闲", len(gaps) == 3 && total == heapWords-liveWords)
}

