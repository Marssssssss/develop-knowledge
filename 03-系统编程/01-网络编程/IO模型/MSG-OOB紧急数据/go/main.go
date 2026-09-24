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

func eq(got, want int, msg string)    { ok(got == want, msg) }
func eqi(got, want int64, msg string) { ok(got == want, msg) }

func selfcheck() (int, []string) {
	scCount, scFails = 0, nil

	ok(Before(1, 2), "before(1,2)")
	ok(!After(1, 2), "!after(1,2)")
	ok(!Before(1, 1), "!before(1,1)")
	ok(Before(0xFFFFFFFF, 0), "u32 回绕")
	eq(urgValid, 0x100, "TCP_URG_VALID")
	eq(urgNotYet, 0x200, "TCP_URG_NOTYET")
	eq(urgRead, 0x400, "TCP_URG_READ")

	// BSD 默认解释
	a := newUrgSock(100, 100, false, false)
	a.OnSegment(100, 1, []byte("ABC"), 5, 0)
	eqi(a.UrgSeq, 100, "默认解释 ptr=100")
	eq(a.UrgData, urgValid|0x41, "取到 'A'")
	eq(a.Sockatmark(), 1, "在标记处")
	a.RcvNxt = 103
	eqi(a.Inq(), 0, "SIOCINQ 被截断到 0")
	eqi(a.RecvLimit(100, 3), 0, "普通读上限 0")
	c, hole := a.ReadChunk(100, 3)
	eqi(c, 2, "非 inline 读到 2 字节")
	eq(hole, 1, "urg_hole=1")

	// URGINLINE 成对
	b := newUrgSock(100, 100, true, false)
	b.OnSegment(100, 1, []byte("ABC"), 5, 0)
	b.RcvNxt = 103
	eqi(b.Inq(), 3, "inline -> SIOCINQ 3")
	eqi(b.RecvLimit(100, 3), 0, "inline 也截断")
	c, hole = b.ReadChunk(100, 3)
	eqi(c, 3, "inline 读到 3 字节")
	eq(hole, 0, "inline 无洞")

	// RFC 1122 解释
	d := newUrgSock(100, 100, false, true)
	d.OnSegment(100, 1, []byte("ABC"), 5, 0)
	eqi(d.UrgSeq, 101, "stdurg ptr=101")
	eq(d.UrgData, urgValid|0x42, "取到 'B'")

	// 三条守卫
	e := newUrgSock(100, 105, false, false)
	eq(e.OnSegment(100, 1, []byte("ABC"), 5, 0).Result, "ignored_replay", "不回放")
	f := newUrgSock(100, 100, false, false)
	f.UrgData = urgNotYet
	f.UrgSeq = 102
	eq(f.OnSegment(100, 3, []byte("ABCDE"), 5, 0).Result, "ignored_duplicate", "旧指针")
	eq(f.OnSegment(100, 4, []byte("ABCDE"), 5, 0).Result, "accepted", "新指针(成对)")
	g := newUrgSock(200, 200, false, false)
	eq(g.OnSegment(100, 1, []byte("ABC"), 5, 0).Result, "ignored_already_read", "已读过")

	// Double Dutch
	h := newUrgSock(100, 103, false, false)
	h.UrgData = urgNotYet
	h.UrgSeq = 100
	eq(h.OnSegment(103, 1, []byte("XY"), 5, 0).Result, "accepted_advance_copied_seq", "推进")
	eqi(h.CopiedSeq, 101, "copied_seq 100->101")
	i := newUrgSock(100, 103, true, false)
	i.UrgData = urgNotYet
	i.UrgSeq = 100
	eq(i.OnSegment(103, 1, []byte("XY"), 5, 0).Result, "accepted", "inline 不推进")
	eqi(i.CopiedSeq, 100, "copied_seq 不动")

	// recv_urg 三态
	j := newUrgSock(100, 103, false, false)
	j.UrgData = urgValid | 0x41
	j.UrgSeq = 100
	ret, fl := j.RecvOob(1, false)
	eq(ret, 1, "读到 1 字节")
	eq(fl, msgOob, "msg_flags 带 MSG_OOB")
	eq(j.UrgData, urgRead, "读后 TCP_URG_READ")
	ret, _ = j.RecvOob(1, false)
	eq(ret, -eInval, "再读 -> EINVAL")
	k := newUrgSock(100, 103, false, false)
	k.UrgData = urgValid | 0x41
	k.UrgSeq = 100
	ret, _ = k.RecvOob(1, true)
	eq(ret, 1, "MSG_PEEK 能读")
	eq(k.UrgData, urgValid|0x41, "MSG_PEEK 不改状态")
	ret, fl = k.RecvOob(0, false)
	eq(ret, 0, "len=0 -> 0")
	eq(fl, msgOob|msgTrunc, "len=0 -> MSG_TRUNC")
	l := newUrgSock(100, 103, true, false)
	l.UrgData = urgValid | 0x41
	l.UrgSeq = 100
	ret, _ = l.RecvOob(1, false)
	eq(ret, -eInval, "URGINLINE -> EINVAL")
	n := newUrgSock(100, 103, false, false)
	n.UrgData = urgNotYet
	n.UrgSeq = 100
	ret, _ = n.RecvOob(1, false)
	eq(ret, 0, "NOTYET 返回 0 而非 EINVAL")

	// epoll
	p := newUrgSock(100, 103, false, false)
	p.UrgData = urgValid | 0x41
	p.UrgSeq = 100
	m, target := p.EpollMask(1)
	eq(m, epollPri, "EPOLLPRI")
	eq(target, 2, "rcvlowat +1")
	p.UrgData = urgNotYet
	m, _ = p.EpollMask(1)
	eq(m, 0, "仅 NOTYET 不给 EPOLLPRI")

	return scCount, scFails
}

func main() {
	fmt.Println("== Go 侧自检 ==")
	n, fails := selfcheck()
	fmt.Printf("  assertions=%d  fails=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL:", f)
	}

	fmt.Println("\n== 紧急指针两种解释 ==")
	for _, std := range []bool{false, true} {
		s := newUrgSock(100, 100, false, std)
		r := s.OnSegment(100, 1, []byte("ABC"), 5, 0)
		s.RcvNxt = 103
		name := "Linux 默认(BSD)"
		if std {
			name = "tcp_stdurg=1(RFC 1122)"
		}
		fmt.Printf("  %-24s ptr=%-4d byte=%q atmark=%d inq=%d\n",
			name, s.UrgSeq, r.Byte, s.Sockatmark(), s.Inq())
	}
}
