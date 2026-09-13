// sendfile_server: Go zero-copy file -> socket.
//
// Go's syscall.Sendfile wraps sendfile(2) on Linux; on macOS / *BSD it
// uses a similar zero-copy primitive via syscall.Syscall, but only file -> socket.
// We use it to stream the bulk of the response body to every connected client.
// Headers are sent through conn.Write since sendfile can't prepend user data.
//
//   go run sendfile_server.go 9000 1024
package main

import (
	"fmt"
	"io"
	"net"
	"os"
	"syscall"
)

const filePath = "/tmp/sendfile_demo.bin"

// buildDemoFile materialises a deterministic N-KiB file on disk.
func buildDemoFile(kib int) (int64, error) {
	f, err := os.Create(filePath)
	if err != nil {
		return 0, err
	}
	defer f.Close()
	chunk := make([]byte, 4096)
	for i := range chunk {
		chunk[i] = byte(i % 256)
	}
	total := int64(kib) * 1024
	written := int64(0)
	for written < total {
		n, err := f.Write(chunk)
		if err != nil {
			return 0, err
		}
		written += int64(n)
	}
	return total, nil
}

func serveFileZeroCopy(c net.Conn, file *os.File, fileSize int64) (int64, error) {
	// 1) header via normal write
	header := fmt.Sprintf("X-Source: sendfile\r\nContent-Length: %d\r\n\r\n", fileSize)
	if _, err := c.Write([]byte(header)); err != nil {
		return 0, err
	}
	// 2) bulk zero-copy
	rc, err := syscall.Sendfile(c.(*net.TCPConn).Fd(), file.Fd(), nil, fileSize)
	if err != nil {
		return int64(rc), err
	}
	return int64(rc), nil
}

func main() {
	port := "9000"
	var kib int = 1024
	if len(os.Args) > 1 {
		port = os.Args[1]
	}
	if len(os.Args) > 2 {
		fmt.Sscanf(os.Args[2], "%d", &kib)
	}
	fileSize, err := buildDemoFile(kib)
	if err != nil {
		fmt.Fprintln(os.Stderr, "buildDemoFile:", err)
		os.Exit(1)
	}
	file, err := os.Open(filePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "open:", err)
		os.Exit(1)
	}
	defer file.Close()

	ln, err := net.Listen("tcp", ":"+port)
	if err != nil {
		fmt.Fprintln(os.Stderr, "listen:", err)
		os.Exit(1)
	}
	fmt.Printf("sendfile serving %s (%d B) on :%s\n", filePath, fileSize, port)
	for {
		c, err := ln.Accept()
		if err != nil {
			fmt.Fprintln(os.Stderr, "accept:", err)
			continue
		}
		go func(c net.Conn) {
			defer c.Close()
			// drain request
			_, _ = io.Copy(io.Discard, c)
			if _, err := serveFileZeroCopy(c, file, fileSize); err != nil {
				fmt.Fprintln(os.Stderr, "sendfile:", err)
			}
		}(c)
	}
}
