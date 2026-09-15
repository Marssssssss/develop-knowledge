// real_identity.go — 场景 1：证明传的是 open file description 的引用（从 main.go 拆出）。
//
// 同 package main，零语义变化。用法与 main.go 里的注释一致：
// go run main.go cc_model.go real_identity.go ……（本包所有文件）。
package main

import (
	"fmt"
	"os"
	"syscall"
)

func scenarioIdentity() {
	fmt.Println("=== 1) 传的是 open file description 的引用（等价 dup），不是 fd 号 ===")
	f, err := os.CreateTemp("", "scm_demo_")
	if err != nil {
		check("建临时文件", false, "%v", err)
		return
	}
	defer os.Remove(f.Name())
	defer f.Close()
	payload := []byte("HELLO-WORLD-0123456789")
	if _, err := f.Write(payload); err != nil {
		check("写入 fixture", false, "%v", err)
		return
	}
	if _, err := f.Seek(0, 0); err != nil {
		check("seek 回 0", false, "%v", err)
		return
	}
	head := make([]byte, 5)
	if _, err := f.Read(head); err != nil {
		check("发送方先读 5 字节", false, "%v", err)
		return
	}
	fmt.Printf("  发送方先读 5 字节: %s  → 偏移 %d\n", head, offsetOf(int(f.Fd())))

	pair, err := syscall.Socketpair(syscall.AF_UNIX, syscall.SOCK_STREAM, 0)
	if err != nil {
		check("socketpair", false, "%v", err)
		return
	}
	defer syscall.Close(pair[0])
	defer syscall.Close(pair[1])

	if err := realSend(pair[0], []byte("!"), []int{int(f.Fd())}, false); err != nil {
		check("sendmsg 一个 fd", false, "%v", err)
		return
	}
	data, fds, _, err := realRecv(pair[1], 16, 256, 0)
	if err != nil {
		check("recvmsg 收到 fd", false, "%v", err)
		return
	}
	fmt.Printf("  接收方 recvmsg → data=%q fds=%v（原编号 %d）\n", data, fds, int(f.Fd()))
	check("确实收到了一个 fd", len(fds) == 1, "fds=%v", fds)
	if len(fds) == 0 {
		return
	}
	recvFile := os.NewFile(uintptr(fds[0]), "received")
	defer recvFile.Close()
	off := offsetOf(fds[0])
	fmt.Printf("  接收方刚拿到时偏移就已是 %d ← 与发送方共享同一个 OFD\n", off)
	check("偏移由发送方那边推进过，说明是同一个 open file description", off == 5, "off=%d", off)
	tail := make([]byte, 5)
	if _, err := recvFile.Read(tail); err != nil {
		check("接收方接着读", false, "%v", err)
		return
	}
	fmt.Printf("  接收方接着读 5 字节: %s\n", tail)
	check("读到偏移 5 之后的内容（-WORL）", string(tail) == "-WORL", "got=%s", tail)

	// 对照：按路径自己 open → 独立 OFD，偏移从 0 开始
	fresh, err := os.Open(f.Name())
	if err != nil {
		check("按路径 re-open", false, "%v", err)
		return
	}
	defer fresh.Close()
	freshHead := make([]byte, 5)
	_, _ = fresh.Read(freshHead)
	fmt.Printf("  对照：按路径自己 open 再读 5 字节: %s  → 偏移 %d\n",
		freshHead, offsetOf(int(fresh.Fd())))
	check("re-open 得到独立 OFD（偏移从 0 起，内容不同）",
		string(freshHead) == "HELLO", "got=%s", freshHead)
}

func offsetOf(fd int) int64 {
	off, err := syscall.Seek(fd, 0, 1) // SEEK_CUR
	if err != nil {
		return -1
	}
	return off
}

// scenarioLimits SCM_MAX_FD / 无真实数据 / 无效 fd。
