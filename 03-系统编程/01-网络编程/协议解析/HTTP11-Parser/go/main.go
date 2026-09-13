// HTTP/1.1 请求解析器 — RFC 7230 状态机 (Go 版)。
// 支持:Request-Line / Header Fields / 空行终结 / Content-Length / chunked / 裸 LF 容错。
// 运行: go run . test | serve <port>
package main

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"os"
	"strconv"
	"strings"
	"time"
)

const maxHeaderBytes = 16 * 1024

var ErrBadHTTP = errors.New("bad http message")

type RequestHead struct {
	Method    string
	Target    string
	Version   string
	Headers   map[string]string
	BodyStart int
	BodyLen   int // -1 表示 chunked
}

// readLine 从 buf[pos] 开始读到下一个 CRLF(或裸 LF);返回 (line_inclusive_of_terminator, new_pos)。
func readLine(buf []byte, pos int) ([]byte, int, error) {
	start := pos
	for pos < len(buf) {
		b := buf[pos]
		if b == '\n' {
			return buf[start : pos+1], pos + 1, nil
		}
		if b == '\r' && pos+1 < len(buf) && buf[pos+1] == '\n' {
			return buf[start : pos+2], pos + 2, nil
		}
		pos++
	}
	return nil, 0, io.ErrUnexpectedEOF
}

func ParseRequestHead(buf []byte) (RequestHead, error) {
	pos := 0
	// 跳过前导空行(RFC 7230 §3.5)
	for pos+1 < len(buf) && buf[pos] == '\r' && buf[pos+1] == '\n' {
		pos += 2
	}
	line, p, err := readLine(buf, pos)
	if err != nil {
		return RequestHead{}, err
	}
	pos = p
	line = bytes.TrimRight(line, "\r\n")
	parts := bytes.Split(line, []byte(" "))
	if len(parts) != 3 {
		return RequestHead{}, fmt.Errorf("%w: bad request-line %q", ErrBadHTTP, line)
	}
	method, target, version := string(parts[0]), string(parts[1]), string(parts[2])
	if !strings.HasPrefix(version, "HTTP/") {
		return RequestHead{}, fmt.Errorf("%w: bad version %q", ErrBadHTTP, version)
	}
	// Header fields;同名合并为逗号分隔(§3.2.2)
	headers := make(map[string]string)
	for pos < len(buf) {
		line, p, err := readLine(buf, pos)
		if err != nil {
			return RequestHead{}, err
		}
		pos = p
		trimmed := bytes.TrimRight(line, "\r\n")
		if len(trimmed) == 0 {
			break
		}
		idx := bytes.IndexByte(trimmed, ':')
		if idx < 0 {
			return RequestHead{}, fmt.Errorf("%w: bad header %q", ErrBadHTTP, trimmed)
		}
		name := strings.ToLower(strings.TrimSpace(string(trimmed[:idx])))
		value := strings.TrimSpace(string(trimmed[idx+1:]))
		if prev, ok := headers[name]; ok {
			headers[name] = prev + ", " + value
		} else {
			headers[name] = value
		}
	}
	bodyLen := 0
	if cl := headers["content-length"]; cl != "" {
		bodyLen, err = strconv.Atoi(cl)
		if err != nil {
			return RequestHead{}, fmt.Errorf("%w: bad content-length", ErrBadHTTP)
		}
	} else if headers["transfer-encoding"] == "chunked" {
		bodyLen = -1
	}
	return RequestHead{Method: method, Target: target, Version: version,
		Headers: headers, BodyStart: pos, BodyLen: bodyLen}, nil
}

// decodeChunked 流式解码 chunked body;io.Reader 形式产出各 chunk bytes。
func decodeChunked(r io.Reader) io.Reader {
	pr, pw := io.Pipe()
	go func() {
		defer pw.Close()
		br := bufio.NewReader(r)
		for {
			sizeLine, err := br.ReadString('\n')
			if err != nil { pw.CloseWithError(err); return }
			sizeLine = strings.TrimRight(sizeLine, "\r\n")
			if semi := strings.Index(sizeLine, ";"); semi >= 0 {
				sizeLine = sizeLine[:semi]
			}
			size, err := strconv.ParseInt(strings.TrimSpace(sizeLine), 16, 64)
			if err != nil { pw.CloseWithError(err); return }
			if size == 0 {
				// 末块:吃掉 trailer + CRLF
				for {
					tl, err := br.ReadString('\n')
					if err != nil { pw.CloseWithError(err); return }
					if strings.TrimRight(tl, "\r\n") == "" { return }
				}
			}
			if _, err := io.CopyN(pw, br, size); err != nil { pw.CloseWithError(err); return }
			if _, err := br.ReadString('\n'); err != nil { pw.CloseWithError(err); return }
		}
	}()
	return pr
}

// ============== 自测 ==============
var sampleGet = []byte("GET /index.html HTTP/1.1\r\n" +
	"Host: example.com\r\n" +
	"User-Agent: demo/1.0\r\n" +
	"Accept: text/html, application/xhtml+xml\r\n" +
	"X-Multi: a\r\n" +
	"X-Multi: b\r\n" +
	"Content-Length: 0\r\n" +
	"\r\n")

var sampleChunked = []byte("POST /upload HTTP/1.1\r\n" +
	"Host: example.com\r\n" +
	"Transfer-Encoding: chunked\r\n" +
	"\r\n" +
	"5\r\nhello\r\n" +
	"6\r\n world\r\n" +
	"0\r\n\r\n")

func selfTest() {
	fmt.Println("=== HTTP/1.1 parser self-test ===")
	r, err := ParseRequestHead(sampleGet)
	check(err)
	checkEq(r.Method, "GET"); checkEq(r.Target, "/index.html"); checkEq(r.Version, "HTTP/1.1")
	checkEq(r.Headers["host"], "example.com")
	checkEq(r.Headers["accept"], "text/html, application/xhtml+xml")
	checkEq(r.Headers["x-multi"], "a, b"); checkEq(r.BodyLen, 0)
	fmt.Printf("  [1] GET no body OK  headers=%d\n", len(r.Headers))

	r, err = ParseRequestHead(sampleChunked)
	check(err)
	checkEq(r.BodyLen, -1)
	body, _ := io.ReadAll(decodeChunked(bytes.NewReader(sampleChunked[r.BodyStart:])))
	checkEq(string(body), "hello world")
	fmt.Printf("  [2] chunked OK  body=%q\n", body)

	bareLF := []byte("GET / HTTP/1.1\nHost: x\nAccept: */*\n\n")
	r, err = ParseRequestHead(bareLF)
	check(err)
	checkEq(r.Method, "GET"); checkEq(r.Headers["host"], "x")
	fmt.Println("  [3] bare LF tolerated OK")

	leadingCRLF := []byte("\r\nGET / HTTP/1.1\r\nHost: x\r\n\r\n")
	r, err = ParseRequestHead(leadingCRLF)
	check(err)
	checkEq(r.Method, "GET")
	fmt.Println("  [4] leading CRLF ignored OK")

	_, err = ParseRequestHead([]byte("GET /only-two\r\n\r\n"))
	if err == nil { log.Fatal("[5] expected bad request-line error") }
	fmt.Printf("  [5] bad request-line detected: %v\n", err)

	fmt.Println("all self-tests passed.")
}

func checkEq[T comparable](got, want T) {
	if got != want { log.Fatalf("mismatch: got %v want %v", got, want) }
}

func check(err error) { if err != nil { log.Fatal(err) } }

// ============== mini server ==============
func miniServer(port int) {
	ln, err := net.Listen("tcp", ":"+strconv.Itoa(port))
	if err != nil { log.Fatal(err) }
	defer ln.Close()
	log.Printf("[mini server] listening on %s", ln.Addr())
	for {
		conn, err := ln.Accept()
		if err != nil { return }
		go handle(conn)
	}
}

func handle(conn net.Conn) {
	defer conn.Close()
	peer := conn.RemoteAddr().String()
	log.Printf("[mini server] accept %s", peer)
	br := bufio.NewReader(conn)
	_ = conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	var buf bytes.Buffer
	for {
		chunk, err := br.ReadBytes('\n')
		if err != nil { return }
		buf.Write(chunk)
		if buf.Len() > maxHeaderBytes {
			_, _ = conn.Write([]byte("HTTP/1.1 413 Payload Too Large\r\n\r\n"))
			return
		}
		if strings.HasSuffix(buf.String(), "\r\n\r\n") { break }
	}
	r, err := ParseRequestHead(buf.Bytes())
	if err != nil {
		log.Printf("  parse err: %v", err)
		_, _ = conn.Write([]byte("HTTP/1.1 400 Bad Request\r\n\r\n"))
		return
	}
	log.Printf("  method=%s target=%s version=%s", r.Method, r.Target, r.Version)
	for k, v := range r.Headers {
		log.Printf("  %15s: %s", k, v)
	}
	body := []byte("hello from go http parser\n")
	resp := "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: " +
		strconv.Itoa(len(body)) + "\r\nConnection: close\r\n\r\n"
	_, _ = conn.Write([]byte(resp))
	_, _ = conn.Write(body)
}

func main() {
	if len(os.Args) >= 2 {
		switch os.Args[1] {
		case "test":
			selfTest(); return
		case "serve":
			if len(os.Args) != 3 {
				fmt.Fprintln(os.Stderr, "usage: serve <port>")
				os.Exit(1)
			}
			p, _ := strconv.Atoi(os.Args[2])
			miniServer(p); return
		}
	}
	fmt.Fprintln(os.Stderr, "usage: http_parser test | serve <port>")
	os.Exit(1)
}