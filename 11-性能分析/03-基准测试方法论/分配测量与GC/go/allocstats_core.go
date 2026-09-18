// -benchmem 的 B/op 与 allocs/op:核心模型(Go 镜像)。
//
// 与 Python 版逐结构对应。口径来自 golang/go src/testing/benchmark.go 的
// StartTimer/StopTimer/ResetTimer/runN,以及 pkg.go.dev/runtime 的 MemStats 字段文档。
// 全部确定性:GC 触发条件与摊销用离散模型算,不依赖真实计时。
package main

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
	m       *MemStats
	gogc    int
	pauseNS int64
	gcCPUNS int64
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
	g.Collect(0) // runtime.GC():清空上一轮垃圾,不计入计时
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
