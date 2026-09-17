// Go benchmark 分配统计复刻：b.N 递增、ReportAllocs、B/op 与 allocs/op、SetBytes 的 MB/s。
//
// 口径（pkg.go.dev/testing，本轮实读）：
//   - b.N：多次调用基准函数并调整 b.N，直到持续足够久、可可靠计时（setup 可能执行多次）
//   - ReportAllocs：等价 -test.benchmem，但只影响调用它的那个基准函数
//   - ResetTimer：清零时间与分配计数器；不改变计时器运行状态
//   - B/op = MemBytes/N、allocs/op = MemAllocs/N（整数除法）
//   - SetBytes：单次操作处理的字节数 → 输出 ns/op 与 MB/s（吞吐量，与 B/op 无关）
//   - RunParallel：ns/op 是整体墙钟时间，非各 goroutine 之和
//
// 注意：b.N 递增策略为简化模型（next = max(n+1, min(100n, target/per_op))），
// 只忠实复刻"多次调整直到够久"的语义。
package main

import (
	"fmt"
	"strings"
)

// B 是 testing.B 的最小复刻：计时 + 分配计数（只在计时开启期间累计）。
type B struct {
	N             int
	timerOn       bool
	elapsed       float64 // 秒（模拟时钟）
	memAllocs     int64
	memBytes      int64
	bytesPerOp    int64 // SetBytes
	reportAllocsOn bool
}

func (b *B) StartTimer()               { b.timerOn = true }
func (b *B) StopTimer()                { b.timerOn = false }
func (b *B) ReportAllocs()             { b.reportAllocsOn = true }
func (b *B) SetBytes(n int64)          { b.bytesPerOp = n }

// ResetTimer 清零时间与分配计数器；不改变 timerOn（官方原文）。
func (b *B) ResetTimer() {
	b.elapsed = 0
	b.memAllocs = 0
	b.memBytes = 0
}

// Op 模拟一次迭代：耗时 dt 秒、分配 nbytes 字节、nallocs 次 malloc。
// 只有计时开启时才入账（StopTimer 期间的动作不计）。
func (b *B) Op(dt float64, nbytes, nallocs int64) {
	if b.timerOn {
		b.elapsed += dt
		b.memBytes += nbytes
		b.memAllocs += nallocs
	}
}

// Result 是 testing.BenchmarkResult 的最小复刻。
type Result struct {
	N         int
	T         float64
	Bytes     int64
	MemAllocs int64
	MemBytes  int64
}

func newResult(b *B) *Result {
	return &Result{N: b.N, T: b.elapsed, Bytes: b.bytesPerOp,
		MemAllocs: b.memAllocs, MemBytes: b.memBytes}
}

func (r *Result) NsPerOp() float64 {
	if r.N == 0 {
		return 0
	}
	return 1e9 * r.T / float64(r.N)
}

// AllocedBytesPerOp："B/op" = r.MemBytes / r.N（整数除法）。
func (r *Result) AllocedBytesPerOp() int64 {
	if r.N == 0 {
		return 0
	}
	return r.MemBytes / int64(r.N)
}

// AllocsPerOp："allocs/op" = r.MemAllocs / r.N（整数除法）。
func (r *Result) AllocsPerOp() int64 {
	if r.N == 0 {
		return 0
	}
	return r.MemAllocs / int64(r.N)
}

func (r *Result) MBPerS() float64 {
	if r.Bytes <= 0 || r.T <= 0 {
		return 0
	}
	return float64(r.Bytes) * float64(r.N) / r.T / 1e6
}

func (r *Result) MemString() string {
	return fmt.Sprintf("%d B/op\t%d allocs/op", r.AllocedBytesPerOp(), r.AllocsPerOp())
}

// launch 是 go test 运行单个基准的简化模型：多次调整 b.N 直到持续足够久。
func launch(name string, body func(*B), target float64, gomaxprocs int, benchmem bool) (string, *Result) {
	b := &B{}
	n := 1
	for {
		b.N = n
		b.ResetTimer() // 框架行为：每轮重跑前清零计时/分配
		b.StartTimer()
		body(b)
		b.StopTimer()
		if b.elapsed >= target-1e-9 { // 浮点容差：10×0.1 累加为 0.999…9
			break
		}
		perOp := 0.0
		if b.N > 0 {
			perOp = b.elapsed / float64(b.N)
		}
		pred := 100 * n
		if perOp > 0 {
			pred = int(target / perOp)
		}
		next := pred
		if 100*n < next {
			next = 100 * n
		}
		if n+1 > next {
			next = n + 1
		}
		n = next
	}
	r := newResult(b)
	line := fmt.Sprintf("%s-%d\t%d\t%.2f ns/op", name, gomaxprocs, r.N, r.NsPerOp())
	if b.reportAllocsOn || benchmem {
		line += "\t" + r.MemString()
	}
	if r.Bytes > 0 {
		line += fmt.Sprintf("\t%.2f MB/s", r.MBPerS())
	}
	return line, r
}

func check(label string, cond bool) {
	if !cond {
		fmt.Println("FAIL:", label)
		panic("assertion failed: " + label)
	}
}

func main() {
	// 1. b.N 递增：0.1s/次、目标 1s → N 序列 1 → 10，第二轮达标停止
	var seq []int
	line, r := launch("BenchmarkStep", func(b *B) {
		seq = append(seq, b.N)
		for i := 0; i < b.N; i++ {
			b.Op(0.1, 24, 1)
		}
	}, 1.0, 8, false)
	check("n sequence 1,10", eqInts(seq, []int{1, 10}))
	check("ns/op", r.N == 10 && r.NsPerOp() > 1e8-1e-6 && r.NsPerOp() < 1e8+1e-6)
	check("name suffix", strings.Contains(line, "BenchmarkStep-8"))

	// 2. 单次就够久 → N 不再增长
	seq2 := []int{}
	_, r2 := launch("BenchmarkSlow", func(b *B) {
		seq2 = append(seq2, b.N)
		for i := 0; i < b.N; i++ {
			b.Op(1.5, 0, 0)
		}
	}, 1.0, 8, false)
	check("slow single round", len(seq2) == 1 && r2.N == 1)

	// 3. B/op 与 allocs/op：官方整数除法（MemBytes/N、MemAllocs/N）
	rr := &Result{N: 3, MemBytes: 1000, MemAllocs: 10}
	check("b/op int div", rr.AllocedBytesPerOp() == 333) // 1000/3 截断
	check("allocs/op int div", rr.AllocsPerOp() == 3)
	check("memstring", rr.MemString() == "333 B/op\t3 allocs/op")

	// 4. ReportAllocs 只影响调用它的基准；全局 benchmem 影响所有
	lineOn, _ := launch("BenchmarkA", func(b *B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			b.Op(0.5, 24, 1)
		}
	}, 1.0, 8, false)
	check("reportallocs on", strings.Contains(lineOn, "24 B/op") && strings.Contains(lineOn, "1 allocs/op"))
	lineOff, _ := launch("BenchmarkB", func(b *B) {
		for i := 0; i < b.N; i++ {
			b.Op(0.5, 24, 1)
		}
	}, 1.0, 8, false)
	check("no mem cols", !strings.Contains(lineOff, "B/op"))
	lineGlob, _ := launch("BenchmarkC", func(b *B) {
		for i := 0; i < b.N; i++ {
			b.Op(0.5, 24, 1)
		}
	}, 1.0, 8, true)
	check("benchmem global", strings.Contains(lineGlob, "24 B/op"))

	// 5. ResetTimer：清零时间与分配计数器（排除 setup），且每轮重跑也会重置
	_, r5 := launch("BenchmarkSetup", func(b *B) {
		b.Op(0.4, 10000, 500) // setup：耗 0.4s、分配 500 次
		b.ResetTimer()        // 关键：清零
		for i := 0; i < b.N; i++ {
			b.Op(0.6, 24, 1)
		}
	}, 0.6, 8, false)
	check("setup excluded", r5.N == 1 && r5.T == 0.6 && r5.MemBytes == 24 && r5.MemAllocs == 1)

	// 6. StopTimer/StartTimer：不想计入的收尾步骤
	_, r6 := launch("BenchmarkTail", func(b *B) {
		for i := 0; i < b.N; i++ {
			b.Op(0.6, 24, 1)
		}
		b.StopTimer()
		b.Op(0.3, 999, 99) // 收尾：不计
		b.StartTimer()
	}, 0.6, 8, false)
	check("tail excluded", r6.T == 0.6 && r6.MemBytes == 24 && r6.MemAllocs == 1)

	// 7. ResetTimer 不改变计时器运行状态（官方原文）
	b := &B{}
	b.StopTimer()
	b.ResetTimer()
	check("reset keeps stopped", !b.timerOn)
	b.StartTimer()
	b.ResetTimer()
	check("reset keeps running", b.timerOn)

	// 8. SetBytes → MB/s 吞吐列；与 B/op（分配字节）是两回事
	line8, r8 := launch("BenchmarkIO", func(b *B) {
		b.SetBytes(1024 * 1000)
		for i := 0; i < b.N; i++ {
			b.Op(0.1, 0, 0)
		}
	}, 1.0, 8, false)
	check("mb per s", r8.MBPerS() > 10.23 && r8.MBPerS() < 10.25) // 1024000B×10/1s
	check("throughput col", strings.Contains(line8, "MB/s") && !strings.Contains(line8, "B/op"))

	// 9. RunParallel 口径：ns/op 是整体墙钟时间，不是各 goroutine 之和
	workers := 4.0
	_, rPar := launch("BenchmarkPar", func(b *B) {
		for i := 0; i < b.N; i++ {
			b.Op(0.1/workers, 24, 1)
		}
	}, 1.0, 8, false)
	_, rSer := launch("BenchmarkSer", func(b *B) {
		for i := 0; i < b.N; i++ {
			b.Op(0.1, 24, 1)
		}
	}, 1.0, 8, false)
	check("parallel wall time", rPar.NsPerOp()*workers-rSer.NsPerOp() < 1e-6 &&
		rSer.NsPerOp()-rPar.NsPerOp()*workers < 1e-6)

	// 10. 同一 op 多次小分配 → allocs/op > 1
	line10, r10 := launch("BenchmarkChatty", func(b *B) {
		b.ReportAllocs()
		for i := 0; i < b.N; i++ {
			b.Op(1.0, 40, 3)
		}
	}, 1.0, 8, false)
	check("chatty allocs", r10.AllocsPerOp() == 3 && r10.AllocedBytesPerOp() == 40)
	check("chatty line", strings.Contains(line10, "40 B/op") && strings.Contains(line10, "3 allocs/op"))

	fmt.Println("bench_allocs: 10 组断言全部通过")
}

func eqInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
