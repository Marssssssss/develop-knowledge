package main

import (
	"fmt"

	"dartloop"
)

func main() {
	fmt.Println("== 微任务：普通 vs 优先级 ==")
	var order []string
	rt := &dartloop.AsyncRuntime{}
	rt.ScheduleAsyncCallback(func() { order = append(order, "a") })
	rt.SchedulePriorityAsyncCallback(func() {
		order = append(order, "p1")
		rt.SchedulePriorityAsyncCallback(func() { order = append(order, "p2") })
		rt.ScheduleAsyncCallback(func() { order = append(order, "b") })
	})
	rt.RunEventLoop()
	fmt.Println("  执行顺序:", order, " immediate 次数:", rt.ScheduleCount)

	fmt.Println("== Timer 堆（5,3,9,1,7）==")
	h := dartloop.NewTimerHeap()
	for _, w := range []int{5, 3, 9, 1, 7} {
		h.Add(&dartloop.Timer{WakeupTime: w, ID: w})
	}
	var seq []int
	for h.Used > 0 {
		seq = append(seq, h.RemoveFirst().WakeupTime)
	}
	fmt.Println("  出队:", seq, " 容量:", len(h.List))

	fmt.Println("== 同时刻按 id 先进先出 ==")
	h2 := dartloop.NewTimerHeap()
	h2.Add(&dartloop.Timer{WakeupTime: 5, ID: 0})
	h2.Add(&dartloop.Timer{WakeupTime: 5, ID: 1})
	fmt.Printf("  id 顺序: %d, %d\n", h2.RemoveFirst().ID, h2.RemoveFirst().ID)

	fmt.Println("== 周期计时器逾期补偿（周期 100，逾期 250）==")
	wakeup, ms, now := 1100, 100, 1350
	tick := 0
	overdue := now - wakeup
	if overdue > ms {
		missed := overdue / ms
		wakeup += missed * ms
		tick += missed
	}
	tick++
	wakeup += ms // _advanceWakeupTime
	fmt.Printf("  tick=%d 下次唤醒=%d\n", tick, wakeup)
}
