// -benchmem 的 B/op 与 allocs/op 的 Go 镜像实现。
//
// 与 Python 版逐结构对应。口径来自 golang/go src/testing/benchmark.go 的
// StartTimer/StopTimer/ResetTimer/runN,以及 pkg.go.dev/runtime 的 MemStats 字段文档。
// 全部确定性:GC 触发条件与摊销用离散模型算,不依赖真实计时。
package main

import (
	"fmt"
	"os"
)

// minNextGC Go 的最小目标堆(默认 4MB),保证 next_gc 不会退化到 0。
const minNextGC = 4 << 20

// MemStats runtime.MemStats 里与本 demo 相关的字段。
type MemStats struct {
	totalAlloc   int64 // 累计分配字节,释放也不减少
	mallocs      int64
	frees        int64
	heapAlloc    int64
	pauseTotalNS int64
	numGC        int
	nextGC       int64
}

// HeapObjects 存活对象数 = Mallocs - Frees。
func (m *MemStats) HeapObjects() int64 { return m.mallocs - m.frees }

func newMemStats() *MemStats { return &MemStats{nextGC: minNextGC} }

// GC 简化的垃圾收集器:堆达到 next_gc 触发一轮 STW,之后按 GOGC 重算目标堆。
type GC struct {
	m        *MemStats
	gogc     int
	pauseNS  int64
	gcCPUNS  int64
}

func newGC(m *MemStats, gogc int, pauseNS int64) *GC {
	return &GC{m: m, gogc: gogc, pauseNS: pauseNS}
}

// Allocate 分配 size 字节,返回本次触发的 STW 纳秒。
func (g *GC) Allocate(size int64) int64 {
	g.m.totalAlloc += size
	g.m.mallocs++
	g.m.heapAlloc += size
	if g.m.heapAlloc >= g.m.nextGC {
		return g.Collect(-1)
	}
	return 0
}

// Free 归还 size 字节(heapAlloc 下降,但 totalAlloc 不变)。
func (g *GC) Free(size int64) {
	g.m.frees++
	g.m.heapAlloc -= size
	if g.m.heapAlloc < 0 {
		g.m.heapAlloc = 0
	}
}

// Collect 一轮 GC。forcedLive<0 时存活量取当前 heapAlloc;runtime.GC() 走 forcedLive=0。
func (g *GC) Collect(forcedLive int64) int64 {
	live := g.m.heapAlloc
	if forcedLive >= 0 {
		live = forcedLive
	}
	g.m.heapAlloc = live
	g.m.numGC++
	g.m.pauseTotalNS += g.pauseNS
	g.gcCPUNS += g.pauseNS
	target := live * int64(100+g.gogc) / 100
	g.m.nextGC = maxI(minNextGC, target)
	return g.pauseNS
}

func maxI(a, b int64) int64 {
	if a > b {
		return a
	}
	return b
}

// Timer B 的计时/计数状态机:StartTimer / StopTimer / ResetTimer 的复刻。
type Timer struct {
	m           *MemStats
	timerOn     bool
	duration    int64
	netAllocs   int64
	netBytes    int64
	startAllocs int64
	startBytes  int64
}

// Start 对应 StartTimer:采样内存基线。
func (t *Timer) Start() {
	if !t.timerOn {
		t.startAllocs = t.m.mallocs
		t.startBytes = t.m.totalAlloc
		t.timerOn = true
	}
}

// Stop 对应 StopTimer:把增量累加到净值。
func (t *Timer) Stop() {
	if t.timerOn {
		t.netAllocs += t.m.mallocs - t.startAllocs
		t.netBytes += t.m.totalAlloc - t.startBytes
		t.timerOn = false
	}
}

// Reset 对应 ResetTimer:只在计时器开着时重采基线,净值一律清零。
func (t *Timer) Reset() {
	if t.timerOn {
		t.startAllocs = t.m.mallocs
		t.startBytes = t.m.totalAlloc
	}
	t.duration = 0
	t.netAllocs = 0
	t.netBytes = 0
}

// BenchmarkResult testing.BenchmarkResult 的三个取值方法(整数除法、Extra 优先)。
type BenchmarkResult struct {
	n         int64
	t         int64
	memAllocs int64
	memBytes  int64
	extra     map[string]float64
}

func (r BenchmarkResult) nsPerOp() int64 {
	if v, ok := r.extra["ns/op"]; ok {
		return int64(v)
	}
	if r.n <= 0 {
		return 0
	}
	return r.t / r.n
}

func (r BenchmarkResult) allocsPerOp() int64 {
	if v, ok := r.extra["allocs/op"]; ok {
		return int64(v)
	}
	if r.n <= 0 {
		return 0
	}
	return r.memAllocs / r.n
}

func (r BenchmarkResult) allocedBytesPerOp() int64 {
	if v, ok := r.extra["B/op"]; ok {
		return int64(v)
	}
	if r.n <= 0 {
		return 0
	}
	return r.memBytes / r.n
}

// gccpuFraction 分子是 GC 占用的 CPU 时间,分母是 GOMAXPROCS 的积分(可用 CPU)。
func gccpuFraction(gcCPUNS, gomaxprocsIntegralNS int64) float64 {
	if gomaxprocsIntegralNS <= 0 {
		return 0
	}
	return float64(gcCPUNS) / float64(gomaxprocsIntegralNS)
}

// runN 复刻 runN:先 runtime.GC(),再 Reset/Start/循环/Stop。
func runN(t *Timer, g *GC, n, opNS, allocPerOp int64, liveFraction float64) BenchmarkResult {
	g.Collect(0) // runtime.GC():不计入计时
	t.Reset()
	t.Start()
	dead := allocPerOp - int64(float64(allocPerOp)*liveFraction)
	for i := int64(0); i < n; i++ {
		t.duration += opNS
		if allocPerOp > 0 {
			t.duration += g.Allocate(allocPerOp)
			if dead > 0 {
				g.Free(dead)
			}
		}
	}
	t.Stop()
	return BenchmarkResult{n: n, t: t.duration, memAllocs: t.netAllocs, memBytes: t.netBytes}
}

// measuredAllocationBoundary 「把分配当逻辑测」判别:B/op 变化远大于 ns/op 变化。
func measuredAllocationBoundary(old, nw BenchmarkResult) bool {
	if old.allocedBytesPerOp() == 0 || old.nsPerOp() == 0 {
		return false
	}
	rB := float64(nw.allocedBytesPerOp()) / float64(old.allocedBytesPerOp())
	rT := float64(nw.nsPerOp()) / float64(old.nsPerOp())
	return rB >= 3.0 && rT < 1.5
}

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
	m.totalAlloc += 5
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
		got := BenchmarkResult{n: n, memBytes: 500_000}.allocedBytesPerOp()
		if got != want[i] {
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

	// 6) 每轮 runN 前都 GC
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

	fmt.Printf("      GC 摊销:N=1e3 -> %d ns/op(+%d),N=1e5 -> %d(+%d),N=1e6 -> %d(+%d)\n",
		small.nsPerOp(), gc1, big.nsPerOp(), gc2, huge.nsPerOp(), gc3)
	fmt.Printf("      B/op 随 N:1 -> %d,1e6 -> %d\n",
		BenchmarkResult{n: 1, memBytes: 500_000}.allocedBytesPerOp(),
		BenchmarkResult{n: 1_000_000, memBytes: 500_000}.allocedBytesPerOp())

	if failures > 0 {
		fmt.Printf("\n%d 项失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\nallocstats(go): 全部自检通过")
}
