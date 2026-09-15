// main.go — SCM_RIGHTS / fd 传递 demo（Go）。
//
// 真实系统调用路径 —— Go 标准库把送控制消息的活拆成了三个函数：
//   syscall.UnixRights(fds...)          → 造出 SCM_RIGHTS 的 oob 字节串
//   syscall.Sendmsg(sock, data, oob, …) → 发（**必须**同时给 >=1 字节 data）
//   syscall.Recvmsg(...) + syscall.ParseSocketControlMessage + ParseUnixRights
// 注意 UnixRights/ParseUnixRights 按"int 为 4 字节"编码，只在 Linux 正确。
//
// 编译： cd go && go build -o scm_demo .
//        或直接 go run main.go scm_model.go
package main

import (
	"fmt"
	"os"
	"syscall"
)

var (
	nOk   int
	nFail int
)

func check(name string, cond bool, format string, args ...any) {
	if cond {
		nOk++
		fmt.Printf("  [ok]   %s\n", name)
		return
	}
	nFail++
	fmt.Printf("  [FAIL] %s  %s\n", name, fmt.Sprintf(format, args...))
}

// ---------------------------------------------------------------- 真实路径

// realSend 用真实 sendmsg 送 len(data) 字节 + n 个 fd。
func realSend(sock int, data []byte, fds []int, errnoOnly bool) error {
	oob := syscall.UnixRights(fds...)
	if errnoOnly {
		oob = nil
	}
	return syscall.Sendmsg(sock, data, oob, nil, 0)
}

// realRecv 收一次；返回数据、fd 列表、msg_flags。
func realRecv(sock int, bufSize, ctrlBufSize int, flags int) ([]byte, []int, int, error) {
	buf := make([]byte, bufSize)
	oob := make([]byte, ctrlBufSize)
	n, oobn, recvFlags, _, err := syscall.Recvmsg(sock, buf, oob, flags)
	if err != nil {
		return nil, nil, 0, err
	}
	var fds []int
	if oobn > 0 {
		msgs, perr := syscall.ParseSocketControlMessage(oob[:oobn])
		if perr != nil {
			return nil, nil, recvFlags, perr
		}
		for i := range msgs {
			got, perr := syscall.ParseUnixRights(&msgs[i])
			if perr == nil {
				fds = append(fds, got...)
			}
		}
	}
	return buf[:n], fds, recvFlags, nil
}

// scenarioIdentity 证明"传的是 open file description 的引用"。
func scenarioLimits() {
	fmt.Printf("\n=== 2) SCM_MAX_FD=%d（<2.6.38 为 %d）；3) 必须带真实数据；7) EBADF ===\n",
		scmMaxFd, scmMaxFdOld)
	pair, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		check("socketpair", false, "%v", err)
		return
	}
	defer syscall.Close(pair[0])
	defer syscall.Close(pair[1])

	many := make([]int, 0, scmMaxFd+1)
	for i := 0; i < scmMaxFd+1; i++ {
		dup, derr := syscall.Dup(0)
		if derr != nil {
			check("准备 dup 的 fd", false, "%v", derr)
			return
		}
		many = append(many, dup)
	}
	if err := realSend(pair[0], []byte("!"), many[:scmMaxFd], false); err != nil {
		check("传 253 个 fd 成功", false, "%v", err)
	} else {
		_, fds, _, rerr := realRecv(pair[1], 8, fdsSpace(scmMaxFd), 0)
		fmt.Printf("  传 %d 个 fd：发送成功，接收方装好 %d 个（控制缓冲 %d B）\n",
			scmMaxFd, len(fds), fdsSpace(scmMaxFd))
		check("253 个 fd 可以一次性传完", rerr == nil && len(fds) == scmMaxFd,
			"got=%d err=%v", len(fds), rerr)
		for _, fd := range fds {
			syscall.Close(fd)
		}
	}
	err = realSend(pair[0], []byte("!"), many, false)
	fmt.Printf("  传 %d 个 fd：sendmsg → %v\n", len(many), err)
	check("254 个 fd 报 EINVAL", err == syscall.EINVAL, "err=%v", err)

	err = realSend(pair[0], nil, many[:1], false)
	fmt.Printf("  流式 socket sendmsg(data=0 B, fd) → %v\n", err)
	check("流式 socket 传 0 字节报 EINVAL", err == syscall.EINVAL, "err=%v", err)

	err = realSend(pair[0], []byte("!"), []int{99999}, false)
	fmt.Printf("  sendmsg(fd=99999，本进程没有) → %v\n", err)
	check("发送无效 fd 报 EBADF", err == syscall.EBADF, "err=%v", err)
}

// scenarioTruncation 控制缓冲过小 → MSG_CTRUNC，多余的 fd 由内核在接收方关闭。
func scenarioTruncation() {
	fmt.Println("\n=== 4) CMSG_SPACE vs CMSG_LEN；缓冲过小 → MSG_CTRUNC ===")
	fmt.Printf("  1 个 fd: CMSG_LEN(4)=%d（写进 cmsg_len），CMSG_SPACE(4)=%d（要预留的缓冲）\n",
		cmsgLen(4), cmsgSpace(4))
	check("CMSG_SPACE 比 CMSG_LEN 多一个对齐填充", cmsgSpace(4)-cmsgLen(4) == 4,
		"%d vs %d", cmsgSpace(4), cmsgLen(4))
	fmt.Printf("  fdsFit(4,1)=%d  fdsFit(%d,1)=%d  fdsFit(%d,1)=%d\n",
		fdsFit(4, 1), cmsgLen(4), fdsFit(cmsgLen(4), 1), cmsgSpace(4), fdsFit(cmsgSpace(4), 1))
	check("按 sizeof(int)=4 预留 → 一个都收不到", fdsFit(4, 1) == 0, "")
	check("按 CMSG_LEN=20 预留 → 仍然收不到", fdsFit(cmsgLen(4), 1) == 0, "")
	check("按 CMSG_SPACE=24 预留 → 正好收到 1 个", fdsFit(cmsgSpace(4), 1) == 1, "")
	fmt.Printf("  ⚠ 对齐粒度：CMSG_SPACE(4)=%d 与 CMSG_SPACE(8)=%d 相等 → "+
		"1 个和 2 个 fd 占的缓冲一样大\n", cmsgSpace(4), cmsgSpace(8))
	check("按 CMSG_SPACE(4*2)=24 预留其实能装 2 个", fdsFit(cmsgSpace(4), 2) == 2, "")

	pair, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		check("socketpair", false, "%v", err)
		return
	}
	defer syscall.Close(pair[0])
	defer syscall.Close(pair[1])
	three := make([]int, 0, 3)
	for i := 0; i < 3; i++ {
		dup, _ := syscall.Dup(0)
		three = append(three, dup)
	}
	if err := realSend(pair[0], []byte("!"), three, false); err != nil {
		check("发送 3 个 fd", false, "%v", err)
		return
	}
	_, fds, recvFlags, err := realRecv(pair[1], 8, cmsgSpace(4), 0)
	fmt.Printf("  发送 3 个 fd、控制缓冲只给 %d B → 收到 %d 个，MSG_CTRUNC=%v\n",
		cmsgSpace(4), len(fds), recvFlags&syscall.MSG_CTRUNC != 0)
	check("多余 fd 触发 MSG_CTRUNC", recvFlags&syscall.MSG_CTRUNC != 0, "flags=%d", recvFlags)
	check("只装下 2 个 fd", len(fds) == 2, "got=%d err=%v", len(fds), err)
	fmt.Println("  ← 被截断的那个 fd 由内核在接收进程里自动关闭（不会退回发送方）")
	for _, fd := range fds {
		syscall.Close(fd)
	}
	for _, fd := range three {
		syscall.Close(fd)
	}
}

// scenarioBarrier 屏障 + MSG_CMSG_CLOEXEC。
func scenarioBarrier() {
	fmt.Println("\n=== 5) 控制数据是屏障；6) MSG_CMSG_CLOEXEC ===")
	pair, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		check("socketpair", false, "%v", err)
		return
	}
	defer syscall.Close(pair[0])
	defer syscall.Close(pair[1])
	fd, _ := syscall.Dup(0)
	defer syscall.Close(fd)

	syscall.Write(pair[0], []byte("AAAA"))
	if err := realSend(pair[0], []byte("B"), []int{fd}, false); err != nil {
		check("sendmsg 中段", false, "%v", err)
		return
	}
	syscall.Write(pair[0], []byte("CCCC"))

	data, fds, _, err := realRecv(pair[1], 20, 256, syscall.MSG_CMSG_CLOEXEC)
	if err != nil {
		check("第 1 次 recvmsg", false, "%v", err)
		return
	}
	fmt.Printf("  第 1 次 recvmsg(buf=20) → %q + %d 个 fd\n", data, len(fds))
	check("一次拿到 5 字节（前 4 + 带控制数据的 1）",
		string(data) == "AAAAB" && len(fds) == 1, "data=%q fds=%v", data, fds)
	if len(fds) == 1 {
		flags, _, ferr := syscall.Syscall(syscall.SYS_FCNTL, uintptr(fds[0]), syscall.F_GETFD, 0)
		fmt.Printf("  带 MSG_CMSG_CLOEXEC 收到的 fd：FD_CLOEXEC=%v\n",
			ferr == 0 && flags&syscall.FD_CLOEXEC != 0)
		check("MSG_CMSG_CLOEXEC 原子地设好 FD_CLOEXEC",
			ferr == 0 && flags&syscall.FD_CLOEXEC != 0, "flags=%d err=%v", flags, ferr)
		syscall.Close(fds[0])
	}
	data2, _, _, err := realRecv(pair[1], 20, 256, 0)
	if err != nil {
		check("第 2 次 recvmsg", false, "%v", err)
		return
	}
	fmt.Printf("  第 2 次 recvmsg(buf=20) → %q（屏障挡住了这 4 字节）\n", data2)
	check("屏障挡住后面那段：第 2 次才拿到 CCCC", string(data2) == "CCCC", "got=%q", data2)
}

// scenarioModel 纯模型部分：在途 fd 记账 + RLIMIT_NOFILE（真实内核上很难稳定复现）。
func scenarioModel() {
	fmt.Println("\n=== 8) 纯模型：在途 fd 记账（ETOOMANYREFS）与接收方 RLIMIT ===")
	sender := newFdTable("sender", 8)
	sock, peer := newPair("stream")
	var lastErr error
	alwaysEmpty := true
	for i := 1; i <= 12; i++ {
		ofd := newOFD("file", []byte("f"))
		fd, _ := sender.install(ofd, false)
		if _, err := sock.sendmsg(sender, []byte("!"), []int{fd}, false); err != nil {
			lastErr = err
			sender.close(fd)
			break
		}
		sender.close(fd) // 发完立刻 close：旧内核靠这招绕过 RLIMIT_NOFILE
		if sender.size() != 0 || peer.pendingFds() != i {
			alwaysEmpty = false
		}
	}
	fmt.Printf("  发送方 fd 表恒空、在途累积到 %d 后 → %v\n", peer.pendingFds(), lastErr)
	check("在途 fd 超限报 ETOOMANYREFS",
		lastErr != nil && lastErr.(*scmError).errno == "ETOOMANYREFS", "err=%v", lastErr)
	check("旧内核漏洞的成因：本进程表恒空而在途持续累积", alwaysEmpty, "")

	recv := newFdTable("receiver", 3)
	recv.install(newOFD("file", []byte("occupied")), false)
	out := peer.recvmsg(recv, 8, fdsSpace(12), 0)
	fmt.Printf("  接收方 RLIMIT_NOFILE=3（已用 1，在途 8）→ 装好 %d 个，"
		"因限流自动关闭 %d 个，recvmsg 本身没报错\n", len(out.fds), out.droppedRlim)
	check("装下的数量不超过剩余槽位", len(out.fds) <= 2, "got=%d", len(out.fds))
	check("超限的 fd 被自动关闭（且不置 MSG_CTRUNC —— man 页未规定）",
		out.droppedRlim >= 1 && out.msgFlags&msgCtrunc == 0, "drop=%d", out.droppedRlim)
}

func main() {
	fmt.Println("SCM_RIGHTS / fd 传递 demo —— Go（syscall.UnixRights 真实路径）\n")
	if syscall.Gettid() < 0 {
		fmt.Println("（当前平台不支持）")
		return
	}
	scenarioIdentity()
	scenarioLimits()
	scenarioTruncation()
	scenarioBarrier()
	scenarioModel()
	fmt.Printf("\n===== 断言结果: %d/%d 通过，%d 失败 =====\n", nOk, nOk+nFail, nFail)
	if nFail > 0 {
		os.Exit(1)
	}
}
