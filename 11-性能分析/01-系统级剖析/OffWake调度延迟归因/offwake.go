// Off-Wake 调度延迟归因 —— Go 侧实现（无工具链，人工审查 + 机械核查）。
//
// 口径同 Python 版，来自 Brendan Gregg《Off-CPU Analysis》：
//   off-CPU time = 阻塞到重新开跑，含调度延迟（时间膨胀）；
//   唯一插桩点是 finish_task_switch() 结尾，在下一个线程的上下文中执行。
package main

import "fmt"

const (
	taskRunning        = 0
	taskInterruptible  = 1
	taskUninterruptible = 2
)

// Sample 是一段完整的 off-CPU 区间。
type Sample struct {
	TID        int
	Start, End float64
	State      int
	Stack      string
	WokenAt    float64 // <= 0 表示没有唤醒点
	WakerStack string
}

func (s Sample) OffCPU() float64 { return s.End - s.Start }

func (s Sample) Blocked() float64 {
	if s.WokenAt <= 0 {
		return s.OffCPU()
	}
	return s.WokenAt - s.Start
}

func (s Sample) SchedLatency() float64 {
	if s.WokenAt <= 0 {
		return 0
	}
	return s.End - s.WokenAt
}

func (s Sample) Inflation() float64 {
	b := s.Blocked()
	if b <= 0 {
		return 1
	}
	return s.OffCPU() / b
}

// Tracer 按原文伪代码实现 off-CPU 记账。
type Tracer struct {
	StateFilter int // <0 表示不过滤
	SleepTime   map[int]float64
	State       map[int]int
	WakeTS      map[int]float64
	WakerStack  map[int]string
	Samples     []Sample
}

func NewTracer(stateFilter int) *Tracer {
	return &Tracer{
		StateFilter: stateFilter,
		SleepTime:   map[int]float64{},
		State:       map[int]int{},
		WakeTS:      map[int]float64{},
		WakerStack:  map[int]string{},
	}
}

func (t *Tracer) OnWakeup(tid int, ts float64, wakerStack string) {
	t.WakeTS[tid] = ts
	t.WakerStack[tid] = wakerStack
}

// OnSwitchFinish 是 finish_task_switch(prev) 的结尾：给 prev 打起点、结算 cur。
func (t *Tracer) OnSwitchFinish(prevTID, prevState, curTID int, curStack string, ts float64) (Sample, bool) {
	t.SleepTime[prevTID] = ts
	t.State[prevTID] = prevState
	start := t.SleepTime[curTID]
	if start == 0 { // 0 表示没在睡 ⇒ 本次不是阻塞后的唤醒
		return Sample{}, false
	}
	state := t.State[curTID]
	s := Sample{
		TID: curTID, Start: start, End: ts, State: state, Stack: curStack,
		WokenAt: t.WakeTS[curTID], WakerStack: t.WakerStack[curTID],
	}
	delete(t.WakeTS, curTID)
	delete(t.WakerStack, curTID)
	t.SleepTime[curTID] = 0 // 原文：清零而不是删除
	t.Samples = append(t.Samples, s)
	return s, true
}

func (t *Tracer) Filtered() []Sample {
	if t.StateFilter < 0 {
		return t.Samples
	}
	var out []Sample
	for _, s := range t.Samples {
		if s.State == t.StateFilter {
			out = append(out, s)
		}
	}
	return out
}

// ByStack 按阻塞栈聚合 off-CPU 时间（折叠格式的 count 列）。
func (t *Tracer) ByStack() map[string]float64 {
	out := map[string]float64{}
	for _, s := range t.Filtered() {
		out[s.Stack] += s.OffCPU()
	}
	return out
}

// RequestSync 等价于 grep 请求上下文标记（线程池陷阱的解药）。
func (t *Tracer) RequestSync(marker string) []Sample {
	var out []Sample
	for _, s := range t.Filtered() {
		if contains(s.Stack, marker) {
			out = append(out, s)
		}
	}
	return out
}

// OffWakeKey 是 (唤醒者栈, 阻塞栈) 二元组。
type OffWakeKey struct{ Waker, Waiter string }

func (t *Tracer) OffWake() map[OffWakeKey]float64 {
	out := map[OffWakeKey]float64{}
	for _, s := range t.Filtered() {
		w := s.WakerStack
		if w == "" {
			w = "(unknown waker)"
		}
		out[OffWakeKey{w, s.Stack}] += s.OffCPU()
	}
	return out
}

func contains(s, sub string) bool {
	if sub == "" {
		return true
	}
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}

func main() {
	t := NewTracer(-1)
	t.SleepTime[1] = 1
	t.State[1] = taskUninterruptible
	t.OnWakeup(1, 11, "irq;net_rx_action")
	s, ok := t.OnSwitchFinish(3, taskRunning, 1, "app;read", 91)
	if ok {
		fmt.Printf("off_cpu=%.1f blocked=%.1f sched=%.1f inflation=%.2f\n",
			s.OffCPU(), s.Blocked(), s.SchedLatency(), s.Inflation())
	}

	t2 := NewTracer(taskUninterruptible)
	t2.SleepTime[1] = 1
	t2.SleepTime[2] = 1
	t2.SleepTime[3] = 1
	t2.State[1] = taskUninterruptible
	t2.State[2] = taskInterruptible
	t2.State[3] = taskRunning
	t2.OnSwitchFinish(9, taskRunning, 1, "io", 100)
	t2.OnSwitchFinish(9, taskRunning, 2, "sleep", 100)
	t2.OnSwitchFinish(9, taskRunning, 3, "spin", 100)
	fmt.Printf("全部 %d 条，--state 2 过滤后 %d 条\n", len(t2.Samples), len(t2.Filtered()))
	fmt.Printf("off-wake 键数=%d，按阻塞栈聚合只有 %d 个\n", len(t2.OffWake()), len(t2.ByStack()))
}
