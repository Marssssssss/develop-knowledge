/*
 * Go reactor using goroutines + net.Conn:
 *
 * In Go the canonical "single-threaded reactor" is what `net` already gives
 * us inside one OS thread: each Conn has blocking Read/Write, and the runtime
 * parks the goroutine on I/O. This demo deliberately exposes the dispatch
 * mechanics so you can map a Linux epoll Reactor to the equivalent in Go.
 *
 *   go build -o reactor reactor.go && ./reactor 9000
 */
package main

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"sync"
	"time"
)

// --- Reactor participants (mirroring Schmidt 1995 OO pattern) ---

// Synchronous Event Demultiplexer abstraction: in Go this is the runtime
// net poller. Each Listener.Accept() returns a Conn that parks its goroutine
// on readiness.
type EventDemultiplexer interface {
	Listen(addr string) (net.Listener, error)
}

// Concrete demultiplexer backed by net.Listen.
type TcpDemux struct{}

func (TcpDemux) Listen(addr string) (net.Listener, error) {
	return net.Listen("tcp", addr)
}

// --- Event Handler interface ---

type Handler interface {
	OnAccept(c net.Conn)
	OnRead(c net.Conn, line []byte)
	OnClose(c net.Conn)
}

// EchoHandler implements Handler.
type EchoHandler struct{}

// Sessions keeps a map for lifetime management (mirrors Schmidt's "handle").
type Sessions struct {
	mu sync.Mutex
	m  map[net.Conn]struct{}
}

func NewSessions() *Sessions { return &Sessions{m: map[net.Conn]struct{}{}} }

func (s *Sessions) Add(c net.Conn) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.m[c] = struct{}{}
}

func (s *Sessions) Remove(c net.Conn) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.m, c)
}

func (s *Sessions) Count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.m)
}

var _sessions = NewSessions()

func (h EchoHandler) OnAccept(c net.Conn) {
	fmt.Printf("accept %s (sessions=%d)\n", c.RemoteAddr(), _sessions.Count())
	_sessions.Add(c)
}

func (h EchoHandler) OnRead(c net.Conn, line []byte) {
	// simple line-oriented echo
	c.Write(append([]byte("echo:"), line...))
}

func (h EchoHandler) OnClose(c net.Conn) {
	fmt.Printf("close %s\n", c.RemoteAddr())
	_sessions.Remove(c)
}

// --- Reactor core ---
// In Go, the main loop is hidden inside net.Listener.Accept; each connection
// spawns a goroutine, which is the natural mapping of "dispatcher hands off
// to handler". The explicit Accept loop below makes the mapping legible.
type Reactor struct {
	dmux     EventDemultiplexer
	handler  Handler
	listener net.Listener
	running  bool
}

func NewReactor(dmux EventDemultiplexer, h Handler) *Reactor {
	return &Reactor{dmux: dmux, handler: h}
}

func (r *Reactor) Start(addr string) error {
	l, err := r.dmux.Listen(addr)
	if err != nil {
		return err
	}
	r.listener = l
	r.running = true
	fmt.Printf("reactor listening on %s\n", addr)
	for r.running {
		// Phase 1: Synchronous Event Demultiplexer
		conn, err := l.Accept()
		if err != nil {
			if !r.running {
				return nil
			}
			fmt.Fprintln(os.Stderr, "accept:", err)
			time.Sleep(100 * time.Millisecond) // backoff on accept storms
			continue
		}
		// Phase 2: dispatch to handler in its own goroutine
		r.handler.OnAccept(conn)
		go r.serve(conn)
	}
	return nil
}

func (r *Reactor) serve(c net.Conn) {
	defer c.Close()
	defer r.handler.OnClose(c)
	r := bufio.NewReader(c)
	for {
		line, err := r.ReadBytes('\n')
		if len(line) > 0 {
			r.handler.OnRead(c, line)
		}
		if err != nil {
			return
		}
	}
}

func (r *Reactor) Stop() {
	r.running = false
	if r.listener != nil {
		r.listener.Close()
	}
}

func main() {
	addr := ":9000"
	if len(os.Args) > 1 {
		addr = ":" + os.Args[1]
	}
	rc := NewReactor(TcpDemux{}, EchoHandler{})
	if err := rc.Start(addr); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
