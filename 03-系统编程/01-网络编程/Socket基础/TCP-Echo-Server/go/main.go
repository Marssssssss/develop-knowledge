// TCP Echo Server — net 库最小阻塞实现。
// 流程: net.Listen("tcp", ":port") → Accept → Read/Write 回环
// 运行: go run . 9090   (客户端:nc 127.0.0.1 9090)
package main

import (
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
)

const readBuf = 4096

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: echo_server <port>")
		os.Exit(1)
	}
	addr := ":" + os.Args[1]

	// 1) socket + bind + listen (net.Listen 一次性封装)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		log.Fatalf("listen: %v", err)
	}
	defer ln.Close()
	log.Printf("echo server listening on %s (Ctrl-C to stop)", addr)

	// 捕获 SIGINT/SIGTERM 优雅关闭
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		log.Println("shutting down...")
		ln.Close() // 触发 Accept 返回 net.ErrClosed
	}()

	// 2) accept loop
	for {
		conn, err := ln.Accept()
		if err != nil {
			if errors.Is(err, net.ErrClosed) {
				return
			}
			log.Printf("accept: %v", err)
			continue
		}
		log.Printf("accept %s", conn.RemoteAddr())
		handle(conn)
		log.Printf("close  %s", conn.RemoteAddr())
	}
}

// handle 单连接回环:io.CopyBuffer 把 client 读到的字节原样回写;
// client 关闭后 Read 返回 io.EOF → io.CopyBuffer 退出 → 我们关闭 conn。
func handle(c net.Conn) {
	defer c.Close()
	_, _ = io.CopyBuffer(c, c, make([]byte, readBuf))
}