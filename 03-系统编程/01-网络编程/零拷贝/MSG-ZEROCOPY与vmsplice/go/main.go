package main

import "fmt"

var scCount int
var scFails []string

func ok(cond bool, msg string) {
	scCount++
	if !cond {
		scFails = append(scFails, msg)
	}
}

func eq(got, want int, msg string)   { ok(got == want, msg) }
func eqi(got, want int64, msg string) { ok(got == want, msg) }

func selfcheck() (int, []string) {
	scCount, scFails = 0, nil

	// 必须先声明意图
	s := &ZerocopySocket{}
	ret, ctr, got := s.Send(4096, true, false)
	eq(ret, 4096, "未开 SO_ZEROCOPY 仍发送成功")
	ok(!got, "未开 SO_ZEROCOPY 不分配 counter")
	ok(ctr == 0, "counter 仍为 0")
	s.ZcEnabled = true
	_, ctr, got = s.Send(4096, true, false)
	ok(got, "开了之后分配 counter")
	eqi(ctr, 1, "第一个 counter 是 1")
	_, _, got = s.Send(4096, false, false)
	ok(!got, "不带 flag 走普通拷贝")

	// counter 三条规则
	c := &ZerocopySocket{ZcEnabled: true}
	_, ctr, _ = c.Send(100000, true, false)
	eqi(ctr, 1, "按调用计数")
	_, ctr, _ = c.Send(1, true, false)
	eqi(ctr, 2, "1 字节也只加 1")
	_, _, got = c.Send(0, true, false)
	ok(!got, "length=0 不增")
	ret, _, _ = c.Send(4096, true, true)
	eq(ret, -eNoBufs, "ENOBUFS")
	_, ctr, _ = c.Send(4096, true, false)
	eqi(ctr, 3, "失败不占用号")

	// 回绕
	w := &ZerocopySocket{ZcEnabled: true, Counter: uint32Max - 1}
	_, ctr, _ = w.Send(1, true, false)
	eqi(ctr, uint32Max, "递增到 UINT_MAX")
	_, ctr, _ = w.Send(1, true, false)
	eqi(ctr, 0, "回绕到 0")

	// 合并
	q := &ZerocopySocket{ZcEnabled: true}
	ok(q.Complete(1, nil), "首条新包")
	ok(!q.Complete(2, nil), "第二条合并")
	eq(q.Outstanding(), 1, "仍只有 1 条")
	n, have := q.RecvErrqueue()
	ok(have, "取到通知")
	eqi(n.Info, 1, "ee_info")
	eqi(n.Data, 2, "ee_data 闭区间")
	eq(n.Errno, 0, "ee_errno 恒 0")
	eq(n.Origin, soEeOriginZerocopy, "ee_origin")
	eq(n.Code, 0, "ee_code")

	r := &ZerocopySocket{ZcEnabled: true}
	r.Complete(1, nil)
	ok(r.Complete(4, nil), "跳号不合并")
	eq(r.Outstanding(), 2, "两条")

	wr := &ZerocopySocket{ZcEnabled: true}
	wr.Complete(uint32Max, nil)
	ok(!wr.Complete(0, nil), "UINT_MAX -> 0 仍算连续")

	// loopback 必定拷贝
	lb := &ZerocopySocket{ZcEnabled: true, Loopback: true}
	lb.Complete(1, nil)
	n, _ = lb.RecvErrqueue()
	eq(n.Code, soEeCodeZerocopyCopied, "loopback -> COPIED")
	no := &ZerocopySocket{ZcEnabled: true}
	no.Complete(1, nil)
	n, _ = no.RecvErrqueue()
	eq(n.Code, 0, "非 loopback -> 0")

	// 量级
	ok(ZerocopyIsWorthIt(65536), "64 KiB 值得")
	ok(!ZerocopyIsWorthIt(1024), "1 KiB 不值得")
	eq(iovMax, 1024, "IOV_MAX")

	// vmsplice
	v, e := Vmsplice(false, []Iovec{{0, 8}}, 0, false)
	eq(v, -1, "非 pipe 返回 -1")
	eq(e, eBadFd, "EBADF")
	big := make([]Iovec, iovMax+1)
	_, e = Vmsplice(true, big, 0, false)
	eq(e, eInval, "nr_segs 超限")
	_, e = Vmsplice(true, []Iovec{{pageSize + 1, pageSize}}, spliceFGift, false)
	eq(e, eInval, "GIFT 基址未对齐")
	_, e = Vmsplice(true, []Iovec{{pageSize, pageSize - 1}}, spliceFGift, false)
	eq(e, eInval, "GIFT 长度未对齐")
	v, e = Vmsplice(true, []Iovec{{pageSize, pageSize}}, spliceFGift, false)
	eq(v, pageSize, "GIFT 对齐成功")
	eq(e, 0, "errno 0")
	v, _ = Vmsplice(true, []Iovec{{pageSize + 1, 7}}, 0, false)
	eq(v, 7, "不带 GIFT 不校验对齐")
	_, e = Vmsplice(true, []Iovec{{0, 8}}, spliceFNonblock, true)
	eq(e, eAgain, "会阻塞 -> EAGAIN")

	eq(VmspliceDirection(true), "splice", "写端 splice")
	eq(VmspliceDirection(false), "copy", "读端 copy")

	return scCount, scFails
}

func main() {
	fmt.Println("== Go 侧自检 ==")
	n, fails := selfcheck()
	fmt.Printf("  assertions=%d  fails=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL:", f)
	}

	fmt.Println("\n== 通知合并对照 ==")
	for _, vals := range [][]int64{{1, 2, 3}, {1, 2, 4}, {uint32Max, 0}} {
		q := &ZerocopySocket{ZcEnabled: true}
		marks := []string{}
		for _, v := range vals {
			if q.Complete(v, nil) {
				marks = append(marks, "新包")
			} else {
				marks = append(marks, "合并")
			}
		}
		outs := []string{}
		for q.Outstanding() > 0 {
			e, _ := q.RecvErrqueue()
			outs = append(outs, fmt.Sprintf("[%d,%d]", e.Info, e.Data))
		}
		fmt.Printf("  %-24v -> %-12v -> %v\n", vals, marks, outs)
	}
}
