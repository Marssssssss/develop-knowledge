// Package main 把 JMH -prof gc 与 Go -benchmem 的分配口径做成同一份模型下的两个报告器。
// 口径来源：openjdk/jmh 的 GCProfiler.java 与 golang/go 的 src/testing/benchmark.go。
package main

import (
	"fmt"
	"math"
)

// Policy 对应 JMH 的 AggregationPolicy。
type Policy string

const (
	Sum Policy = "SUM"
	Avg Policy = "AVG"
)

// ScalarResult 对应 JMH 的 ScalarResult(name, value, unit, policy)。
type ScalarResult struct {
	Name   string
	Value  float64
	Unit   string
	Policy Policy
}

// Snapshot 是 GCProfiler 在 beforeIteration / afterIteration 各取一次的快照。
type Snapshot struct {
	Counts      map[string]int64 // 每个 GarbageCollectorMXBean 的 getCollectionCount
	TimesMs     map[string]int64 // getCollectionTime
	GcCPUNs     int64            // MemoryMXBean.getTotalGcCpuTime，-1 表示不支持
	TotalAlloc  int64            // getTotalThreadAllocatedBytes，-1 表示被禁用
	ThreadAlloc map[int64]int64  // 回退路径：getThreadAllocatedBytes
	HasThreads  bool
}

// ChurnEvent 对应一次 GarbageCollectionNotificationInfo。
type ChurnEvent struct {
	Before map[string]int64
	After  map[string]int64
}

type options struct {
	allocEnabled   bool
	churnEnabled   bool
	churnWaitMs    int64
	gcCPUSupported bool
	gcCPUBefore    int64
	events         []ChurnEvent
	currentThread  int64 // 0 表示不排除任何线程
	hasCurrent     bool
}

func defaultOptions() options {
	return options{allocEnabled: true, churnEnabled: false, churnWaitMs: 500}
}

func delta(after, before map[string]int64) int64 {
	keys := map[string]bool{}
	for k := range after {
		keys[k] = true
	}
	for k := range before {
		keys[k] = true
	}
	var d int64
	for k := range keys {
		d += after[k] - before[k]
	}
	return d
}

// allocatedBytes 复刻 VMSupport.getSnapshot + difference。
func allocatedBytes(before, after Snapshot, opt options) (int64, bool) {
	if before.TotalAlloc != -1 && after.TotalAlloc != -1 {
		d := after.TotalAlloc - before.TotalAlloc
		if d < 0 {
			return 0, true // "Do not allow negative values"
		}
		return d, true
	}
	if !before.HasThreads || !after.HasThreads {
		return 0, false
	}
	var allocated int64
	for tid, val := range after.ThreadAlloc {
		if opt.hasCurrent && tid == opt.currentThread {
			continue // 当前线程被刻意排除
		}
		allocated += val - before.ThreadAlloc[tid]
	}
	return allocated, true
}

// GcProfile 复刻 GCProfiler.afterIteration 的结果构造顺序。
func GcProfile(before, after Snapshot, dtNs, allOps int64, opt options) []ScalarResult {
	out := []ScalarResult{}
	gcCount := delta(after.Counts, before.Counts)
	gcTime := delta(after.TimesMs, before.TimesMs)

	out = append(out, ScalarResult{"gc.count", float64(gcCount), "counts", Sum})
	if gcCount != 0 || gcTime != 0 {
		out = append(out, ScalarResult{"gc.time", float64(gcTime), "ms", Sum})
	}
	if opt.gcCPUSupported {
		out = append(out, ScalarResult{"gc.cpuTime", float64((after.GcCPUNs - opt.gcCPUBefore) / 1_000_000), "ms", Sum})
	}

	if opt.allocEnabled {
		allocated, okAlloc := allocatedBytes(before, after, opt)
		if !okAlloc {
			out = append(out, ScalarResult{"gc.alloc.rate", math.NaN(), "MB/sec", Avg})
		} else {
			rate := math.NaN()
			if dtNs != 0 {
				rate = float64(allocated) / 1024 / 1024 * 1e9 / float64(dtNs)
			}
			out = append(out, ScalarResult{"gc.alloc.rate", rate, "MB/sec", Avg})
			if allocated != 0 { // 零分配时整列消失
				norm := math.NaN()
				if allOps != 0 {
					norm = 1.0 * float64(allocated) / float64(allOps)
				}
				out = append(out, ScalarResult{"gc.alloc.rate.norm", norm, "B/op", Avg})
			}
		}
	}

	if opt.churnEnabled {
		churn := map[string]int64{}
		for _, ev := range opt.events {
			for space, afterUsed := range ev.After {
				if c := ev.Before[space] - afterUsed; c > 0 {
					churn[space] += c
				}
			}
		}
		for space, bytes := range churn {
			rate := math.NaN()
			norm := math.NaN()
			if dtNs != 0 {
				rate = float64(bytes) * 1e9 / float64(dtNs) / 1024 / 1024
			}
			if allOps != 0 {
				norm = float64(bytes) / float64(allOps)
			}
			out = append(out, ScalarResult{"gc.churn." + space, rate, "MB/sec", Avg})
			out = append(out, ScalarResult{"gc.churn." + space + ".norm", norm, "B/op", Avg})
		}
	}
	return out
}

// Aggregate 按 Policy 聚合多轮：SUM 求和、AVG 取算术平均。
func Aggregate(rounds [][]ScalarResult) map[string]float64 {
	sums := map[string]float64{}
	avgSum := map[string]float64{}
	avgN := map[string]int{}
	for _, r := range rounds {
		for _, s := range r {
			if s.Policy == Sum {
				sums[s.Name] += s.Value
			} else {
				avgSum[s.Name] += s.Value
				avgN[s.Name]++
			}
		}
	}
	out := map[string]float64{}
	for k, v := range sums {
		out[k] = v
	}
	for k, v := range avgSum {
		out[k] = v / float64(avgN[k])
	}
	return out
}

// ---- Go -benchmem 侧 ----

// MemStats 只保留 Mallocs / TotalAlloc 两个参与净值计算的字段。
type MemStats struct {
	Mallocs    int64
	TotalAlloc int64
}

// GoBench 复刻 testing.B 与分配相关的那几条语句。
type GoBench struct {
	Mem        *MemStats
	N          int64
	TimerOn    bool
	StartAllocs, StartBytes int64
	NetAllocs, NetBytes     int64
	Duration                int64
	NumGC                   int
}

func (b *GoBench) StartTimer() {
	if !b.TimerOn {
		b.StartAllocs = b.Mem.Mallocs
		b.StartBytes = b.Mem.TotalAlloc
		b.TimerOn = true
	}
}

func (b *GoBench) StopTimer() {
	if b.TimerOn {
		b.NetAllocs += b.Mem.Mallocs - b.StartAllocs
		b.NetBytes += b.Mem.TotalAlloc - b.StartBytes
		b.TimerOn = false
	}
}

// ResetTimer 只清净值与 duration；timerOn 时顺带重设起点。
func (b *GoBench) ResetTimer() {
	if b.TimerOn {
		b.StartAllocs = b.Mem.Mallocs
		b.StartBytes = b.Mem.TotalAlloc
	}
	b.Duration = 0
	b.NetAllocs = 0
	b.NetBytes = 0
}

// RunN 复刻 runtime.GC() → ResetTimer → StartTimer → benchFunc → StopTimer 的顺序。
func (b *GoBench) RunN(allocs, bytes int64, elapsedNs int64) {
	b.NumGC++
	b.ResetTimer()
	b.StartTimer()
	b.Duration = elapsedNs
	b.Mem.Mallocs += allocs
	b.Mem.TotalAlloc += bytes
	b.StopTimer()
}

func (b *GoBench) NsPerOp() int64 {
	if b.N <= 0 {
		return 0
	}
	return b.Duration / b.N
}

func (b *GoBench) AllocsPerOp() int64 {
	if b.N <= 0 {
		return 0
	}
	return b.NetAllocs / b.N
}

func (b *GoBench) AllocedBytesPerOp() int64 {
	if b.N <= 0 {
		return 0
	}
	return b.NetBytes / b.N
}

func (b *GoBench) MemString() string {
	return fmt.Sprintf("%8d B/op\t%8d allocs/op", b.AllocedBytesPerOp(), b.AllocsPerOp())
}

// Lookup 取指定名字的结果，未发射时返回 (0,false)。
func Lookup(res []ScalarResult, name string) (float64, bool) {
	for _, r := range res {
		if r.Name == name {
			return r.Value, true
		}
	}
	return 0, false
}

func main() {
	// 真实每次操作分配 0.5 字节：JMH 报 0.5，Go 截断成 0。
	opt := defaultOptions()
	jmh := GcProfile(Snapshot{TotalAlloc: 0}, Snapshot{TotalAlloc: 500}, 1e9, 1000, opt)
	v, _ := Lookup(jmh, "gc.alloc.rate.norm")
	fmt.Printf("JMH  gc.alloc.rate.norm = %.1f B/op\n", v)

	mem := &MemStats{}
	b := &GoBench{Mem: mem, N: 1000}
	b.RunN(1000, 500, 1000)
	fmt.Printf("Go   %8d ns/op\t%s\n", b.NsPerOp(), b.MemString())

	// 零分配：JMH 没有 norm 列，Go 仍打印 0 B/op。
	jmh = GcProfile(Snapshot{TotalAlloc: 0}, Snapshot{TotalAlloc: 0}, 1e9, 1000, opt)
	_, hasNorm := Lookup(jmh, "gc.alloc.rate.norm")
	b2 := &GoBench{Mem: &MemStats{}, N: 1000}
	b2.RunN(0, 0, 1000)
	fmt.Printf("零分配: JMH 有 norm 列 = %v, Go = %s\n", hasNorm, b2.MemString())

	// 聚合策略陷阱：count 是 SUM，norm 是 AVG。
	o := defaultOptions()
	r1 := GcProfile(Snapshot{Counts: map[string]int64{"G": 0}, TotalAlloc: 0},
		Snapshot{Counts: map[string]int64{"G": 1}, TotalAlloc: 1000}, 1e7, 100, o)
	r2 := GcProfile(Snapshot{Counts: map[string]int64{"G": 0}, TotalAlloc: 0},
		Snapshot{Counts: map[string]int64{"G": 1}, TotalAlloc: 3000}, 3e7, 1000, o)
	agg := Aggregate([][]ScalarResult{r1, r2})
	fmt.Printf("两轮聚合: gc.count = %.0f (SUM), norm = %.4f (AVG), 加权真值 = %.4f\n",
		agg["gc.count"], agg["gc.alloc.rate.norm"], 4000.0/1100.0)
}
