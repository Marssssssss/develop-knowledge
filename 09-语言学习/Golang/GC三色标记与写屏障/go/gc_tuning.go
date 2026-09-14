// gc_tuning.go — 单文件串讲 Go GC 的观测与调优 API(算法模拟见 python/tricolor_gc.py)
// Demo 4: 三色标记 + 混合写屏障 + GOGC/Pacer 的运行时口径
// 涵盖 5 个示例:ReadMemStats / runtime.GC / GOGC 目标堆公式 /
// debug.SetGCPercent / PauseNs 历史暂停
package main

import (
	"fmt"
	"runtime"
	"runtime/debug"
)

// memInMiB 辅助:字节数转 MiB 字符串
func memInMiB(b uint64) string {
	return fmt.Sprintf("%.2f MiB", float64(b)/(1<<20))
}

// ---------- 1) ReadMemStats:调用时刻最新的分配器统计 ----------
// runtime 文档:ReadMemStats populates m with memory allocator statistics,
// 数据是"调用时刻最新值",区别于堆 profile(只反映最近一次完成 GC 的快照)。
func demoMemStats() {
	fmt.Println("[1] ReadMemStats")
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	fmt.Printf("  HeapAlloc = %s(存活+待回收的堆对象)\n", memInMiB(m.HeapAlloc))
	fmt.Printf("  HeapSys   = %s(向 OS 申请的堆内存)\n", memInMiB(m.HeapSys))
	fmt.Printf("  NumGC     = %d(完成的 GC 周期数)\n", m.NumGC)
}

// ---------- 2) runtime.GC():强制一轮 GC 并阻塞调用者 ----------
// runtime 文档:GC runs a garbage collection and blocks the caller until the
// garbage collection is complete. It may also block the entire program.
// gc-guide 补充:它只"排队"cleanups/finalizers,不等待其执行完。
func demoForceGC() {
	fmt.Println("\n[2] runtime.GC()")
	sink := make([]*[]byte, 0, 8)
	for i := 0; i < 4; i++ {
		b := make([]byte, 4<<20) // 每块 4 MiB
		sink = append(sink, &b)
	}
	var before runtime.MemStats
	runtime.ReadMemStats(&before)
	sink = nil // 断引用,4 块 16MiB 变垃圾
	runtime.GC()
	var after runtime.MemStats
	runtime.ReadMemStats(&after)
	fmt.Printf("  GC 前 HeapAlloc = %s\n", memInMiB(before.HeapAlloc))
	fmt.Printf("  GC 后 HeapAlloc = %s(垃圾被清扫)\n", memInMiB(after.HeapAlloc))
	fmt.Printf("  NumGC %d -> %d\n", before.NumGC, after.NumGC)
}

// ---------- 3) GOGC 目标堆公式(gc-guide,Go 1.18+ 口径) ----------
// Target heap memory = Live heap + (Live heap + GC roots) * GOGC / 100
// Go 1.18 之前不计 roots:Target = Live * (1 + GOGC/100)
// 关键结论:doubling GOGC will double heap memory overheads and roughly
// halve GC CPU cost(反之亦然);最小堆 4 MiB 兜底。
func gogcTarget(liveHeap, gcRoots uint64, gogc int) uint64 {
	const minHeap = uint64(4) << 20 // gc-guide: minimum total heap size of 4 MiB
	target := liveHeap + (liveHeap+gcRoots)*uint64(gogc)/100
	if target < minHeap {
		return minHeap
	}
	return target
}

func demoGOGCFormula() {
	fmt.Println("\n[3] GOGC 目标堆公式")
	live := uint64(8) << 20  // 8 MiB 存活堆(复刻 gc-guide 官方示例)
	roots := uint64(2) << 20 // 1 MiB 栈 + 1 MiB 全局指针 = 2 MiB roots
	for _, gogc := range []int{50, 100, 200} {
		fmt.Printf("  GOGC=%3d -> 目标堆 %s\n", gogc, memInMiB(gogcTarget(live, roots, gogc)))
	}
	// 官方示例(live 8MiB + 根 2MiB,即"work"=10MiB):GOGC=100 时
	// 新分配 10MiB、总足迹 18MiB;50 -> 5MiB;200 -> 20MiB
	fmt.Println("  (gc-guide 官方示例口径:GOGC=100 时总足迹 18 MiB)")
}

// ---------- 4) debug.SetGCPercent:程序化调 GOGC ----------
// gc-guide:GOGC 可用环境变量或 SetGCPercent API 配置;
// SetGCPercent(-1) 等价于 GOGC=off(彻底关闭 GC,前提是没有内存限制约束)。
func demoSetGCPercent() {
	fmt.Println("\n[4] debug.SetGCPercent")
	old := debug.SetGCPercent(200) // 返回旧设置
	fmt.Printf("  SetGCPercent(200) 返回旧值 %d\n", old)
	debug.SetGCPercent(old) // 改回去
	// debug.SetGCPercent(-1) 即 GOGC=off —— 演示后立刻恢复,避免影响后续
	off := debug.SetGCPercent(-1)
	fmt.Printf("  SetGCPercent(-1) 返回旧值 %d(GOGC=off)\n", off)
	debug.SetGCPercent(off)
}

// ---------- 5) PauseNs:最近 GC 周期的 STW 暂停历史 ----------
// gc-guide:暂停主要由"停住所有运行中 goroutine"的时间决定,
// 与堆大小解耦(核心追踪与应用并发执行);1.8 混合写屏障消除了栈重扫,
// 演进史见 README(1.5: 300-400ms -> 30-40ms;1.8: sub-millisecond)。
func demoPauseNs() {
	fmt.Println("\n[5] PauseNs 最近暂停")
	runtime.GC() // 保证有新数据
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	n := int(m.NumGC)
	if n > 5 {
		n = 5 // 只看最近几轮
	}
	for i := 0; i < n; i++ {
		idx := (int(m.NumGC) + 255 - i) % 256 // NumGC 回绕缓冲区 256 项
		fmt.Printf("  第 -%d 轮 STW 暂停 ~%d µs\n", i, m.PauseNs[idx]/1000)
	}
}

func main() {
	demoMemStats()
	demoForceGC()
	demoGOGCFormula()
	demoSetGCPercent()
	demoPauseNs()
}
