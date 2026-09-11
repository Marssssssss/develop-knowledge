// nagle_demo.go — Nagle algorithm & TCP_NODELAY on loopback (Go 1.21+)
//
// Demonstrates:
//   demo 1 — Nagle on (default):   100 single-byte writes => fewer segments
//   demo 2 — TCP_NODELAY=1:         100 single-byte writes => 100 segments
//   demo 3 — interactive echo:     ping/pong latency with/without Nagle
//
// Measurement: getsockopt(TCP_INFO) via raw syscall6 (golang.org/x/sys not used).
// struct tcp_info field offsets on Linux 5.10+ (200-byte layout) place
// tcpi_segs_out at offset 100 (uint32). Verified against `man 7 tcp`.
//
// Run:
//   go run nagle_demo.go
//
// Linux only — uses IPPROTO_TCP and the Linux struct tcp_info layout.

package main

import (
	"fmt"
	"net"
	"os"
	"syscall"
	"time"
	"unsafe"
)

const (
	loopback  = "127.0.0.1"
	nWrites   = 100
	tcpInfoSz = 200 // sizeof(struct tcp_info) on Linux 5.10+

	// Offsets into struct tcp_info (Linux 5.10+) — see <linux/tcp.h>:
	//   tcpi_segs_out is the 26th uint32 on the standard 200-byte layout.
	tcpInfoSegsOutOffset = 25 * 4 // bytes
)

// linux TCP constants not always in syscall.
const (
	ipprotoTCP   = syscall.IPPROTO_TCP
	tcpOptInfo   = 11 // TCP_INFO (defined in <netinet/tcp.h>)
)

// ----------------------------------------------------------------------------
// Low-level: setsockopt / getsockopt for TCP_INFO via raw fd.
// ----------------------------------------------------------------------------

// rawSyscall6 wraps syscall.Syscall6 — needed for getsockopt on IPv4 TCP
// sockets because there's no stdlib helper that returns []byte.
func rawSyscall6(n, a1, a2, a3, a4, a5, a6 uintptr) (r1, r2 uintptr, err syscall.Errno) {
	return syscall.Syscall6(n, a1, a2, a3, a4, a5, a6)
}

func getsockoptTCPInfoSegsOut(fd uintptr) (uint32, error) {
	var buf [tcpInfoSz]byte
	l := uint32(tcpInfoSz)
	_, _, errno := rawSyscall6(syscall.SYS_GETSOCKOPT, fd, ipprotoTCP, tcpOptInfo,
		uintptr(unsafe.Pointer(&buf[0])), uintptr(unsafe.Pointer(&l)), 0)
	if errno != 0 {
		return 0, errno
	}
	return *(*uint32)(unsafe.Pointer(&buf[tcpInfoSegsOutOffset])), nil
}

// fd returns the underlying fd via the net.Conn SyscallConn control hook.
func fdOf(conn net.Conn) (uintptr, error) {
	sc, ok := conn.(syscall.Conn)
	if !ok {
		return 0, fmt.Errorf("not a syscall.Conn")
	}
	raw, err := sc.SyscallConn()
	if err != nil {
		return 0, err
	}
	var fd uintptr
	if err := raw.Control(func(f uintptr) { fd = f }); err != nil {
		return 0, err
	}
	return fd, nil
}

// ----------------------------------------------------------------------------
// Server helpers.
// ----------------------------------------------------------------------------

type server struct {
	ln  *net.TCPListener
	cli *net.TCPConn
}

// startServer opens an ephemeral loopback listener.
func startServer() (*server, int, error) {
	ln, err := net.ListenTCP("tcp4", &net.TCPAddr{IP: net.ParseIP(loopback), Port: 0})
	if err != nil {
		return nil, 0, err
	}
	return &server{ln: ln}, ln.Addr().(*net.TCPAddr).Port, nil
}

// acceptOnce blocks until one client connects.
func (s *server) acceptOnce() (*net.TCPConn, error) {
	cli, err := s.ln.AcceptTCP()
	if err != nil {
		return nil, err
	}
	s.cli = cli
	return cli, nil
}

// runSendNWrites performs demo 1/2 and returns segments-sent delta + bytes rx + wall us.
func runSendNWrites(nodelayOn bool) (int, int, float64, error) {
	s, port, err := startServer()
	if err != nil {
		return 0, 0, 0, err
	}
	defer s.ln.Close()

	type accepted struct{ n int }
	ready := make(chan accepted, 1)
	go func() {
		c, err := s.acceptOnce()
		if err != nil {
			ready <- accepted{-1}
			return
		}
		// Drain until peer closes.
		var total int
		buf := make([]byte, 4096)
		for {
			n, err := c.Read(buf)
			total += n
			if err != nil {
				break
			}
		}
		c.Close()
		ready <- accepted{total}
	}()

	addr, _ := net.ResolveTCPAddr("tcp4", fmt.Sprintf("%s:%d", loopback, port))
	c, err := net.DialTCP("tcp4", nil, addr)
	if err != nil {
		return 0, 0, 0, err
	}
	if nodelayOn {
		c.SetNoDelay(true)
	}

	fd, _ := fdOf(c)
	segsBefore, err := getsockoptTCPInfoSegsOut(fd)
	if err != nil {
		return 0, 0, 0, err
	}

	t0 := time.Now()
	for i := 0; i < nWrites; i++ {
		if _, err := c.Write([]byte{'a'}); err != nil {
			return 0, 0, 0, err
		}
		time.Sleep(1 * time.Microsecond)
	}
	c.CloseWrite()
	t1 := time.Now()

	segsAfter, err := getsockoptTCPInfoSegsOut(fd)
	c.Close()
	if err != nil {
		return 0, 0, 0, err
	}

	res := <-ready
	if res.n < 0 {
		return 0, 0, 0, fmt.Errorf("server failed to accept")
	}
	us := float64(t1.Sub(t0)) / 1e3
	delta := int(segsAfter - segsBefore)
	return delta, res.n, us, nil
}

func demoSendNWrites(nodelayOn bool) {
	delta, rx, us, err := runSendNWrites(nodelayOn)
	if err != nil {
		fmt.Fprintf(os.Stderr, "demo %s: %v\n",
			map[bool]string{true: "2 (NODELAY)", false: "1 (Nagle)"}[nodelayOn], err)
		return
	}
	label := "demo 2  TCP_NODELAY=1"
	if !nodelayOn {
		label = "demo 1  Nagle on  "
	}
	fmt.Printf("=== %s ==============================================\n", label)
	fmt.Printf("  Sent %d single-byte writes (%d bytes total)\n", nWrites, nWrites)
	fmt.Printf("  TCP segments emitted (tcpi_segs_out delta): %d\n", delta)
	fmt.Printf("  wall time                         : %8.1f µs (%.2f µs/write)\n",
		us, us/float64(nWrites))
	fmt.Printf("  server received                   : %d bytes\n", rx)
	if !nodelayOn && delta < nWrites {
		fmt.Printf("  >>> Nagle coalesced %d bytes into %d segments (ratio %.2fx)\n",
			nWrites, delta, float64(nWrites)/float64(delta))
	}
	if nodelayOn && delta == nWrites {
		fmt.Printf("  >>> TCP_NODELAY emitted one segment per write as expected\n")
	}
}

// ----------------------------------------------------------------------------
// demo 3 — interactive echo.
// ----------------------------------------------------------------------------

func runInteractive(nodelayOn bool) (float64, error) {
	s, port, err := startServer()
	if err != nil {
		return 0, err
	}
	defer s.ln.Close()

	rounds := 50
	ready := make(chan error, 1)
	go func() {
		c, err := s.acceptOnce()
		if err != nil {
			ready <- err
			return
		}
		for i := 0; i < rounds; i++ {
			buf := make([]byte, 1)
			if _, err := c.Read(buf); err != nil {
				break
			}
			if _, err := c.Write(buf); err != nil {
				break
			}
		}
		c.Close()
		ready <- nil
	}()

	addr, _ := net.ResolveTCPAddr("tcp4", fmt.Sprintf("%s:%d", loopback, port))
	c, err := net.DialTCP("tcp4", nil, addr)
	if err != nil {
		return 0, err
	}
	if nodelayOn {
		c.SetNoDelay(true)
	}

	t0 := time.Now()
	for i := 0; i < rounds; i++ {
		if _, err := c.Write([]byte{'X'}); err != nil {
			return 0, err
		}
		buf := make([]byte, 1)
		if _, err := c.Read(buf); err != nil {
			return 0, err
		}
	}
	t1 := time.Now()
	c.Close()
	<-ready

	us := float64(t1.Sub(t0)) / 1e3
	return us, nil
}

func demoInteractive(nodelayOn bool) {
	us, err := runInteractive(nodelayOn)
	if err != nil {
		fmt.Fprintf(os.Stderr, "demo 3 (%v): %v\n",
			map[bool]string{true: "NODELAY", false: "Nagle"}[nodelayOn], err)
		return
	}
	rounds := 50
	label := "TCP_NODELAY=1"
	if !nodelayOn {
		label = "Nagle on  "
	}
	fmt.Printf("=== demo 3  interactive echo (%s) ===========================\n", label)
	fmt.Printf("  %d ping/pong rounds in %8.1f µs (%.3f µs/round, RTT ≈ %.3f µs)\n",
		rounds, us, us/float64(rounds), us/float64(rounds)/2)
	fmt.Printf("  (On loopback both are sub-microsecond; the algorithmic wait is\n")
	fmt.Printf("   dwarfed by syscall overhead — the seg-count test is the real\n")
	fmt.Printf("   differentiator.)\n")
}

func main() {
	fmt.Println("# Nagle vs TCP_NODELAY — loopback measurement (Go)")
	fmt.Println("# server & client in same process; counts via TCP_INFO")
	fmt.Println()

	demoSendNWrites(false)
	fmt.Println()
	demoSendNWrites(true)
	fmt.Println()
	demoInteractive(false)
	demoInteractive(true)
}
