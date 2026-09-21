package main

import "fmt"

var checks, failed int

func check(cond bool, label string) {
	checks++
	if !cond {
		failed++
		fmt.Println("FAIL:", label)
	}
}

func checkEq(got, want interface{}, label string) {
	checks++
	if fmt.Sprint(got) != fmt.Sprint(want) {
		failed++
		fmt.Printf("FAIL: %s (got=%v want=%v)\n", label, got, want)
	}
}

func fresh() (*Netpoll, *PollDesc) {
	return &Netpoll{}, &PollDesc{fd: 7, fdseq: 1}
}

func main() {
	// 1) 未就绪 → park → IO 就绪唤醒
	np, pd := fresh()
	checkEq(netpollblock(np, pd, modeR, false), false, "未就绪时 block 返回 false")
	check(pd.rg > pdWait, "信号量变成 G 指针")
	checkEq(np.Waiters, int32(1), "netpollWaiters +1")
	g := pd.rg
	toRun, delta := netpollready(np, pd, modeR)
	checkEq(len(toRun), 1, "唤醒 1 个 goroutine")
	checkEq(toRun[0], g, "唤醒的正是被挂起的那个")
	checkEq(delta, int32(-1), "delta = -1")
	checkEq(pd.rg, pdReady, "信号量变 pdReady")
	checkEq(np.Waiters, int32(0), "waiters 归零")

	// 2) 先就绪再等 → 消费通知，不阻塞
	np2, pd2 := fresh()
	pd2.rg = pdReady
	checkEq(netpollblock(np2, pd2, modeR, false), true, "已就绪时 block 返回 true")
	checkEq(pd2.rg, pdNil, "通知被消费回 pdNil")
	checkEq(len(pd2.parked), 0, "没有 park")

	// 3) 超时唤醒不是「就绪」
	np3, pd3 := fresh()
	netpollblock(np3, pd3, modeR, false)
	pollRuntimePollSetDeadline(np3, pd3, 5, 1000, modeR)
	checkEq(netpolldeadlineimpl(np3, pd3, pd3.rseq, true, false), true, "seq 匹配才生效")
	checkEq(pd3.rg, pdNil, "超时唤醒后是 pdNil 不是 pdReady")
	checkEq(netpollcheckerr(pd3, modeR), pollErrTimeout, "checkerr 返回 ErrTimeout")
	checkEq(pollRuntimePollWait(np3, pd3, modeR), pollErrTimeout, "pollWait 不再阻塞")

	// 4) 同一方向二次等待 → double wait
	np4, pd4 := fresh()
	netpollblock(np4, pd4, modeR, false)
	func() {
		defer func() {
			r := recover()
			check(fmt.Sprint(r) == "runtime: double wait", "二次等待 panic: double wait")
		}()
		netpollblock(np4, pd4, modeR, false)
	}()

	// 5) deadline 的 seq 失效机制
	np5, pd5 := fresh()
	pollRuntimePollSetDeadline(np5, pd5, 100, 1000, modeR)
	checkEq(pd5.rd, int64(1100), "deadline 是绝对时刻")
	old := pd5.rseq
	checkEq(netpolldeadlineimpl(np5, pd5, old-1, true, false), false, "旧 seq 被丢弃")
	checkEq(pd5.expiredRead, false, "被丢弃的 timer 不标记过期")
	pollRuntimePollSetDeadline(np5, pd5, 200, 1000, modeR)
	checkEq(pd5.rseq, old+1, "改 deadline 时 rseq++")

	// 6) 关闭：两边一起解阻塞
	np6, pd6 := fresh()
	netpollblock(np6, pd6, modeR, false)
	netpollblock(np6, pd6, modeW, false)
	checkEq(np6.Waiters, int32(2), "两个方向共 2 个 waiter")
	rg, wg := pollRuntimePollUnblock(np6, pd6)
	check(rg != 0 && wg != 0, "两边都被摘出")
	check(pd6.closing, "置 closing")
	checkEq(np6.Waiters, int32(0), "waiters 归零")
	checkEq(netpollcheckerr(pd6, modeR), pollErrClosing, "之后返回 ErrClosing")

	// 7) netpollBreak 的 CAS 去重
	np7, _ := fresh()
	checkEq(np7.netpollBreak(), true, "第一次 break 生效")
	checkEq(np7.netpollBreak(), false, "未消费前第二次被拦掉")

	fmt.Printf("checks=%d failed=%d\n", checks, failed)
	if failed > 0 {
		panic("selfcheck failed")
	}
}
