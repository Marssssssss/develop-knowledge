// mmap_demo.go — Go syscall.Mmap 的 4 个核心 demo
//
// 运行: go run mmap_demo.go
//
// 演示:
//   1. MAP_SHARED 写文件 + Msync(MS_SYNC)
//   2. MAP_PRIVATE 写时复制
//   3. 跨 mmap 对象共享同一文件(SHARED IPC)
//   4. mmap 后 truncate → bus error 边界
//
// 注意:Go 1.20+ 直接暴露 syscall.Mmap;为避免引第三方依赖,
//  本 demo 用 syscall.Mmap/Msync/Munmap(Unix only)。

//go:build unix

package main

import (
	"fmt"
	"os"
	"syscall"
	"unsafe"
)

const (
	pageSize = 4096
	pathF    = "data.bin"
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}

func demo1SharedWrite() {
	fmt.Println("\n=== demo 1: MAP_SHARED write + Msync ===")
	f, err := os.OpenFile(pathF, os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0644)
	must(err)
	must(f.Truncate(pageSize))
	_, err = f.Write([]byte("INIT____INIT____INIT____INIT____" +
		"INIT____INIT____INIT____INIT____" +
		"INIT____INIT____INIT____INIT____" +
		"INIT____INIT____INIT____INIT____"))
	must(err)

	b, err := syscall.Mmap(int(f.Fd()), 0, pageSize,
		syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	must(err)

	// 写脏页
	b[0] = 'A'
	b[1] = 'B'
	b[2] = 0
	fmt.Printf("in-memory b[0..3] = %q\n", b[:3])

	// Msync(MS_SYNC) 强制落盘
	must(syscall.Msync(b, syscall.MS_SYNC))
	fmt.Println("Msync(MS_SYNC) done")

	// 重新读磁盘文件
	f2, err := os.Open(pathF)
	must(err)
	buf := make([]byte, 7)
	_, err = f2.Read(buf)
	must(err)
	fmt.Printf("file on disk [0..7] = %q\n", buf)

	must(syscall.Munmap(b))
	f.Close()
	f2.Close()
}

func demo2PrivateCoW() {
	fmt.Println("\n=== demo 2: MAP_PRIVATE copy-on-write ===")
	f, err := os.Open(pathF)
	must(err)
	b, err := syscall.Mmap(int(f.Fd()), 0, pageSize,
		syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_PRIVATE)
	must(err)

	fmt.Printf("MAP_PRIVATE b[0..3] before write = %q\n", b[:3])
	b[0] = 'Z'
	fmt.Printf("after b[0]='Z', b[0..3] = %q\n", b[:3])

	// 磁盘仍应是 'AB'(PRIVATE 不回写)
	f2, err := os.Open(pathF)
	must(err)
	buf := make([]byte, 3)
	_, err = f2.Read(buf)
	must(err)
	fmt.Printf("disk still contains [0..2] = %q (no Z)\n", buf)
	f2.Close()

	must(syscall.Munmap(b))
	f.Close()
}

func demo3SharedIPC() {
	fmt.Println("\n=== demo 3: cross-mmap object sharing via same file ===")
	tmpf, err := os.CreateTemp("", "ipc_*.bin")
	must(err)
	tmpName := tmpf.Name()
	tmpf.Close()
	defer os.Remove(tmpName)

	// 初始化 1 页全 0
	f1, err := os.OpenFile(tmpName, os.O_RDWR, 0644)
	must(err)
	must(f1.Truncate(pageSize))
	m1, err := syscall.Mmap(int(f1.Fd()), 0, pageSize,
		syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	must(err)

	// 第二个独立 mmap 对象,但指向同一文件同一 offset
	f2, err := os.OpenFile(tmpName, os.O_RDWR, 0644)
	must(err)
	m2, err := syscall.Mmap(int(f2.Fd()), 0, pageSize,
		syscall.PROT_READ, syscall.MAP_SHARED)
	must(err)

	// 写 m1
	m1[0] = 'D'
	m1[1] = 'E'
	m1[2] = 'A'
	m1[3] = 'D'
	must(syscall.Msync(m1, syscall.MS_SYNC))

	// m2 应看到(共享同一 page cache)
	fmt.Printf("writer m1[0:4]  = %q\n", m1[:4])
	fmt.Printf("reader m2[0:4]  = %q    (shared file-backed cache)\n", m2[:4])
	if string(m2[:4]) != "DEAD" {
		panic(fmt.Sprintf("shared mapping broken: got %q", m2[:4]))
	}

	must(syscall.Munmap(m1))
	must(syscall.Munmap(m2))
	f1.Close()
	f2.Close()
}

func demo4TruncateBusError() {
	fmt.Println("\n=== demo 4: SIGBUS on access beyond truncated size ===")
	f, err := os.OpenFile("trunc_demo.bin", os.O_RDWR|os.O_CREATE|os.O_TRUNC, 0644)
	must(err)
	must(f.Truncate(pageSize * 2))

	b, err := syscall.Mmap(int(f.Fd()), 0, pageSize*2,
		syscall.PROT_READ|syscall.PROT_WRITE, syscall.MAP_SHARED)
	must(err)
	b[0] = 'X'
	fmt.Printf("before truncate: b[0]='X' OK\n")

	// 截断到半页后访问第二页 → SIGBUS(在子进程里)
	must(f.Truncate(pageSize / 2))

	pid, _, err := syscall.Syscall(syscall.SYS_FORK, 0, 0, 0)
	if err != 0 {
		panic("fork failed")
	}
	if pid == 0 {
		// child
		fmt.Fprintln(os.Stderr, "child: accessing b[4096] (beyond new size) ...")
		_ = unsafe.Pointer(&b[pageSize])
		b[pageSize] = 'Y' // 触发 SIGBUS
		fmt.Fprintln(os.Stderr, "child: unexpectedly returned")
		os.Exit(0)
	}
	var status syscall.WaitStatus
	_, err = syscall.Wait4(int(pid), &status, 0, nil)
	must(err)
	if status.Signaled() && status.Signal() == syscall.SIGBUS {
		fmt.Println("parent: child killed by SIGBUS as expected ✓")
	} else {
		fmt.Printf("parent: child exited status=%d\n", status.ExitStatus())
	}

	must(syscall.Munmap(b))
	f.Close()
	os.Remove("trunc_demo.bin")
}

func main() {
	demo1SharedWrite()
	demo2PrivateCoW()
	demo3SharedIPC()
	demo4TruncateBusError()
	os.Remove(pathF)
	fmt.Println("\nall 4 demos done")
}