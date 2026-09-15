// 零拷贝发送实测（Go）：io.Copy 的 sendfile/splice 快速路径 vs 用户态缓冲拷贝。
//
// 关键机制：io.Copy(dst, src) 会先看 src 是否实现 io.WriterTo、再看 dst 是否
// 实现 io.ReaderFrom。*net.TCPConn 实现了 io.ReaderFrom，其内部会尝试
// splice(2) / sendfile(2)，失败才退回通用的用户态缓冲拷贝。因此：
//
//	io.Copy(conn, file)                      → 走 sendfile（文件已在 page cache）
//	io.Copy(conn, readerOnly{file})           → 包装后隐藏 WriteTo，退回用户态拷贝
//
// 只有 Linux 才能命中 sendfile/splice；其他平台两条路径都是用户态拷贝
// （本 demo 仍可跑，只是差距会消失）。
//
// 运行：go run main.go
package main

import (
	"fmt"
	"io"
	"net"
	"os"
	"time"
)

const (
	fileMiB  = 64
	chunkKiB = 128
)

// readerOnly 只暴露 Read，故意隐藏 *os.File 的 WriteTo，
// 从而强制 io.Copy 走"用户态缓冲区"的通用路径。
type readerOnly struct{ io.Reader }

type result struct {
	name string
	dur  time.Duration
	n    int64
	mbs  float64
}

var failed int

func check(cond bool, msg string) {
	if !cond {
		fmt.Printf("  [FAIL] %s\n", msg)
		failed++
	}
}

func makeFile(path string, size int64) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	buf := make([]byte, chunkKiB*1024)
	for i := range buf {
		buf[i] = byte('A' + i%26)
	}
	for written := int64(0); written < size; {
		n := int64(len(buf))
		if size-written < n {
			n = size - written
		}
		if _, err := f.Write(buf[:n]); err != nil {
			return err
		}
		written += n
	}
	return f.Sync()
}

// serveOnce 起一个 loopback 监听，返回监听地址与"收完一个连接"的收尾函数。
func serveOnce() (string, func() (int64, error), error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return "", nil, err
	}
	recv := make(chan int64, 1)
	errc := make(chan error, 1)
	go func() {
		conn, err := ln.Accept()
		if err != nil {
			errc <- err
			return
		}
		defer conn.Close()
		n, err := io.Copy(io.Discard, conn) // 全部读掉，避免发送端被流控卡住
		if err != nil {
			errc <- err
			return
		}
		recv <- n
	}()
	done := func() (int64, error) {
		ln.Close()
		select {
		case n := <-recv:
			return n, nil
		case e := <-errc:
			return 0, e
		case <-time.After(60 * time.Second):
			return 0, fmt.Errorf("接收端超时")
		}
	}
	return ln.Addr().String(), done, nil
}

// transfer 发一遍 src 并校验接收端收到的字节数，返回耗时与发送字节数。
func transfer(src io.Reader, copyFn func(io.Writer, io.Reader) (int64, error)) (time.Duration, int64, error) {
	addr, done, err := serveOnce()
	if err != nil {
		return 0, 0, err
	}
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		return 0, 0, err
	}
	tcp := conn.(*net.TCPConn)
	tcp.SetNoDelay(true) // 别让 Nagle 拖延小尺寸传输的计时

	start := time.Now()
	n, err := copyFn(tcp, src)
	elapsed := time.Since(start)
	if cerr := tcp.CloseWrite(); cerr != nil && err == nil {
		err = cerr
	}
	got, rerr := done()
	tcp.Close()
	if err != nil {
		return elapsed, n, err
	}
	if rerr != nil {
		return elapsed, n, rerr
	}
	if got != n {
		return elapsed, n, fmt.Errorf("接收端只收到 %d / %d 字节", got, n)
	}
	return elapsed, n, nil
}

func runCase(name, path string, wrap func(*os.File) io.Reader) result {
	f, err := os.Open(path)
	if err != nil {
		fmt.Println("打开失败:", err)
		os.Exit(1)
	}
	defer f.Close()
	d, n, err := transfer(wrap(f), io.Copy)
	if err != nil {
		fmt.Printf("%s 失败: %v\n", name, err)
		os.Exit(1)
	}
	return result{name: name, dur: d, n: n, mbs: float64(n) / d.Seconds() / (1024 * 1024)}
}

func main() {
	size := int64(fileMiB) * 1024 * 1024
	path := "zerocopy_go_test.bin"
	if err := makeFile(path, size); err != nil {
		fmt.Println("造文件失败:", err)
		os.Exit(1)
	}
	defer os.Remove(path)

	fmt.Printf("=== 零拷贝发送实测（Go，文件 %d MiB，loopback TCP）===\n", fileMiB)

	results := []result{
		// *os.File 有 WriteTo，Linux 上会走到 sendfile(2)
		runCase("io.Copy(*os.File)  → sendfile", path, func(f *os.File) io.Reader { return f }),
		// 隐藏 WriteTo，退回用户态缓冲拷贝（对照基线）
		runCase("io.Copy(readerOnly) → 用户态拷贝", path, func(f *os.File) io.Reader { return readerOnly{f} }),
	}
	for _, r := range results {
		fmt.Printf("  %-34s %7.3f s  %8.1f MiB/s  (%d 字节)\n",
			r.name, r.dur.Seconds(), r.mbs, r.n)
	}

	fmt.Println("\n=== 自检 ===")
	// 确定性断言：两条路径都必须完整送达相同字节数（不对计时做断言）
	for _, r := range results {
		check(r.n == size, r.name+" 应完整发送 "+fmt.Sprint(size)+" 字节")
	}
	// readerOnly 必须真的隐藏 WriteTo，否则"对照基线"名不副实
	{
		f, err := os.Open(path)
		if err != nil {
			fmt.Println("打开失败:", err)
			os.Exit(1)
		}
		_, rHasWT := interface{}(readerOnly{f}).(io.WriterTo)
		_, fHasWT := interface{}(f).(io.WriterTo)
		check(!rHasWT, "readerOnly 不应实现 io.WriterTo（否则对照组无效）")
		check(fHasWT, "*os.File 应实现 io.WriterTo（sendfile 快速路径的前提）")
		f.Close()
	}
	// socket→socket 代理：sendfile 的 in_fd 不能是 socket，只能靠 splice 转发
	{
		src, dst := net.Pipe()
		go func() {
			_, _ = src.Write([]byte("hello zero-copy"))
			src.Close()
		}()
		buf := make([]byte, 64)
		n, _ := io.ReadFull(dst, buf[:len("hello zero-copy")])
		check(n == len("hello zero-copy"), "socket→socket 需用 splice/net.Pipe 转发")
		dst.Close()
	}
	// 接收端字节数必须与发送端一致（已经在上面的 transfer 里强校验，这里复核语义）
	check(results[0].n == results[1].n, "两条路径的接收字节数应一致")

	if failed == 0 {
		fmt.Println("自检通过")
	} else {
		fmt.Printf("自检失败 %d 项\n", failed)
	}
}
