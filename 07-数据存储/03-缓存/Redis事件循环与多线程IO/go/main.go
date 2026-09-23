package main

import "fmt"

func mkPoll(ready []FiredEvent) func(*EventLoop, *int64) []FiredEvent {
	return func(el *EventLoop, tvp *int64) []FiredEvent {
		return ready
	}
}

func dispatchDemo(barrier bool) ([]string, string) {
	clock := &ManualClock{Now: 0}
	el := NewEventLoop(64, clock, mkPoll([]FiredEvent{{Fd: 7, Mask: AeReadable | AeWritable}}))
	var trace []string
	rp := &FileProc{Call: func(e *EventLoop, fd int, d interface{}, m int) { trace = append(trace, "读回调") }}
	wp := &FileProc{Call: func(e *EventLoop, fd int, d interface{}, m int) { trace = append(trace, "写回调") }}
	AeCreateFileEvent(el, 7, AeReadable, rp, nil)
	AeCreateFileEvent(el, 7, AeWritable, wp, nil)
	if barrier {
		el.Events[7].Mask |= AeBarrier
	}
	AeProcessEvents(el, AeFileEvents|AeCallBeforeSleep|AeCallAfterSleep)
	tvp := "无限等待"
	if !el.TvpInfinite {
		tvp = fmt.Sprintf("%d us", el.TvpUs)
	}
	return trace, tvp
}

func timerDemo() {
	clock := &ManualClock{Now: 1000000}
	el := NewEventLoop(64, clock, mkPoll(nil))
	var fired []int64
	cron := func(e *EventLoop, id int64, d interface{}) int64 {
		fired = append(fired, clock.GetMonotonicUs())
		clock.Advance(20000)
		return 50
	}
	oneshot := func(e *EventLoop, id int64, d interface{}) int64 {
		fired = append(fired, clock.GetMonotonicUs())
		return AeNoMore
	}
	AeCreateTimeEvent(el, 10, cron, nil, nil)
	AeCreateTimeEvent(el, 10, oneshot, nil, nil)
	clock.Advance(10000) // 循环睡到最近定时器到期
	n := AeProcessEvents(el, AeTimeEvents)
	fmt.Printf("  本轮处理 %d 个时间事件，触发时刻(us) = %v\n", n, fired)
	fmt.Print("  剩余定时器：")
	for te := el.TimeEventHead; te != nil; te = te.Next {
		fmt.Printf("id=%d when=%d  ", te.ID, te.When)
	}
	fmt.Println("\n  （一次性事件返回 AE_NOMORE → id 已置 -1，下一轮才释放）")
}

func main() {
	fmt.Println("== 1. 文件事件派发顺序 ==")
	t1, tvp := dispatchDemo(false)
	fmt.Printf("  无 AE_BARRIER : %v   poll 超时=%s\n", t1, tvp)
	t2, _ := dispatchDemo(true)
	fmt.Printf("  有 AE_BARRIER : %v  （写被提到读之前）\n", t2)

	fmt.Println("\n== 2. 时间事件 ==")
	timerDemo()

	fmt.Println("\n== 3. poll 超时由最近定时器决定 ==")
	clock := &ManualClock{Now: 0}
	el := NewEventLoop(64, clock, mkPoll(nil))
	AeCreateTimeEvent(el, 30, func(e *EventLoop, id int64, d interface{}) int64 { return AeNoMore }, nil, nil)
	AeProcessEvents(el, AeTimeEvents)
	fmt.Printf("  最近定时器 30ms 后到期 → tvp = %d us\n", el.TvpUs)

	fmt.Println("\n== 4. 多线程 I/O ==")
	fmt.Printf("  IO_THREADS_MAX_NUM = %d\n", IoThreadsMaxNum)
	fmt.Printf("  拷贝规避阈值：>= %d 线程时不限长度；单线程 %d B；带线程 %d B\n",
		CopyAvoidMinIoThreads, CopyAvoidMinStringSize, CopyAvoidMinStringSizeThreaded)
	srv := &IoServer{IoThreadsNum: 4, ReplyCopyAvoidanceEnabled: 1}
	c := &IoClient{HasConn: true, ClientType: ClientTypeNormal}
	o := &IoRobj{Encoding: ObjEncodingRaw, Refcount: 1}
	fmt.Printf("  4 线程、16 KB → 走引用? %v\n", IsCopyAvoidPreferred(srv, c, o, 16384) == 1)
	fmt.Printf("  4 线程、64 KB → 走引用? %v\n", IsCopyAvoidPreferred(srv, c, o, 65536) == 1)
	fmt.Printf("  8 核建议 io-threads = %d（conf：4 核用 3、8 核用 7）\n", SuggestedIoThreads(8))
	fmt.Printf("  io-threads-do-reads 已废弃? %v\n", IsDeprecatedConfig("io-threads-do-reads"))
}
