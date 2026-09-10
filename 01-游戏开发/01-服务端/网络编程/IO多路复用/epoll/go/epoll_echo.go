// epoll_echo.go — Go 版回显服务器：阻塞式 API 背后是 epoll
//
// 依据 Go 官方 runtime 源码 src/runtime/netpoll_epoll.go（Linux 构建）：
//
//	func netpollinit() {
//	    epfd, errno = syscall.EpollCreate1(syscall.EPOLL_CLOEXEC)   // 创建 epoll 实例
//	    ...
//	}
//	func netpollopen(fd uintptr, pd *pollDesc) uintptr {
//	    var ev syscall.EpollEvent
//	    ev.Events = syscall.EPOLLIN | syscall.EPOLLOUT | syscall.EPOLLRDHUP | syscall.EPOLLET
//	    ...  // 注意 EPOLLET：Go 的 netpoller 用的是【边缘触发】模式
//	    return syscall.EpollCtl(epfd, syscall.EPOLL_CTL_ADD, int32(fd), &ev)
//	}
//
// 即：Go 把 epoll(7) 的三步工作流（create1 / ctl / wait）封装进了运行时调度器，
// goroutine 在读阻塞时被挂起（netpollgwait），fd 就绪后由 netpoll() 唤醒并放回运行队列。
// 用户代码因此可以写"一连接一 goroutine"的同步阻塞风格，享受 epoll 的性能。
//
// 运行：go run epoll_echo.go [port]
// 测试：nc 127.0.0.1 9000
package main

import (
	"fmt"
	"io"
	"net"
	"os"
	"strconv"
)

func main() {
	port := 9000
	if len(os.Args) > 1 {
		if p, err := strconv.Atoi(os.Args[1]); err == nil {
			port = p
		}
	}

	ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		fmt.Fprintln(os.Stderr, "listen:", err)
		os.Exit(1)
	}
	fmt.Printf("echo server (goroutine-per-conn, netpoller=epoll ET) listening on %s\n", ln.Addr())

	// 主 goroutine 只负责 accept。注意：Accept() 看似阻塞，
	// 实际由 runtime netpoller 在监听 fd 可读时唤醒。
	for {
		conn, err := ln.Accept()
		if err != nil {
			fmt.Fprintln(os.Stderr, "accept:", err)
			continue
		}
		// 一连接一 goroutine：调度器 + netpoller 让这几乎是"免费"的
		go echo(conn)
	}
}

func echo(conn net.Conn) {
	defer conn.Close()

	// Copy 内部的 Read/Write 都是"阻塞式"调用：
	// Read 阻塞时 goroutine 让出线程（gopark），fd 事件由 epoll ET 上报后唤醒。
	// 这就是 Go 对 epoll 的最大封装收益——用户无需手写事件循环。
	n, err := io.Copy(conn, conn)
	fmt.Printf("connection %s done: copied %d bytes, err=%v\n",
		conn.RemoteAddr(), n, err)
}
