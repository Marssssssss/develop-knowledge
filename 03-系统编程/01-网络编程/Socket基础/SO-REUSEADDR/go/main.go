// SO_REUSEADDR / SO_REUSEPORT 与 TIME_WAIT 演示。
//
// 关键点:
//   - SO_REUSEADDR: 服务端关掉后立即重启必备;否则 bind EADDRINUSE
//   - SO_REUSEPORT: Linux 3.9+;允许多个 socket 绑同 (addr, port);内核哈希分连接
//   - TIME_WAIT: 主动关闭方进,持续 2*MSL(Linux 固定 60s);影响 server 重启
//
// 运行:
//   go run . explain             # 打印概念说明
//   go run . demo                # 自动跑一组对比实验
package main

import (
	"errors"
	"fmt"
	"log"
	"net"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const port = 9090

// makeListener 按 opts 创建监听 socket。
func makeListener(port int, reuseAddr, reusePort bool) (net.Listener, error) {
	lc := net.ListenConfig{
		Control: func(network, addr string, c syscall.RawConn) error {
			return c.Control(func(fd uintptr) {
				if reuseAddr {
					_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, syscall.SO_REUSEADDR, 1)
				}
				if reusePort {
					_ = syscall.SetsockoptInt(int(fd), syscall.SOL_SOCKET, 15 /* SO_REUSEPORT */, 1)
				}
			})
		},
	}
	return lc.Listen(nil, "tcp", ":"+strconv.Itoa(port))
}

// timeWaitCount 返回本机 sport=:port 的 TIME_WAIT 计数(通过 ss -tan)。
func timeWaitCount(port int) int {
	out, err := exec.Command("ss", "-tan", "state", "time-wait",
		"sport", "=", fmt.Sprintf(":%d", port)).Output()
	if err != nil {
		return -1
	}
	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	if len(lines) <= 1 {
		return 0
	}
	return len(lines) - 1
}

func step(label string) {
	fmt.Printf("\n--- %s ---\n", label)
}

func runDemo() {
	step("实验 A:不开 SO_REUSEADDR → 立即重启应失败")
	a1, err := makeListener(port, false, false)
	if err != nil { log.Fatal(err) }
	fmt.Printf("[A1] listening(no REUSEADDR); TIME_WAIT=%d\n", timeWaitCount(port))
	_ = a1.Close()
	fmt.Printf("[A2] closed; TIME_WAIT=%d\n", timeWaitCount(port))
	a3, err := makeListener(port, false, false)
	if err != nil {
		fmt.Printf("[A3] bind 失败(符合预期): %v\n", err)
	} else {
		fmt.Println("[A3] !!! bind 成功了(异常)")
		_ = a3.Close()
	}

	step("实验 B:开 SO_REUSEADDR → 立即重启应成功")
	b1, err := makeListener(port, true, false)
	if err != nil { log.Fatal(err) }
	fmt.Println("[B1] listening(REUSEADDR=1)")
	_ = b1.Close()
	fmt.Printf("[B2] closed; TIME_WAIT=%d\n", timeWaitCount(port))
	b3, err := makeListener(port, true, false)
	if err != nil {
		fmt.Printf("[B3] bind 失败(异常): %v\n", err)
	} else {
		fmt.Println("[B3] bind 成功(符合预期)")
		_ = b3.Close()
	}

	step("实验 C:SO_REUSEPORT 同端口两组监听(子进程演示)")
	if os.Getenv("REUSEPORT_CHILD") == "1" {
		// 子进程:绑 9091
		ln, err := makeListener(9091, true, true)
		if err != nil { log.Fatal(err) }
		fmt.Println("[C-child] listening on :9091")
		// 保持一会儿,接受几个连接
		for {
			c, err := ln.Accept()
			if err != nil {
				if errors.Is(err, net.ErrClosed) { return }
				log.Printf("accept: %v", err)
				continue
			}
			fmt.Printf("[C-child] accepted %s\n", c.RemoteAddr())
			_ = c.Close()
		}
	}
	cmd := exec.Command(os.Args[0])
	cmd.Env = append(os.Environ(), "REUSEPORT_CHILD=1")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Start(); err != nil { log.Fatal(err) }
	time.Sleep(300 * time.Millisecond) // 等 child 起来

	ln, err := makeListener(9091, true, true)
	if err != nil { log.Fatal(err) }
	fmt.Println("[C-parent] listening on :9091")
	for i := 0; i < 5; i++ {
		c, err := ln.Accept()
		if err != nil { break }
		fmt.Printf("[C-parent] accept #%d: %s\n", i, c.RemoteAddr())
		_ = c.Close()
	}
	_ = ln.Close()
	_ = cmd.Wait()
}

func explain() {
	fmt.Print(`
=== SO_REUSEADDR vs SO_REUSEPORT vs TIME_WAIT ===

TIME_WAIT: TCP 主动关闭方在发 FIN 收到 ACK 后进入;持续 2*MSL(Linux 默认 60s)。
  目的: a) 旧 FIN/ACK 重传完成; b) 旧五元组不再出现在网络中

SO_REUSEADDR (POSIX):
  允许 bind 一个处于 TIME_WAIT 的地址;
  Linux 上旧 socket 也需 SO_REUSEADDR 才能被覆盖。

SO_REUSEPORT (Linux 3.9+):
  允许多个 socket 同时 bind 完全相同的 (addr, port);
  内核按四元组 hash 分发新连接;用于多进程负载分担。
`)
}

func main() {
	if len(os.Args) >= 2 {
		switch os.Args[1] {
		case "explain":
			explain()
			return
		case "demo":
			runDemo()
			return
		}
	}
	fmt.Fprintln(os.Stderr, "usage: reuse_demo demo | explain")
	os.Exit(1)
}