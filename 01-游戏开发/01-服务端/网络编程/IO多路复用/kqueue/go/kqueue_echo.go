/*
 * Go kqueue echo server using syscall.Kqueue / Kevent (macOS / BSD only).
 * NOTE: Go's syscall package supports kqueue on Darwin/BSD. On Linux it
 * does not exist, so this demo must be run on macOS or FreeBSD.
 *
 *   GOOS=darwin go build -o kqueue_echo kqueue_echo.go && ./kqueue_echo 9000
 */
package main

import (
	"fmt"
	"os"
	"syscall"
)

const (
	maxEvents = 32
	bufSize   = 4096
)

// One kevent per fd; udata is the file descriptor itself for O(1) lookup.
type conn struct {
	fd  int
	buf []byte
}

var (
	conns = make(map[int]*conn) // fd -> conn
)

func setNonblock(fd int) {
	// syscall.O_NONBLOCK is set once on listen socket and every accepted conn.
}

// kqueueSett is the helper around EV_SET; flags=EV_ADD|EV_ENABLE.
func register(kq int, fd int16, filter uint16, flags uint16) {
	kev := syscall.Kevent_t{
		Ident:  uint64(fd),
		Filter: filter,
		Flags:  flags,
		// Fflags, Data, Udata default to 0
	}
	if _, err := syscall.Kevent(kq, []syscall.Kevent_t{kev}, nil, nil); err != nil {
		fmt.Fprintf(os.Stderr, "kevent register fd=%d: %v\n", fd, err)
	}
}

func main() {
	port := 9000
	if len(os.Args) > 1 {
		fmt.Sscanf(os.Args[1], "%d", &port)
	}

	// 1. Listen socket
	sfd, err := syscall.Socket(syscall.AF_INET, syscall.SOCK_STREAM, 0)
	if err != nil {
		fmt.Fprintln(os.Stderr, "socket:", err)
		os.Exit(1)
	}
	syscall.SetsockoptInt(sfd, syscall.SOL_SOCKET, syscall.SO_REUSEADDR, 1)
	addr := syscall.SockaddrInet4{Port: port}
	if err := syscall.Bind(sfd, &addr); err != nil {
		fmt.Fprintln(os.Stderr, "bind:", err)
		os.Exit(1)
	}
	if err := syscall.Listen(sfd, 16); err != nil {
		fmt.Fprintln(os.Stderr, "listen:", err)
		os.Exit(1)
	}
	// Non-blocking accept loop
	if err := syscall.SetNonblock(sfd, true); err != nil {
		fmt.Fprintln(os.Stderr, "nonblock:", err)
	}

	// 2. kqueue + listen registration
	kq, err := syscall.Kqueue()
	if err != nil {
		fmt.Fprintln(os.Stderr, "kqueue:", err)
		os.Exit(1)
	}
	register(kq, int16(sfd), syscall.EVFILT_READ, syscall.EV_ADD|syscall.EV_ENABLE)

	fmt.Printf("kqueue_echo listening on :%d (kq=%d)\n", port, kq)

	// 3. Event loop
	events := make([]syscall.Kevent_t, maxEvents)
	for {
		var timeout syscall.Timespec
		timeout.Sec = 1 // 1s tick for clean shutdown
		n, err := syscall.Kevent(kq, nil, events, &timeout)
		if err != nil {
			if err == syscall.EINTR {
				continue
			}
			fmt.Fprintln(os.Stderr, "kevent:", err)
			break
		}
		for i := 0; i < n; i++ {
			ev := &events[i]
			ident := int(ev.Ident)

			// Error or EOF
			if ev.Flags&syscall.EV_ERROR != 0 {
				fmt.Fprintf(os.Stderr, "EV_ERROR fd=%d data=%d\n", ident, int(ev.Data))
				syscall.Close(ident)
				delete(conns, ident)
				continue
			}

			// Listen socket -> accept loop
			if ident == sfd && ev.Filter == syscall.EVFILT_READ {
				for {
					cfd, _, err := syscall.Accept(sfd)
					if err != nil {
						if err == syscall.EAGAIN {
							break
						}
						fmt.Fprintln(os.Stderr, "accept:", err)
						break
					}
					syscall.SetNonblock(cfd, true)
					conns[cfd] = &conn{fd: cfd, buf: make([]byte, 0, bufSize)}
					register(kq, int16(cfd), syscall.EVFILT_READ,
						syscall.EV_ADD|syscall.EV_ENABLE)
				}
				continue
			}

			// Client socket -> recv + echo back
			c, ok := conns[ident]
			if !ok {
				continue
			}
			tmp := make([]byte, bufSize)
			n2, err := syscall.Read(ident, tmp)
			if n2 == 0 {
				syscall.Close(ident)
				delete(conns, ident)
				continue
			}
			if err != nil {
				continue
			}
			c.buf = append(c.buf, tmp[:n2]...)
			syscall.Write(ident, c.buf)
			c.buf = c.buf[:0]
		}
	}
	syscall.Close(kq)
	syscall.Close(sfd)
}
