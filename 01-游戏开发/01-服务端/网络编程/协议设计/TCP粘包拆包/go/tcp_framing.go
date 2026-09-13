// tcp_framing: TLV / length-prefix streaming parser for TCP, in Go.
//
//   Frame:  ┌──────────┬──────────────┬──────────────┐
//           │ Magic 4B │ Length 4B BE │ Payload:Len  │
//           │ "GAME"   │  u32 BE      │   variable   │
//           └──────────┴──────────────┴──────────────┘
//
// One Conn.read goroutine uses Conn.Read to receive a buffer stream and
// parses every complete frame; messages are then dispatched via a channel
// or back onto the client.
//
//   go build -o tcp_framing tcp_framing.go && ./tcp_framing 9000
package main

import (
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"os"
	"sync"
)

const (
	magic     = "GAME"
	hdrLen    = 8
	maxFrame  = 1 << 20 // 1 MiB
	maxBufLen = 1 << 22 // 4 MiB parser buffer before forced reset
)

// ---------- Frame codec ----------

func writeFrame(c net.Conn, payload []byte) error {
	var hdr [hdrLen]byte
	copy(hdr[0:4], magic)
	binary.BigEndian.PutUint32(hdr[4:8], uint32(len(payload)))
	if _, err := c.Write(hdr[:]); err != nil {
		return err
	}
	if len(payload) > 0 {
		_, err := c.Write(payload)
		return err
	}
	return nil
}

// ---------- Streaming parser ----------

type Parser struct {
	buf      []byte
	need     int   // bytes needed to complete current state
	bodyLen  uint32
	inHeader bool // first turn = header
}

func NewParser() *Parser { return &Parser{inHeader: true, need: hdrLen} }

func (p *Parser) Feed(data []byte) {
	p.buf = append(p.buf, data...)
	if len(p.buf) > maxBufLen {
		// protocol misbehaviour; reset and look for magic afresh
		p.buf = p.buf[:0]
		p.inHeader = true
		p.need = hdrLen
		p.bodyLen = 0
		return
	}
	for {
		if len(p.buf) < p.need {
			return
		}
		if p.inHeader {
			if string(p.buf[:4]) != magic {
				// resync by dropping 1 byte and looking again
				p.buf = p.buf[1:]
				continue
			}
			p.bodyLen = binary.BigEndian.Uint32(p.buf[4:8])
			p.buf = p.buf[hdrLen:]
			if p.bodyLen == 0 || p.bodyLen > maxFrame {
				p.buf = p.buf[:0]
				p.need = hdrLen
				p.bodyLen = 0
				p.inHeader = true
				continue
			}
			p.inHeader = false
			p.need = int(p.bodyLen)
			continue
		}
		// body ready: caller pops via Take()
		return
	}
}

func (p *Parser) Take() []byte {
	if p.inHeader || len(p.buf) < int(p.bodyLen) {
		return nil
	}
	body := p.buf[:p.bodyLen]
	p.buf = p.buf[p.bodyLen:]
	p.inHeader = true
	p.need = hdrLen
	p.bodyLen = 0
	return append([]byte(nil), body...) // defensive copy
}

// ---------- Server ----------

type session struct {
	conn   net.Conn
	parser *Parser
}

var mu sync.Mutex
var sessions = map[net.Conn]*session{}

func handleConn(c net.Conn) {
	mu.Lock()
	s := &session{conn: c, parser: NewParser()}
	sessions[c] = s
	mu.Unlock()
	defer func() {
		mu.Lock()
		delete(sessions, c)
		mu.Unlock()
		c.Close()
	}()

	for {
		buf := make([]byte, 65536)
		n, err := c.Read(buf)
		if n > 0 {
			s.parser.Feed(buf[:n])
			for {
				body := s.parser.Take()
				if body == nil {
					break
				}
				fmt.Printf("fd=%d frame len=%d %q\n", fileDescriptor(c), len(body), body)
				if err := writeFrame(c, body); err != nil {
					return
				}
			}
		}
		if err != nil {
			if err != io.EOF {
				return
			}
			return
		}
	}
}

func fileDescriptor(c net.Conn) string {
	if addr, ok := c.RemoteAddr().(*net.TCPAddr); ok {
		return addr.String()
	}
	return c.RemoteAddr().String()
}

func main() {
	addr := ":9000"
	if len(os.Args) > 1 {
		addr = ":" + os.Args[1]
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		fmt.Fprintln(os.Stderr, "listen:", err)
		os.Exit(1)
	}
	fmt.Printf("tcp_framing listening on %s\n", addr)
	for {
		c, err := ln.Accept()
		if err != nil {
			fmt.Fprintln(os.Stderr, "accept:", err)
			continue
		}
		go handleConn(c)
	}
}
