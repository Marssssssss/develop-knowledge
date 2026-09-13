// TCP Keepalive 演示 — 启用 SO_KEEPALIVE + 自定义三参数,验证对端挂起时 Read 返回 net.Error。
//
// Go 标准库自 Go 1.17 起支持 TCP_KEEPIDLE 等参数(通过 SetKeepAliveConfig / SetKeepAlivePeriod)。
// 我们用底层 syscall 走 IPPROTO_TCP 与 Linux 专有选项以保证演示一致性。
//
// 运行: go run . server 9090   +   go run . client 127.0.0.1 9090
package main

import (
	"errors"
	"fmt"
	"log"
	"net"
	"os"
	"strconv"
	"syscall"
	"time"
)

// Linux TCP 选项常量(若将来跨 BSD/macOS 需用 golang.org/x/sys/unix)
const (
	tcpKeepIdle  = 4 // TCP_KEEPIDLE
	tcpKeepIntvl = 5 // TCP_KEEPINTVL
	tcpKeepCnt   = 6 // TCP_KEEPCNT
)

func enableKeepAlive(c *net.TCPConn, idle, intvl, cnt int) error {
	// 标准库:开启 SO_KEEPALIVE
	if err := c.SetKeepAlive(true); err != nil {
		return fmt.Errorf("SetKeepAlive: %w", err)
	}
	// 标准库:Go 1.17+ SetKeepAlivePeriod 等价于 idle + intvl(简化模型)
	// 这里我们直接走 syscall 设置三个独立参数
	raw, err := c.SyscallConn()
	if err != nil {
		return fmt.Errorf("SyscallConn: %w", err)
	}
	var setErr error
	if err := raw.Control(func(fd uintptr) {
		if e := syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepIdle, idle); e != nil {
			setErr = e
		}
		if e := syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepIntvl, intvl); e != nil {
			setErr = e
		}
		if e := syscall.SetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepCnt, cnt); e != nil {
			setErr = e
		}
	}); err != nil {
		return fmt.Errorf("Control: %w", err)
	}
	return setErr
}

func dumpKeepAlive(c *net.TCPConn) {
	on, _ := c.GetKeepAlive()
	fmt.Printf("  SO_KEEPALIVE=%v\n", on)
	raw, err := c.SyscallConn()
	if err != nil {
		return
	}
	_ = raw.Control(func(fd uintptr) {
		idle, _ := syscall.GetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepIdle)
		intvl, _ := syscall.GetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepIntvl)
		cnt, _ := syscall.GetsockoptInt(int(fd), syscall.IPPROTO_TCP, tcpKeepCnt)
		fmt.Printf("  TCP_KEEPIDLE=%ds  TCP_KEEPINTVL=%ds  TCP_KEEPCNT=%d\n", idle, intvl, cnt)
	})
}

func runServer(port int) error {
	addr := ":" + strconv.Itoa(port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return err
	}
	defer ln.Close()
	log.Printf("[server] listening on %s", addr)

	conn, err := ln.Accept()
	if err != nil {
		return err
	}
	defer conn.Close()
	tcpConn := conn.(*net.TCPConn)
	log.Printf("[server] accepted from %s", conn.RemoteAddr())
	fmt.Println("[server] before keepalive:")
	dumpKeepAlive(tcpConn)

	// idle=3s, intvl=2s, cnt=3 → ~3+6=9s 探测失败
	if err := enableKeepAlive(tcpConn, 3, 2, 3); err != nil {
		return fmt.Errorf("enableKeepAlive: %w", err)
	}
	fmt.Println("[server] after keepalive (idle=3, intvl=2, cnt=3 → ~9s 探测失败):")
	dumpKeepAlive(tcpConn)

	_ = conn.SetReadDeadline(time.Now().Add(20 * time.Second))
	buf := make([]byte, 64)
	for {
		n, err := conn.Read(buf)
		if n > 0 {
			fmt.Printf("[server] got %dB: %q\n", n, string(buf[:n]))
		}
		if err == nil {
			continue
		}
		if errors.Is(err, net.ErrClosed) {
			break
		}
		// net.Error.Timeout() 说明没数据但对方还活着;其他错误多由 keepalive 触发
		var ne net.Error
		if errors.As(err, &ne) && ne.Timeout() {
			fmt.Println("[server] read timeout (peer still alive, no data yet)")
			continue
		}
		fmt.Printf("[server] read error after dead-peer: %v\n", err)
		break
	}
	return nil
}

func runClient(host string, port int) error {
	addr := host + ":" + strconv.Itoa(port)
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		return err
	}
	defer conn.Close()
	fmt.Println("[client] connected; sending hello and sleeping 15s")
	_, _ = conn.Write([]byte("hello\n"))
	time.Sleep(15 * time.Second)
	_, _ = conn.Write([]byte("after sleep\n"))
	return nil
}

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage:\n  ka_demo server <port>\n  ka_demo client <ip> <port>")
		os.Exit(1)
	}
	mode := os.Args[1]
	switch mode {
	case "server":
		port, _ := strconv.Atoi(os.Args[2])
		if err := runServer(port); err != nil {
			log.Fatal(err)
		}
	case "client":
		if len(os.Args) != 4 {
			log.Fatal("client needs <ip> <port>")
		}
		port, _ := strconv.Atoi(os.Args[3])
		if err := runClient(os.Args[2], port); err != nil {
			log.Fatal(err)
		}
	default:
		log.Fatalf("unknown mode %q", mode)
	}
}