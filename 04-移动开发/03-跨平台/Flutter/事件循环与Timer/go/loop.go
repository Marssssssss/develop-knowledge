// Package dartloop 复刻 dart-lang/sdk 里事件循环的两段实现：
//   sdk/lib/async/schedule_microtask.dart        —— 微任务单链表 + 优先级回调
//   sdk/lib/_internal/vm/lib/timer_impl.dart     —— _TimerHeap + 零延迟计时器链表
// 无本机 Go 工具链，仅人工审查 + 括号配平校验。
package dartloop

// ---------------------------------------------------------------- 微任务

// Entry 对应 _AsyncCallbackEntry：单链表节点。
type Entry struct {
	Callback func()
	Next     *Entry
}

// AsyncRuntime 对应 schedule_microtask.dart 的顶层状态。
type AsyncRuntime struct {
	NextCallback          *Entry
	LastCallback          *Entry
	LastPriorityCallback  *Entry
	IsInCallbackLoop      bool
	Immediates            []func()
	ScheduleCount         int
}

// ScheduleImmediate 对应 _AsyncRun._scheduleImmediate（external，由 VM 实现）。
func (r *AsyncRuntime) ScheduleImmediate(cb func()) {
	r.ScheduleCount++
	r.Immediates = append(r.Immediates, cb)
}

// ScheduleAsyncCallback 对应 _scheduleAsyncCallback。
func (r *AsyncRuntime) ScheduleAsyncCallback(cb func()) {
	e := &Entry{Callback: cb}
	if r.LastCallback == nil {
		r.NextCallback, r.LastCallback = e, e
		if !r.IsInCallbackLoop {
			r.ScheduleImmediate(r.StartMicrotaskLoop)
		}
		return
	}
	r.LastCallback.Next = e
	r.LastCallback = e
}

// SchedulePriorityAsyncCallback 对应 _schedulePriorityAsyncCallback：
// 插到「上一个优先级项」之后，因此多个优先级项之间仍保持调度顺序。
func (r *AsyncRuntime) SchedulePriorityAsyncCallback(cb func()) {
	if r.NextCallback == nil {
		r.ScheduleAsyncCallback(cb)
		r.LastPriorityCallback = r.LastCallback
		return
	}
	e := &Entry{Callback: cb}
	if r.LastPriorityCallback == nil {
		e.Next = r.NextCallback
		r.NextCallback, r.LastPriorityCallback = e, e
		return
	}
	nxt := r.LastPriorityCallback.Next
	e.Next = nxt
	r.LastPriorityCallback.Next = e
	r.LastPriorityCallback = e
	if nxt == nil {
		r.LastCallback = e
	}
}

// MicrotaskLoop 对应 _microtaskLoop：每轮开头清掉优先级尾指针。
func (r *AsyncRuntime) MicrotaskLoop() {
	for e := r.NextCallback; e != nil; e = r.NextCallback {
		r.LastPriorityCallback = nil
		nxt := e.Next
		r.NextCallback = nxt
		if nxt == nil {
			r.LastCallback = nil
		}
		e.Callback()
	}
}

// StartMicrotaskLoop 对应 _startMicrotaskLoop；finally 里若还有残留则再排一次。
func (r *AsyncRuntime) StartMicrotaskLoop() {
	r.IsInCallbackLoop = true
	defer func() {
		r.LastPriorityCallback = nil
		r.IsInCallbackLoop = false
		if r.NextCallback != nil {
			r.ScheduleImmediate(r.StartMicrotaskLoop)
		}
	}()
	r.MicrotaskLoop()
}

// RunEventLoop 把 immediate 队列跑干（真实的 isolate 循环还会夹带 I/O 事件）。
func (r *AsyncRuntime) RunEventLoop() int {
	turns := 0
	for len(r.Immediates) > 0 {
		cb := r.Immediates[0]
		r.Immediates = r.Immediates[1:]
		cb()
		turns++
	}
	return turns
}

// ------------------------------------------------------------- Timer 堆

// IDMask 见 timer_impl.dart：id 回绕掩码。
const IDMask = 0x1fffffff

// Timer 对应 _Timer。
type Timer struct {
	Callback    func(*Timer)
	WakeupTime  int
	MilliSeconds int
	Repeating   bool
	IndexOrNext int
	ID          int
	Tick        int
}

// CompareTo 先比唤醒时刻，再比 id（同时刻先进先出）。
func (t *Timer) CompareTo(o *Timer) int {
	if c := t.WakeupTime - o.WakeupTime; c != 0 {
		return c
	}
	return t.ID - o.ID
}

// TimerHeap 对应 _TimerHeap：初值 7，扩容 2n+1。
type TimerHeap struct {
	List []*Timer
	Used int
}

// NewTimerHeap 构造堆。
func NewTimerHeap() *TimerHeap { return &TimerHeap{List: make([]*Timer, 7)} }

func (h *TimerHeap) isFirst(t *Timer) bool { return t.IndexOrNext == 0 }

// Add 入堆并上浮。
func (h *TimerHeap) Add(t *Timer) {
	if h.Used == len(h.List) {
		nl := make([]*Timer, len(h.List)*2+1)
		copy(nl, h.List[:h.Used])
		h.List = nl
	}
	i := h.Used
	h.Used++
	t.IndexOrNext = i
	h.List[i] = t
	h.bubbleUp(t)
}

// RemoveFirst 弹出堆顶。
func (h *TimerHeap) RemoveFirst() *Timer {
	f := h.List[0]
	h.Remove(f)
	return f
}

// Remove 删除任意元素：尾部元素补位后按需上浮或下沉。
func (h *TimerHeap) Remove(t *Timer) {
	h.Used--
	if h.Used == 0 {
		h.List[0] = nil
		t.IndexOrNext = -1
		return
	}
	last := h.List[h.Used]
	if last != t {
		i := t.IndexOrNext
		last.IndexOrNext = i
		h.List[i] = last
		if last.CompareTo(t) < 0 {
			h.bubbleUp(last)
		} else {
			h.bubbleDown(last)
		}
	}
	h.List[h.Used] = nil
	t.IndexOrNext = -1
}

func (h *TimerHeap) bubbleUp(t *Timer) {
	for !h.isFirst(t) {
		p := h.List[(t.IndexOrNext-1)/2]
		if t.CompareTo(p) < 0 {
			h.swap(t, p)
			continue
		}
		break
	}
}

func (h *TimerHeap) bubbleDown(t *Timer) {
	for {
		li := 2*t.IndexOrNext + 1
		ri := 2*t.IndexOrNext + 2
		newest := t
		if li < h.Used && h.List[li].CompareTo(newest) < 0 {
			newest = h.List[li]
		}
		if ri < h.Used && h.List[ri].CompareTo(newest) < 0 {
			newest = h.List[ri]
		}
		if newest == t {
			return
		}
		h.swap(newest, t)
	}
}

func (h *TimerHeap) swap(a, b *Timer) {
	ia, ib := b.IndexOrNext, a.IndexOrNext
	a.IndexOrNext, b.IndexOrNext = ia, ib
	h.List[ia], h.List[ib] = a, b
}

// ParentIndex 与 Dart 的 (index - 1) ~/ 2 一致（Go 的整数除法对非负数等价）。
func ParentIndex(i int) int { return (i - 1) / 2 }
