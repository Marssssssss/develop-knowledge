// Go execution tracer 复刻：确定性事件流、乱序重排、trace 拆分、flight recorder。
//
// 口径（go.dev 官方博客与 pkg.go.dev，本轮实读）：
//   - 与 pprof 互补：pprof 采样只能看到「有执行」的时刻，
//     一堆 goroutine 阻塞在同一个 channel 上这种并发瓶颈，CPU profile 里几乎没有样本
//   - 开销：Go 1.21 前约 10–20% CPU，优化 traceback 后降到 1–2%（traceback 是大头）
//   - 事件写入 per-P/线程本地缓冲 ⇒ **落盘顺序不等于真实时间顺序**，重排由工具负责
//   - Go 1.22 起 runtime 可以随时拆分 trace：切点之前的数据本身是完整自包含的
//   - Flight recorder（Go 1.25）：常开 tracing，内存里只留最近一段；
//     MinAge 建议取「待观测事件窗口的 2 倍」，MaxBytes 限制缓冲大小
//   - 用户标注三类：log（带 category）、region（同 goroutine 内的区间，可嵌套）、
//     task（跨 goroutine，走 context.Context，延迟 = NewTask → End）
package main

import (
	"fmt"
	"sort"
)

const (
	Running = "running"
	Waiting = "waiting"
)

// Event 对应 runtime/trace 的 EventStateTransition（只建 goroutine 资源）。
type Event struct {
	Ts     int64
	G      string
	To     string
	From   string
	Reason string
}

// IsBlock 对应官方示例：from.Executing() && to == GoWaiting。
func (e Event) IsBlock() bool { return e.From == Running && e.To == Waiting }

// BlockedOnNetworkRatio 复刻官方博客里那段统计代码。
func BlockedOnNetworkRatio(evs []Event) (float64, bool) {
	var blocked, onNet int
	for _, e := range evs {
		if !e.IsBlock() {
			continue
		}
		blocked++
		if contains(e.Reason, "network") {
			onNet++
		}
	}
	if blocked == 0 {
		return 0, false
	}
	return 100 * float64(onNet) / float64(blocked), true
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

// PeakConcurrency 按给定顺序扫一遍算并发峰值：顺序错了结论就错了。
func PeakConcurrency(evs []Event) int {
	cur, mx := 0, 0
	for _, e := range evs {
		if e.To == Running {
			cur++
			if cur > mx {
				mx = cur
			}
		} else if e.From == Running {
			cur--
		}
	}
	return mx
}

// SplitTrace 复刻 Go 1.22 的切点：切点前后各自自包含、可独立解析。
func SplitTrace(evs []Event, at int64) ([]Event, []Event) {
	var before, after []Event
	for _, e := range evs {
		if e.Ts < at {
			before = append(before, e)
		} else {
			after = append(after, e)
		}
	}
	return before, after
}

// SelfContained 判断一段 trace 里每个 goroutine 的状态迁移是否闭合。
func SelfContained(evs []Event) bool {
	sorted := append([]Event(nil), evs...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Ts < sorted[j].Ts })
	state := map[string]string{}
	for _, e := range sorted {
		if e.To == Running {
			state[e.G] = Running
		} else if state[e.G] == Running {
			state[e.G] = e.To
		}
	}
	for _, v := range state {
		if v == Running {
			return false
		}
	}
	return true
}

// FlightRecorder 复刻 runtime/trace.FlightRecorder 的两个约束。
type FlightRecorder struct {
	MinAge     int64 // 可靠保留的时长
	MaxBytes   int64
	BytesPerEv int64
	buf        []Event
}

func (fr *FlightRecorder) Add(e Event) {
	fr.buf = append(fr.buf, e)
	fr.evict()
}

func (fr *FlightRecorder) evict() {
	maxEvents := fr.MaxBytes / fr.BytesPerEv
	if int64(len(fr.buf)) > maxEvents {
		fr.buf = fr.buf[int64(len(fr.buf))-maxEvents:]
	}
	if len(fr.buf) == 0 {
		return
	}
	newest := fr.buf[0].Ts
	for _, e := range fr.buf {
		if e.Ts > newest {
			newest = e.Ts
		}
	}
	kept := fr.buf[:0]
	for _, e := range fr.buf {
		if e.Ts >= newest-fr.MinAge {
			kept = append(kept, e)
		}
	}
	fr.buf = kept
}

func (fr *FlightRecorder) WriteTo() []Event {
	out := append([]Event(nil), fr.buf...)
	sort.Slice(out, func(i, j int) bool { return out[i].Ts < out[j].Ts })
	return out
}

// Task 对应 runtime/trace 的 task：跨 goroutine，延迟按 NewTask→End 计算。
type Task struct {
	Name    string
	Created int64
	Ended   int64
	Regions map[string]string // region 名 → 所在 goroutine
}

func (t *Task) Latency() int64 { return t.Ended - t.Created }

func main() {
	// 1) 并发瓶颈：8 个 goroutine 全程阻塞 ⇒ CPU profile 里没有样本
	var blocked []Event
	for i := 0; i < 8; i++ {
		blocked = append(blocked, Event{Ts: 0, G: fmt.Sprintf("g%d", i), To: Waiting,
			From: Running, Reason: "chan receive"})
	}
	fmt.Println("blocked events:", len(blocked), "network ratio ok:",
		func() bool { _, ok := BlockedOnNetworkRatio(blocked); return ok }())

	// 2) 乱序：落盘顺序 ≠ 真实时间顺序
	shuffled := []Event{
		{Ts: 100, G: "g1", To: Running},
		{Ts: 50, G: "g2", To: Running},
		{Ts: 120, G: "g1", To: Waiting, From: Running},
		{Ts: 60, G: "g2", To: Waiting, From: Running},
	}
	sorted := append([]Event(nil), shuffled...)
	sort.Slice(sorted, func(i, j int) bool { return sorted[i].Ts < sorted[j].Ts })
	fmt.Println("peak: emitted-order =", PeakConcurrency(shuffled),
		", true-order =", PeakConcurrency(sorted))

	// 3) 拆分
	evs := []Event{
		{Ts: 10, G: "g1", To: Running}, {Ts: 20, G: "g1", To: Waiting, From: Running},
		{Ts: 30, G: "g2", To: Running}, {Ts: 40, G: "g2", To: Waiting, From: Running},
	}
	before, after := SplitTrace(evs, 25)
	fmt.Println("split:", len(before), len(after), SelfContained(before), SelfContained(after))
	fmt.Println("half trace self-contained:", SelfContained(evs[:1]))

	// 4) flight recorder：MaxBytes 才是硬约束（10 MB/s × 1 MiB ⇒ 只留得住 0.1 s）
	mb := int64(1 << 20)
	fmt.Printf("1MiB @10MB/s retains %.2f s, MinAge=10s achievable: %v\n",
		float64(mb)/float64(10*mb), float64(mb)/float64(10*mb) >= 10)

	fr := &FlightRecorder{MinAge: 10_000, MaxBytes: mb, BytesPerEv: 64}
	for ts := int64(0); ts < 60_000; ts += 100 {
		to := Waiting
		from := Running
		if ts%200 == 0 {
			to, from = Running, ""
		}
		fr.Add(Event{Ts: ts, G: "g", To: to, From: from})
	}
	snap := fr.WriteTo()
	fmt.Println("snapshot events:", len(snap), "span:", snap[len(snap)-1].Ts-snap[0].Ts)

	// 5) task 跨 goroutine
	t := &Task{Name: "makeCappuccino", Created: 0, Ended: 5,
		Regions: map[string]string{"steamMilk": "g1", "extractCoffee": "g2"}}
	fmt.Println("task latency:", t.Latency(), "goroutines:", len(t.Regions))
}
