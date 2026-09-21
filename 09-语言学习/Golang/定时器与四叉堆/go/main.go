package main

import "fmt"

var checks, failed int

func check(cond bool, label string) {
	checks++
	if !cond {
		failed++
		fmt.Println("FAIL:", label)
	}
}

func checkEq(got, want interface{}, label string) {
	checks++
	if fmt.Sprint(got) != fmt.Sprint(want) {
		failed++
		fmt.Printf("FAIL: %s (got=%v want=%v)\n", label, got, want)
	}
}

func addAll(ts *Timers, whens []int64) []*Timer {
	var out []*Timer
	for i, w := range whens {
		t := &Timer{When: w, Name: fmt.Sprintf("t%d", i)}
		out = append(out, t)
		ts.AddHeap(t)
	}
	return out
}

func heapOK(ts *Timers) bool {
	for i := 1; i < len(ts.Heap); i++ {
		p := (i - 1) / timerHeapN
		if ts.Heap[p].When > ts.Heap[i].When {
			return false
		}
	}
	return true
}

func main() {
	// 1) 四叉堆拓扑：parent(i) = (i-1)/4
	ts := &Timers{}
	addAll(ts, []int64{50, 10, 90, 30, 70, 20, 80, 40})
	fmt.Println("heap:", func() []int64 {
		var xs []int64
		for _, tw := range ts.Heap {
			xs = append(xs, tw.When)
		}
		return xs
	}())
	checkEq(len(ts.Heap), 8, "8 个定时器入堆")
	checkEq(ts.Heap[0].When, int64(10), "堆顶是最小的 10")
	check(heapOK(ts), "堆序成立")
	for i := 1; i < len(ts.Heap); i++ {
		checkEq(ts.Heap[(i-1)/timerHeapN].When <= ts.Heap[i].When, true,
			fmt.Sprintf("heap[%d] 不小于其父", i))
	}

	// 2) Stop 只打标记
	head := ts.Heap[0].Timer
	checkEq(head.Stop(), true, "未触发时 Stop 返回 true")
	checkEq(len(ts.Heap), 8, "Stop 不从堆里摘除")
	checkEq(ts.Zombies, int32(1), "记 1 个 zombie")
	check(head.state&timerZombie != 0 && head.state&timerModified != 0, "Zombie+Modified 同时置位")
	checkEq(head.Stop(), false, "重复 Stop 返回 false")
	ts.CleanHead()
	checkEq(len(ts.Heap), 7, "cleanHead 摘掉堆顶 zombie")

	// 3) Reset 改晚：快照延后同步
	ts2 := &Timers{}
	rs := addAll(ts2, []int64{10, 20, 30})
	pending, _ := rs[0].Modify(25, 0)
	checkEq(pending, true, "Reset 的 pending 取自旧 when")
	checkEq(ts2.Heap[0].When, int64(10), "modify 不碰堆快照")
	ts2.UpdateHeap(rs[0])
	checkEq(ts2.Heap[0].When, int64(20), "updateHeap 后堆顶是 20")
	checkEq(ts2.Heap[1].When, int64(25), "被改晚的定时器下沉到下标 1")

	// 4) ticker 跳过 missed tick
	ts3 := &Timers{}
	tick := &Timer{When: 10, Period: 5, Name: "tick"}
	ts3.AddHeap(tick)
	checkEq(ts3.Run(9), int64(10), "now=9 返回下次时刻 10")
	checkEq(ts3.Run(27), int64(0), "now=27 触发")
	checkEq(tick.When, int64(30), "next = 10+5*(1+17/5) = 30")
	checkEq(ts3.Run(30), int64(0), "now=30 再触发")
	checkEq(tick.When, int64(35), "未延误时 next = 35")
	checkEq(len(ts3.Fired), 2, "共触发 2 次")

	// 5) 一次性定时器触发后被摘除（zombie 只是瞬时状态）
	ts4 := &Timers{}
	os := addAll(ts4, []int64{10, 20})
	checkEq(ts4.Run(10), int64(0), "一次性定时器触发")
	checkEq(ts4.ZombiePeak, int32(1), "触发瞬间曾置 zombie")
	checkEq(ts4.Zombies, int32(0), "updateHeap 又减回 0")
	check(os[0].ts == nil, "该定时器已离开堆")
	checkEq(len(ts4.Heap), 1, "堆里只剩一个")

	fmt.Printf("checks=%d failed=%d\n", checks, failed)
	if failed > 0 {
		panic("selfcheck failed")
	}
}
