// Package main 用 Go 语言复刻 runtime 的定时器堆（四叉堆 + 延迟同步的状态位）。
//
// 设计取舍：官方 src/runtime/time.go 里的 mutex、atomic、per-P 归属、写屏障
// 都剥掉，只保留**算法与状态机**本身，便于单线程观察。每个函数都标注了
// 对应的官方函数名，可以逐行对照阅读。
package main

const (
	// timerHeapN 与官方同名常量：堆是四叉的，不是二叉的。
	timerHeapN = 4

	// maxWhen 与官方同名常量。
	maxWhen = 1<<63 - 1
)

// 定时器状态位，取值与官方一致（time.go 的 const 块）。
const (
	timerHeaped uint8 = 1 << iota // 在堆里
	timerModified                 // t.when 已改，heap[i].when 未同步
	timerZombie                   // 已 Stop，但还没从堆里摘除
)

// Timer 对应 runtime.timer（去掉锁与原子字段）。
type Timer struct {
	When   int64
	Period int64
	Rand   int32 // 同一时刻的随机次序
	Name   string

	state  uint8
	astate uint8 // unlock 时刻写出的 state 副本
	ts     *Timers
}

// TimerWhen 对应 runtime.timerWhen：堆里存「时刻快照 + 指针」两份信息。
type TimerWhen struct {
	Timer *Timer
	When  int64
}

// Timers 对应 runtime.timers：一个 P 的定时器集合。
type Timers struct {
	Heap            []TimerWhen
	Zombies         int32
	MinWhenHeap     int64
	MinWhenModified int64
	SiftUpSteps     int // 模型附加：观察上浮次数
	SiftDownSteps   int // 模型附加：观察下沉次数
	ZombiePeak      int32
	Fired           []string
}

// less 对应 timerWhen.less：先比 when，完全相等再比 Rand。
func less(a, b TimerWhen) bool {
	if a.When < b.When {
		return true
	}
	if a.When > b.When {
		return false
	}
	return a.Timer.Rand < b.Timer.Rand
}

func badTimer() {
	panic("timer data corruption")
}

// unlock 对应 (*timer).unlock：把 state 刷进 astate。
func (t *Timer) unlock() {
	t.astate = t.state
}

// SiftUp 对应 (*timers).siftUp。
func (ts *Timers) SiftUp(i int) {
	heap := ts.Heap
	if i >= len(heap) {
		badTimer()
	}
	tw := heap[i]
	if tw.When <= 0 {
		badTimer()
	}
	for i > 0 {
		p := (i - 1) / timerHeapN // parent
		if !less(tw, heap[p]) {
			break
		}
		heap[i] = heap[p]
		i = p
		ts.SiftUpSteps++
	}
	if heap[i].Timer != tw.Timer {
		heap[i] = tw
	}
}

// SiftDown 对应 (*timers).siftDown。
func (ts *Timers) SiftDown(i int) {
	heap := ts.Heap
	n := len(heap)
	if i >= n {
		badTimer()
	}
	if i*timerHeapN+1 >= n {
		return // 官方的提前返回：i 已经是叶子
	}
	tw := heap[i]
	if tw.When <= 0 {
		badTimer()
	}
	for {
		left := i*timerHeapN + 1
		if left >= n {
			break
		}
		w := tw
		c := -1
		end := left + timerHeapN
		if end > n {
			end = n
		}
		for j, twj := range heap[left:end] {
			if less(twj, w) {
				w = twj
				c = left + j
			}
		}
		if c < 0 {
			break
		}
		heap[i] = heap[c]
		i = c
		ts.SiftDownSteps++
	}
	if heap[i].Timer != tw.Timer {
		heap[i] = tw
	}
}

// InitHeap 对应 (*timers).initHeap：O(n) 重建堆序。
func (ts *Timers) InitHeap() {
	if len(ts.Heap) <= 1 {
		return
	}
	for i := (len(ts.Heap) - 2) / timerHeapN; i >= 0; i-- {
		ts.SiftDown(i)
	}
}

// AddHeap 对应 maybeAdd + addHeap：官方在 maybeAdd 里置 timerHeaped 后再入堆。
func (ts *Timers) AddHeap(t *Timer) {
	if t.ts != nil {
		panic("ts set in timer")
	}
	t.state |= timerHeaped
	t.ts = ts
	ts.Heap = append(ts.Heap, TimerWhen{t, t.When})
	ts.SiftUp(len(ts.Heap) - 1)
	if t == ts.Heap[0].Timer {
		ts.updateMinWhenHeap()
	}
}

// DeleteMin 对应 (*timers).deleteMin。
func (ts *Timers) DeleteMin() {
	t := ts.Heap[0].Timer
	if t.ts != ts {
		panic("wrong timers")
	}
	t.ts = nil
	last := len(ts.Heap) - 1
	if last > 0 {
		ts.Heap[0] = ts.Heap[last]
	}
	ts.Heap[last] = TimerWhen{}
	ts.Heap = ts.Heap[:last]
	if last > 0 {
		ts.SiftDown(0)
	}
	ts.updateMinWhenHeap()
	if last == 0 {
		ts.MinWhenModified = 0
	}
}

// UpdateMinWhenModified 对应 (*timers).updateMinWhenModified。
func (ts *Timers) UpdateMinWhenModified(when int64) {
	if ts.MinWhenModified != 0 && ts.MinWhenModified < when {
		return
	}
	ts.MinWhenModified = when
}

func (ts *Timers) updateMinWhenHeap() {
	if len(ts.Heap) == 0 {
		ts.MinWhenHeap = 0
		return
	}
	ts.MinWhenHeap = ts.Heap[0].When
}
