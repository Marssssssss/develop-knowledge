// -benchmem 自检:净值语义、整数截断、GC 摊销的非单调性、GCCPUFraction 的分母口径。
package main

import (
	"fmt"
	"os"
)

var failures int

func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s\n", label)
		return
	}
	fmt.Printf("  FAIL %s -- %s\n", label, detail)
	failures++
}

func main() {
	// 1) 净值是差值,计时器停着时的分配不算
	m := newMemStats()
	t := &Timer{m: m}
	t.Start()
	m.totalAlloc += 1000
	m.mallocs += 10
	t.Stop()
	m.totalAlloc += 5 // 计时器已停,这部分必须被忽略
	m.mallocs++
	t.Start()
	m.totalAlloc += 2000
	m.mallocs += 20
	t.Stop()
	check("净值是差值(停表期间的分配被忽略)",
		t.netBytes == 3000 && t.netAllocs == 30,
		fmt.Sprintf("bytes=%d allocs=%d", t.netBytes, t.netAllocs))

	// 2) TotalAlloc 单调不减;存活对象 = Mallocs - Frees
	m2 := newMemStats()
	g2 := newGC(m2, 100, 1000)
	m2.totalAlloc += 500
	m2.mallocs += 5
	g2.Free(0)
	g2.Free(0)
	check("TotalAlloc 释放后不减少", m2.totalAlloc == 500, fmt.Sprint(m2.totalAlloc))
	check("存活对象 = Mallocs-Frees", m2.HeapObjects() == 3, fmt.Sprint(m2.HeapObjects()))

	// 3) 整数截断:0 B/op 不等于零分配
	r := BenchmarkResult{n: 1_000_000, t: 2_000_000_000, memAllocs: 500_000, memBytes: 500_000}
	check("1e6 op 分配 500KB -> 0 B/op", r.memBytes > 0 && r.allocedBytesPerOp() == 0, "")
	check("1e6 op 分配 500K 次 -> 0 allocs/op", r.memAllocs > 0 && r.allocsPerOp() == 0, "")

	// 4) 总分配固定时,B/op 随 N 单调下降
	want := []int64{500_000, 5_000, 50, 0}
	ns := []int64{1, 100, 10_000, 1_000_000}
	ok4 := true
	for i, n := range ns {
		if got := BenchmarkResult{n: n, memBytes: 500_000}.allocedBytesPerOp(); got != want[i] {
			ok4 = false
		}
	}
	check("B/op 随 N 下降 500000/5000/50/0", ok4, "")

	// 5) GC 摊入 ns/op,且随 N 非单调
	s5 := newMemStats()
	g5 := newGC(s5, 100, 2_000_000)
	small := runN(&Timer{m: s5}, g5, 1_000, 100, 1024, 0.05)
	gc1 := s5.numGC
	big := runN(&Timer{m: s5}, g5, 100_000, 100, 1024, 0.05)
	gc2 := s5.numGC - gc1
	huge := runN(&Timer{m: s5}, g5, 1_000_000, 100, 1024, 0.05)
	gc3 := s5.numGC - gc1 - gc2
	check("小 N 因未触发 GC 而低估", small.nsPerOp() < big.nsPerOp(),
		fmt.Sprintf("%d vs %d", small.nsPerOp(), big.nsPerOp()))
	check("ns/op 随 N 非单调(small < huge < big)",
		small.nsPerOp() < huge.nsPerOp() && huge.nsPerOp() < big.nsPerOp(),
		fmt.Sprintf("%d/%d/%d", small.nsPerOp(), big.nsPerOp(), huge.nsPerOp()))

	// 6) 每轮 runN 前都 GC:NumGC 随 -count 增长
	s6 := newMemStats()
	g6 := newGC(s6, 100, 1000)
	before := s6.numGC
	for i := 0; i < 10; i++ {
		runN(&Timer{m: s6}, g6, 10, 100, 0, 0)
	}
	check("-count=10 -> NumGC 至少 +10", s6.numGC-before >= 10, fmt.Sprint(s6.numGC-before))

	// 7) GCCPUFraction 的分母是 GOMAXPROCS 的积分(文档原例)
	frac := gccpuFraction(1_000_000_000, 20_000_000_000)
	check("GOMAXPROCS=2 跑 10s -> 可用 CPU 20s,GC 1s -> 0.05",
		frac > 0.05-1e-12 && frac < 0.05+1e-12, fmt.Sprintf("%.6f", frac))

	// 8) ReportMetric 会覆盖内建列
	r8 := BenchmarkResult{n: 100, t: 1_000_000, memAllocs: 100, memBytes: 100_000,
		extra: map[string]float64{"B/op": 7.0, "allocs/op": 3.0}}
	check("Extra 优先:覆盖内建 B/op 与 allocs/op",
		r8.allocedBytesPerOp() == 7 && r8.allocsPerOp() == 3, "")

	// 9) 「把分配当逻辑测」判别
	old := BenchmarkResult{n: 1000, t: 1_000_000, memAllocs: 1000, memBytes: 8_000_000}
	nw := BenchmarkResult{n: 1000, t: 1_100_000, memAllocs: 1000, memBytes: 80_000_000}
	steady := BenchmarkResult{n: 1000, t: 1_050_000, memAllocs: 1000, memBytes: 8_400_000}
	check("B/op×10 而 ns/op×1.1 -> 判为测的是分配", measuredAllocationBoundary(old, nw), "")
	check("B/op×1.05 -> 不判", !measuredAllocationBoundary(old, steady), "")

	fmt.Printf("      GC 摊销:N=1e3 -> %d ns/op(+%d 次),N=1e5 -> %d(+%d),N=1e6 -> %d(+%d)\n",
		small.nsPerOp(), gc1, big.nsPerOp(), gc2, huge.nsPerOp(), gc3)
	fmt.Printf("      B/op 随 N:N=1 -> %d,N=1e6 -> %d(同为 500KB 总分配)\n",
		BenchmarkResult{n: 1, memBytes: 500_000}.allocedBytesPerOp(),
		BenchmarkResult{n: 1_000_000, memBytes: 500_000}.allocedBytesPerOp())

	if failures > 0 {
		fmt.Printf("\n%d 项失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\nallocstats(go): 全部自检通过")
}
