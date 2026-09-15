// scatter-gather IO 与 TCP_CORK（Go 视角）
//
// Go 标准库对"聚集写"的答案是 net.Buffers：它实现了 io.WriterTo，其 WriteTo
// 在 Linux 上走 writev(2)——一次系统调用写出多块缓冲区，不需要用户态 memcpy 合并。
//
// 本 demo 对照四种"写响应"策略：
//
//	A. conn.Write(header) + conn.Write(body)      两次系统调用
//	B. 合并到用户态缓冲再一次 conn.Write           一次调用 + 一次全量 memcpy
//	C. net.Buffers{header, body}.WriteTo(conn)     一次调用，走 writev
//	D. TCP_CORK + 两次 Write + uncork              两次写被塞住，uncork 时一齐发出
//
// 顺带演示一个 Go 特有的坑：syscall 包**没有**导出 TCP_CORK（只有 TCP_NODELAY），
// 要用它必须自己按内核 uapi 头文件定义常量，或依赖 golang.org/x/sys/unix。
//
// 运行：go run main.go
package main

import (
	"bytes"
	"fmt"
	"io"
	"net"
	"os"
	"runtime"
	"syscall"
	"time"
)

const (
	headerLen = 180 // 典型 HTTP 响应头
	bodyLen   = 512 // 典型小响应体

	// Linux include/uapi/linux/tcp.h: TCP_NODELAY = 1, TCP_CORK = 3。
	// Go 的 syscall 包只导出了 TCP_NODELAY，TCP_CORK 得自己写。
	tcpCork = 3
)

var failed, checks int

func check(cond bool, msg string) {
	checks++
	if !cond {
		fmt.Printf("  [FAIL] %s\n", msg)
		failed++
	}
}

// serveOne 起一个 loopback 监听，返回地址与"收完一个连接"的收尾函数
func serveOne() (string, func() (int64, error), error) {
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
		n, err := io.Copy(io.Discard, conn)
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
		case <-time.After(10 * time.Second):
			return 0, fmt.Errorf("接收端超时")
		}
	}
	return ln.Addr().String(), done, nil
}

// setCork 通过 RawConn 拿到底层 fd 后设置 TCP_CORK
func setCork(tcp *net.TCPConn, on bool) error {
	rc, err := tcp.SyscallConn()
	if err != nil {
		return err
	}
	var serr error
	err = rc.Control(func(fd uintptr) {
		v := 0
		if on {
			v = 1
		}
		serr = syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpCork, v)
	})
	if err != nil {
		return err
	}
	return serr
}

type result struct {
	name string
	n    int64
	dur  time.Duration
}

// run 用给定策略发一遍数据，返回实际发出的字节数与耗时
func run(name string, send func(*net.TCPConn, []byte, []byte) (int64, error)) result {
	addr, done, err := serveOne()
	if err != nil {
		fmt.Println("监听失败:", err)
		os.Exit(1)
	}
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		fmt.Println("拨号失败:", err)
		os.Exit(1)
	}
	tcp := conn.(*net.TCPConn)
	tcp.SetNoDelay(true) // 关掉 Nagle，避免小包被延迟合并干扰观测

	header := bytes.Repeat([]byte("H"), headerLen)
	body := bytes.Repeat([]byte("B"), bodyLen)

	start := time.Now()
	n, err := send(tcp, header, body)
	elapsed := time.Since(start)
	if cerr := tcp.CloseWrite(); cerr != nil && err == nil {
		err = cerr
	}
	got, rerr := done()
	tcp.Close()
	if err != nil {
		fmt.Printf("%s 发送失败: %v\n", name, err)
		os.Exit(1)
	}
	if rerr != nil {
		fmt.Printf("%s 接收失败: %v\n", name, rerr)
		os.Exit(1)
	}
	check(got == int64(headerLen+bodyLen),
		fmt.Sprintf("%s 接收端应收满 %d 字节，实际 %d", name, headerLen+bodyLen, got))
	return result{name: name, n: n, dur: elapsed}
}

// typeProbes 用真实的 loopback 连接验证两个"快速路径钩子"是否存在：
//   - net.Buffers 实现 io.WriterTo  → 写入方向会走 writev(2)
//   - *net.TCPConn 实现 io.ReaderFrom → 读取方向会尝试 sendfile(2)/splice(2)
func typeProbes() {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		fmt.Println("  类型探测跳过:", err)
		return
	}
	defer ln.Close()
	go func() {
		c, err := ln.Accept()
		if err == nil {
			c.Close()
		}
	}()
	conn, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		fmt.Println("  类型探测跳过:", err)
		return
	}
	defer conn.Close()
	tcp := conn.(*net.TCPConn)
	_, isRF := interface{}(tcp).(io.ReaderFrom)
	check(isRF, "*net.TCPConn 应实现 io.ReaderFrom（sendfile/splice 快速路径的钩子）")
}

func main() {
	results := []result{
		// A. 两次独立 Write：两次系统调用
		run("A. Write + Write（2 次系统调用）", func(c *net.TCPConn, h, b []byte) (int64, error) {
			n1, err := c.Write(h)
			if err != nil {
				return int64(n1), err
			}
			n2, err := c.Write(b)
			return int64(n1 + n2), err
		}),
		// B. 用户态 memcpy 合并再一次 Write
		run("B. 合并 + Write（1 次调用 + memcpy）", func(c *net.TCPConn, h, b []byte) (int64, error) {
			merged := make([]byte, 0, len(h)+len(b))
			merged = append(merged, h...)
			merged = append(merged, b...)
			n, err := c.Write(merged)
			return int64(n), err
		}),
		// C. net.Buffers → writev(2)，无需用户态合并
		run("C. net.Buffers.WriteTo（writev）", func(c *net.TCPConn, h, b []byte) (int64, error) {
			bufs := net.Buffers{h, b}
			return bufs.WriteTo(c)
		}),
		// D. TCP_CORK：两次写被塞住到 uncork
		run("D. TCP_CORK + Write + Write + uncork", func(c *net.TCPConn, h, b []byte) (int64, error) {
			if err := setCork(c, true); err != nil {
				return 0, err
			}
			n1, err := c.Write(h)
			if err != nil {
				return int64(n1), err
			}
			n2, err := c.Write(b)
			if err != nil {
				return int64(n1 + n2), err
			}
			if err := setCork(c, false); err != nil { // uncork → 一齐发出
				return int64(n1 + n2), err
			}
			return int64(n1 + n2), nil
		}),
	}

	fmt.Printf("=== 四种写响应策略（loopback TCP，头 %d B + 体 %d B，TCP_NODELAY=1）===\n",
		headerLen, bodyLen)
	for _, r := range results {
		fmt.Printf("  %-38s 写出 %4d 字节  %8.3f ms\n", r.name, r.n, float64(r.dur.Microseconds())/1000)
	}

	fmt.Println("\n=== 自检 ===")
	// 1. 四种策略必须都完整送出相同字节数（短写要自己处理，这里数据量小一次写完）
	want := int64(headerLen + bodyLen)
	for _, r := range results {
		check(r.n == want, fmt.Sprintf("%s 应写出 %d 字节", r.name, want))
	}
	// 2. net.Buffers 必须实现 io.WriterTo —— 这是"走 writev 而非逐块 Write"的前提
	{
		var bufs net.Buffers = net.Buffers{[]byte("a"), []byte("b")}
		_, isWT := interface{}(&bufs).(io.WriterTo)
		check(isWT, "net.Buffers 应实现 io.WriterTo（writev 快速路径的前提）")
	}
	// 3. *net.TCPConn 实现 io.ReaderFrom —— 这是 sendfile/splice 快速路径的钩子
	typeProbes()

	fmt.Println("\n=== 结论 ===")
	fmt.Println("  · 只有 C 是真正的零拷贝聚集写：一次系统调用 + 内核按 iovec 组包")
	fmt.Println("  · B 省了系统调用却付了一次全量 memcpy（响应体越大越亏）")
	fmt.Println("  · D 适合「头在别处产生 + 正文走 sendfile」——man7 sendfile(2) 明确推荐")
	fmt.Println("  · A 是最常见的写法，也是小响应下「多一个包、多一次系统调用」的来源")
	// 4. TCP_CORK 常量与 Linux uapi 一致；且仅在 Linux 可用
	check(tcpCork == 3, "TCP_CORK 在 Linux 上是 3（include/uapi/linux/tcp.h）")
	if runtime.GOOS != "linux" {
		fmt.Printf("  注意：当前平台 %s 非 Linux，TCP_CORK/IPPROTO_TCP 语义不适用\n", runtime.GOOS)
	}

	fmt.Println("\n=== 结论 ===")
	fmt.Println("  · 只有 C 是真正的零拷贝聚集写：一次系统调用 + 内核按 iovec 组包")
	fmt.Println("  · B 省了系统调用却付了一次全量 memcpy（响应体越大越亏）")
	fmt.Println("  · D 适合「头在别处产生 + 正文走 sendfile」——man7 sendfile(2) 明确推荐")
	fmt.Println("  · A 是最常见的写法，也是小响应下"多一个包、多一次系统调用"的来源")

	if failed > 0 {
		fmt.Printf("\n有 %d 项自检失败\n", failed)
		os.Exit(1)
	}
}
