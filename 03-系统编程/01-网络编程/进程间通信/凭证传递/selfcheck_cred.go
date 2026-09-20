package main

// Unix socket 凭证传递 —— Go 版自检
//
// 与 selfcheck_cred.py 同判据。Go 版额外强调：Ucred 是值类型，
// 一旦从 PeerCred 取出就是一份独立拷贝，不会被后续 setuid 影响。

import "fmt"

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

func hasCred(cmsgs []Cmsg) []*Ucred {
	out := []*Ucred{}
	for i := range cmsgs {
		if cmsgs[i].Type == ScmCredentials {
			out = append(out, cmsgs[i].Cred)
		}
	}
	return out
}

func main() {
	client := &Proc{Pid: 1001, UID: 1000, GID: 1000}
	server := &Proc{Pid: 900, UID: 0, GID: 0}

	fmt.Println("== SO_PASSCRED 是开关 ==")
	a, b := SocketPair("stream", client, server)
	SendMsg(a, []byte("hello"), nil, 0)
	m1, _ := RecvMsg(b, 0)
	check("未开 SO_PASSCRED 时看不到凭证", len(hasCred(m1.Cmsgs)) == 0,
		fmt.Sprintf("n=%d", len(hasCred(m1.Cmsgs))))
	b.SetSockopt(SolSocket, SoPasscred, 1)
	SendMsg(a, []byte("world"), nil, 0)
	m2, _ := RecvMsg(b, 0)
	cs := hasCred(m2.Cmsgs)
	check("开启后立刻带上凭证", len(cs) == 1, fmt.Sprintf("n=%d", len(cs)))
	check("pid 是发送方的", cs[0].Pid == 1001, fmt.Sprintf("%+v", *cs[0]))
	check("uid 是发送方的 real uid", cs[0].UID == 1000, "")

	fmt.Println("\n== 显式携带 SCM_CREDENTIALS ==")
	a3, b3 := SocketPair("stream", client, server)
	b3.SetSockopt(SolSocket, SoPasscred, 1)
	stated := Ucred{Pid: 4242, UID: 7, GID: 8}
	SendMsg(a3, []byte("x"), []Cmsg{{Level: SolSocket,
		Type: ScmCredentials, Cred: &stated}}, 0)
	m3, _ := RecvMsg(b3, 0)
	g3 := hasCred(m3.Cmsgs)
	check("显式指定的凭证原样投递", len(g3) == 1 && *g3[0] == stated, fmt.Sprintf("%+v", *g3[0]))

	fmt.Println("\n== SCM_RIGHTS 与 SCM_CREDENTIALS 共存 ==")
	a4, b4 := SocketPair("stream", client, server)
	b4.SetSockopt(SolSocket, SoPasscred, 1)
	SendMsg(a4, []byte("fd"), []Cmsg{{Level: SolSocket,
		Type: ScmRights, Fds: []int{11, 12}}}, 0)
	m4, _ := RecvMsg(b4, 0)
	check("两种 cmsg 同在一条消息里", len(m4.Cmsgs) == 2, fmt.Sprintf("n=%d", len(m4.Cmsgs)))

	fmt.Println("\n== SO_PEERCRED 是 connect 时刻快照 ==")
	p1 := &Proc{Pid: 500, UID: 1000, GID: 1000}
	p2 := &Proc{Pid: 600, UID: 1000, GID: 1000}
	c1 := &Sock{Kind: "stream", Proc: p1}
	s1 := &Sock{Kind: "stream", Proc: p2}
	c1.Connect(s1)
	snap, _ := PeerCred(c1)
	check("拿到对端凭证", snap != nil && *snap == Ucred{600, 1000, 1000},
		fmt.Sprintf("%+v", *snap))
	p2.UID = 0
	snap2, _ := PeerCred(c1)
	check("负向：setuid 后快照不变", snap2.UID == 1000 && p2.UID == 0,
		fmt.Sprintf("snap.uid=%d proc.uid=%d", snap2.UID, p2.UID))

	fmt.Println("\n== SO_PEERCRED 适用范围 ==")
	f1 := &Proc{Pid: 500, UID: 1000, GID: 1000}
	f2 := &Proc{Pid: 600, UID: 1000, GID: 1000}
	d1 := &Sock{Kind: "dgram", Proc: f1}
	_, err := PeerCred(d1)
	check("负向：未连接 datagram 取不到", err != nil, "")
	spA, _ := SocketPair("dgram", f1, f2)
	pc, _ := PeerCred(spA)
	check("socketpair 的 dgram 端可以取", pc != nil && *pc == Ucred{600, 1000, 1000}, "")
	sp2A, _ := SocketPair("stream", f1, f2)
	pc2, _ := PeerCred(sp2A)
	check("socketpair 的 stream 端可以取", pc2 != nil && *pc2 == Ucred{600, 1000, 1000}, "")
	_, errSet := sp2A.SetSockopt(SolSocket, SoPeercred, 1)
	check("负向：SO_PEERCRED 不可写", errSet != nil, "")

	fmt.Println("\n== autobind ==")
	sa := &Sock{Kind: "stream", Proc: f1}
	sa.SetSockopt(SolSocket, SoPasscred, 1)
	check("设 SO_PASSCRED 后自动绑定抽象地址", sa.Abstract && len(sa.Addr) == 6,
		fmt.Sprintf("%q", sa.Addr))
	check("抽象地址以空字节开头", len(sa.Addr) > 0 && sa.Addr[0] == 0, "")
	check("autobind 上限 2^20", AutobindLimit == 1<<20, "")
	sb := &Sock{Kind: "stream", Proc: f1}
	sb.Bind("/tmp/x.sock", false)
	check("显式 bind 是文件系统路径", sb.Addr == "/tmp/x.sock" && !sb.Abstract, sb.Addr)

	fmt.Println("\n== AF_UNIX 能力边界 ==")
	a8, _ := SocketPair("stream", f1, f2)
	_, e1 := SendMsg(a8, []byte("oob"), nil, MsgOob)
	check("负向：不支持 MSG_OOB", e1 != nil, "")
	_, e2 := SendMsg(a8, []byte("more"), nil, MsgMore)
	check("负向：不支持 MSG_MORE", e2 != nil, "")
	okRcv, _ := a8.SetSockopt(SolSocket, SoRcvbuf, 99999)
	check("SO_RCVBUF 无效", !okRcv && a8.RcvBuf == 0, fmt.Sprintf("rcvbuf=%d", a8.RcvBuf))
	okSnd, _ := a8.SetSockopt(SolSocket, SoSndbuf, 4096)
	check("SO_SNDBUF 有效", okSnd && a8.SndBuf == 4096, fmt.Sprintf("%d", a8.SndBuf))

	fmt.Println("\n== 凭证是逐消息的 ==")
	srv := &Sock{Kind: "stream", Proc: server}
	srv.SetSockopt(SolSocket, SoPasscred, 1)
	for _, pid := range []int{7001, 7002, 7003} {
		cl := &Sock{Kind: "stream", Proc: &Proc{Pid: pid, UID: 1000 + pid, GID: 1000}}
		cl.Connect(srv)
		SendMsg(cl, []byte("ping"), nil, 0)
	}
	pids := []int{}
	for _, msg := range srv.Inbox {
		for _, c := range hasCred(msg.Cmsgs) {
			pids = append(pids, c.Pid)
		}
	}
	check("三条消息各带各发送方 pid",
		fmt.Sprint(pids) == fmt.Sprint([]int{7001, 7002, 7003}), fmt.Sprint(pids))

	fmt.Printf("\n---- %d 项断言，失败 %d 项 ----\n", nAssert, nFail)
	if nFail > 0 {
		panic("有断言失败")
	}
	fmt.Println("ALL PASS")
}
