// W-TinyLFU 自检（与 wtinylfu.go 同一 main 包）。
package main

import "fmt"

// ------------------------------------------------------------ 自检

var fails []string

func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s\n", label)
		return
	}
	fails = append(fails, label+" "+detail)
	fmt.Printf("  FAIL %s %s\n", label, detail)
}

func main() {
	fmt.Println("[1] 4-bit Count-Min Sketch")
	sk := NewFrequencySketch(100)
	check("table 长度 = ceiling_pow2", sk.nLongs == 128, fmt.Sprint(sk.nLongs))
	check("8 字节/条目", sk.memoryBytes() == 8*sk.nLongs, fmt.Sprint(sk.memoryBytes()))
	for i := 0; i < 20; i++ {
		sk.Increment("a")
	}
	check("20 次递增饱和在 15", sk.Frequency("a") == counterMax, fmt.Sprint(sk.Frequency("a")))
	check("未见过的 key 频率为 0", sk.Frequency("zzz") == 0, "")

	fmt.Println("[2] 估计量 = 最小值")
	sk2 := NewFrequencySketch(64)
	idx := sk2.indices("k")
	vals := []byte{7, 3, 9, 5}
	for i := range idx {
		sk2.table[idx[i]] = vals[i]
	}
	check("min(7,3,9,5) = 3", sk2.Frequency("k") == 3, fmt.Sprint(sk2.Frequency("k")))
	sk2.Reset()
	check("老化减半后 = 1", sk2.Frequency("k") == 1, fmt.Sprint(sk2.Frequency("k")))

	fmt.Println("[3] 容量划分：1% 窗口 + 主区 SLRU")
	w := NewWTinyLFU(100, 0.01, false)
	for r := 0; r < 5; r++ {
		for i := 0; i < 100; i++ {
			w.Access(fmt.Sprintf("hot%d", i))
		}
	}
	check("总量填满", w.size() == 100, fmt.Sprint(w.size()))
	check("窗口 1 条", w.win.len() == 1, fmt.Sprint(w.win.len()))
	check("protected 79 条", w.prot.len() == 79, fmt.Sprint(w.prot.len()))
	check("probation 20 条", w.prob.len() == 20, fmt.Sprint(w.prob.len()))

	fmt.Println("[4] 扫描污染：LRU 被冲垮，W-TinyLFU 扛住")
	var trace []string
	for r := 0; r < 5; r++ {
		for i := 0; i < 100; i++ {
			trace = append(trace, fmt.Sprintf("hot%d", i))
		}
	}
	for i := 0; i < 500; i++ {
		trace = append(trace, fmt.Sprintf("scan%d", i))
	}
	lru := NewLRU(100)
	wt := NewWTinyLFU(100, 0.01, false)
	for _, k := range trace {
		lru.Access(k)
		wt.Access(k)
	}
	adm, rej := wt.admitted, wt.rejected // 必须在探测之前快照
	lh, wh := 0, 0
	for i := 0; i < 100; i++ {
		k := fmt.Sprintf("hot%d", i)
		if lru.Access(k) {
			lh++
		}
		if wt.Access(k) {
			wh++
		}
	}
	check("LRU 热集命中 0/100", lh == 0, fmt.Sprint(lh))
	check("W-TinyLFU 热集命中 94/100", wh == 94, fmt.Sprint(wh))
	check("495 个扫描项被拒绝", rej == 495, fmt.Sprint(rej))
	check("5 个因碰撞混入", adm == 5, fmt.Sprint(adm))

	fmt.Println("[5] 窗口接住 recency burst")
	burst := func(pct float64, gap int) bool {
		c := NewWTinyLFU(100, pct, false)
		for r := 0; r < 10; r++ {
			for i := 0; i < 100; i++ {
				c.Access(fmt.Sprintf("f%d", i))
			}
		}
		c.Access("X")
		for i := 0; i < gap; i++ {
			c.Access(fmt.Sprintf("gap%d", i))
		}
		return c.Access("X")
	}
	check("无窗口：必 miss", burst(0.0, 3) == false, "")
	check("窗口 5%，间隔 3 -> hit", burst(0.05, 3) == true, "")
	check("窗口 5%，间隔 30 -> miss", burst(0.05, 30) == false, "")
	check("窗口 20%，间隔 3 -> hit", burst(0.20, 3) == true, "")

	fmt.Println("[6] 爬山自适应")
	hc := NewWTinyLFU(100, 0.10, true)
	before := hc.windowPct
	hc.HillClimb(0.9, 0.2)
	check("窗口命中率高 -> 扩大", hc.windowPct > before, fmt.Sprint(before, hc.windowPct))
	mid := hc.windowPct
	hc.HillClimb(0.1, 0.9)
	check("主区命中率高 -> 收缩", hc.windowPct < mid, fmt.Sprint(mid, hc.windowPct))

	fmt.Println()
	if len(fails) > 0 {
		fmt.Printf("FAILED %d\n", len(fails))
		return
	}
	fmt.Println("ALL PASS")
}
