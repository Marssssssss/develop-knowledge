// select_echo.go — Minimal TCP echo server using syscall.Select directly.
//
// Build:  go build -o select_echo select_echo.go
// Run:    ./select_echo 9000
// Test:   nc localhost 9000
//
// Note: syscall.Select works on POSIX (Linux/macOS/BSD). On Windows, Go's
// syscall.Select uses WSAPoll under the hood; the API is the same but fd_set
// is built with the same Bits[] trick.

package main

import (
	"fmt"
	"net"
	"os"
	"syscall"
)

const (
	maxClients = 64
	bufSize    = 4096
)

func main() {
	port := "9000"
	if len(os.Args) > 1 {
		port = os.Args[1]
	}

	srv, err := net.Listen("tcp", ":"+port)
	must(err, "listen")
	defer srv.Close()
	fmt.Printf("select_echo listening on :%s\n", port)

	// Raw file descriptor of the listener — needed because syscall.Select
	// operates on fds, not on net.Listener directly.
	srvFd := int(srv.(*net.TCPListener).Fd())

	clients := make([]int, 0, maxClients)
	buf := make([]byte, bufSize)

	for {
		// 1) Build a fresh fd_set every iteration. Go's FdSet.Bits is
		//    indexed by fd/64, each holding 64 bits (one per fd).
		fdSet := &syscall.FdSet{}
		setBit(fdSet, srvFd)
		maxFd := srvFd

		for _, c := range clients {
			setBit(fdSet, c)
			if c > maxFd {
				maxFd = c
			}
		}

		// 2) Wait up to 1 second — keeps the loop responsive to Ctrl+C.
		tv := &syscall.Timeval{Sec: 1}
		n, err := syscall.Select(maxFd+1, fdSet, nil, nil, tv)
		if err != nil {
			if err == syscall.EINTR {
				continue
			}
			fmt.Fprintln(os.Stderr, "select:", err)
			break
		}
		if n == 0 {
			continue
		}

		// 3) New connection on the listener?
		if hasBit(fdSet, srvFd) {
			conn, err := srv.Accept()
			if err == nil {
				fd := int(conn.(*net.TCPConn).Fd())
				clients = append(clients, fd)
				fmt.Printf("+ client fd=%d from %s\n", fd, conn.RemoteAddr())
			}
		}

		// 4) Read-ready clients → echo or close.
		alive := clients[:0]
		for _, c := range clients {
			if !hasBit(fdSet, c) {
				alive = append(alive, c)
				continue
			}
			n, err := syscall.Read(c, buf)
			if err != nil || n == 0 {
				fmt.Printf("- client fd=%d closed\n", c)
				syscall.Close(c)
				continue
			}
			// Naive echo: assume server can send all at once (fine for tiny msgs).
			syscall.Write(c, buf[:n])
			alive = append(alive, c)
		}
		clients = alive
	}
}

// setBit sets the bit for fd inside FdSet.Bits[fd/64].
func setBit(fs *syscall.FdSet, fd int) {
	fs.Bits[fd/64] |= 1 << (uint(fd) % 64)
}

// hasBit checks the bit for fd.
func hasBit(fs *syscall.FdSet, fd int) bool {
	return fs.Bits[fd/64]&(1<<(uint(fd)%64)) != 0
}

func must(err error, tag string) {
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", tag, err)
		os.Exit(1)
	}
}