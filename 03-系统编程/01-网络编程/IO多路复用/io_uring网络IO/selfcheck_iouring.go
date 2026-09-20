package main

import "fmt"

// ---------------------------------------------------------------- 自检

var nAssert, nFail int

func check(label string, cond bool, detail string) {
	nAssert++
	if cond {
		fmt.Printf("ok   %-52s %s\n", label, detail)
		return
	}
	nFail++
	fmt.Printf("FAIL %-52s %s\n", label, detail)
}

func main() {
	fmt.Println("== 每个 SQE 恰好一个 CQE ==")
	r := NewRing(8, 0, 0)
	for i := 0; i < 5; i++ {
		s, _ := r.GetSqe(OpSend, 10, i)
		s.Payload = []byte("xxxxxxxxxx")
		r.Prep(s)
	}
	n := r.Submit(0, 0)
	check("一次 enter 提交 5 个", n == 5, fmt.Sprintf("n=%d", n))
	cqes := r.ReapAll()
	check("CQE 数 == SQE 数", len(cqes) == 5, fmt.Sprintf("cqe=%d", len(cqes)))
	check("只用了 1 次系统调用", r.Syscalls == 1, fmt.Sprintf("%d", r.Syscalls))

	fmt.Println("\n== res 语义 ==")
	check("成功 res = 字节数", cqes[0].Res == 10 && cqes[0].Ok(), fmt.Sprintf("res=%d", cqes[0].Res))
	check("成功时 errno 恒为 0", cqes[0].Errno() == 0, "")
	r2 := NewRing(4, 0, 0)
	r2.Socks[10] = &Sock{Fd: 10, Reset: true}
	s2, _ := r2.GetSqe(OpSend, 10, 7)
	r2.Prep(s2)
	r2.Submit(0, 0)
	bad := r2.ReapAll()[0]
	check("reset 时 res = -ECONNRESET", bad.Res == -EConnReset, fmt.Sprintf("res=%d", bad.Res))
	check("errno 由 -res 还原", bad.Errno() == EConnReset, fmt.Sprintf("%d", bad.Errno()))

	fmt.Println("\n== 完成顺序不可假设 ==")
	r3 := NewRing(8, 0, 0)
	r3.Reverse = true
	for i := 0; i < 4; i++ {
		s, _ := r3.GetSqe(OpSend, 10, i)
		r3.Prep(s)
	}
	r3.Submit(0, 0)
	got := []int{}
	for _, c := range r3.ReapAll() {
		got = append(got, c.UserData)
	}
	check("负向：CQE 顺序不等于提交顺序", fmt.Sprint(got) != fmt.Sprint([]int{0, 1, 2, 3}),
		fmt.Sprint(got))
	sorted := append([]int{}, got...)
	for i := 0; i < len(sorted); i++ {
		for j := i + 1; j < len(sorted); j++ {
			if sorted[j] < sorted[i] {
				sorted[i], sorted[j] = sorted[j], sorted[i]
			}
		}
	}
	check("但 user_data 集合完整", fmt.Sprint(sorted) == fmt.Sprint([]int{0, 1, 2, 3}),
		fmt.Sprint(sorted))

	fmt.Println("\n== IOSQE_IO_LINK 强制顺序 ==")
	r4 := NewRing(8, 0, 0)
	r4.Reverse = true
	for i := 0; i < 4; i++ {
		s, _ := r4.GetSqe(OpSend, 10, i)
		s.Flags |= IosqeIoLink
		r4.Prep(s)
	}
	r4.Submit(0, 0)
	check("链接后执行顺序 == 提交顺序",
		fmt.Sprint(r4.ExecLog) == fmt.Sprint([]int{0, 1, 2, 3}), fmt.Sprint(r4.ExecLog))
	c4 := []int{}
	for _, c := range r4.ReapAll() {
		c4 = append(c4, c.UserData)
	}
	check("链接后完成顺序也保持", fmt.Sprint(c4) == fmt.Sprint([]int{0, 1, 2, 3}), fmt.Sprint(c4))

	fmt.Println("\n== 挂起操作 ==")
	r7 := NewRing(8, 0, 0)
	s7, _ := r7.GetSqe(OpRecv, 11, 42)
	s7.BufLen = 64
	r7.Prep(s7)
	r7.Submit(0, 0)
	check("无数据时没有 CQE", len(r7.Cq) == 0, "")
	check("处于挂起态", len(r7.Pending) == 1, "")
	r7.Feed(11, []byte("hello"))
	c7 := r7.ReapAll()
	check("数据到达后补出 CQE", len(c7) == 1 && c7[0].Res == 5, "")
	check("user_data 一致", c7[0].UserData == 42, "")

	fmt.Println("\n== SQPOLL ==")
	r8 := NewRing(8, IoringSetupSqPoll, 1.0)
	r8.Tick(2.0)
	check("空闲超时后线程睡了", r8.ThreadAsleep, "")
	check("置起 NEED_WAKEUP", r8.NeedWakeup(), "")
	s8, _ := r8.GetSqe(OpSend, 10, 1)
	r8.Prep(s8)
	r8.Submit(0, 0)
	check("唤醒需一次 enter", r8.Syscalls == 1 && !r8.NeedWakeup(), "")
	r8.Tick(0.1)
	check("忙起来后不再置位", !r8.NeedWakeup(), "")
	r8b := NewRing(8, IoringSetupSqPoll, 1.0)
	s8b, _ := r8b.GetSqe(OpSend, 10, 5)
	r8b.Prep(s8b)
	np := r8b.SqPollTick()
	check("轮询线程醒着时零系统调用", np == 1 && r8b.Syscalls == 0,
		fmt.Sprintf("submitted=%d syscalls=%d", np, r8b.Syscalls))

	fmt.Println("\n== IOPOLL 不能用于网络 ==")
	r9 := NewRing(8, IoringSetupIoPoll, 0)
	_, err := r9.GetSqe(OpRecv, 10, 0)
	check("负向：iopoll 环上网络操作失败", err != nil, "")
	_, err2 := r9.GetSqe(OpRead, 3, 0)
	check("iopoll 允许 READ", err2 == nil, "")

	fmt.Printf("\n---- %d 项断言，失败 %d 项 ----\n", nAssert, nFail)
	if nFail > 0 {
		panic("有断言失败")
	}
	fmt.Println("ALL PASS")
}
