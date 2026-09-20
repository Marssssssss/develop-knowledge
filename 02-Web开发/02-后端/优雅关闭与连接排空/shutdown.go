// Go 侧的对照实现：把 net/http 的 Shutdown/Close 语义抽成可检视的状态机。
//
// 与 Python 模型同构，差异只在于这里用 Go 原生的 sync/atomic 表达 inShutdown，
// 以便演示「doKeepAlives 读的是 shuttingDown()，而不是 disableKeepAlives」这一真实依赖。
//
// 官方依据（golang/go src/net/http/server.go）：
//   - Shutdown: inShutdown.Store(true) -> closeListenersLocked -> go f() -> listenerGroup.Wait()
//     -> for { if closeIdleConns() { return lnerr }; select { ctx.Done / timer.C } }
//   - 轮询: base 1ms 起, interval = base + rand.IntN(base/10), base *= 2, 夹到 500ms
//   - closeIdleConns: StateNew 且 unixSec < now-5 视为 idle；unixSec == 0 视为尚未置状态
//   - Close: 无差别 close 掉 activeConn 里的所有连接
package main

import (
	"fmt"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

// ConnState 对应 net/http 的 ConnState。
type ConnState int

const (
	StateNew ConnState = iota
	StateActive
	StateIdle
)

func (s ConnState) String() string {
	return [...]string{"StateNew", "StateActive", "StateIdle"}[s]
}

const (
	shutdownPollIntervalMax = 500 * time.Millisecond
	stateNewIdleAfter       = 5 * time.Second
)

// Conn 是一条已被 Serve 登记的连接。
type Conn struct {
	ID       string
	state    ConnState
	unixSec  time.Time // 进入当前状态的时刻；零值表示尚未置状态
	closed   bool
	closedBy string
}

func (c *Conn) getState() (ConnState, time.Time) { return c.state, c.unixSec }

// Listener 只保留 Shutdown 关心的 closed 语义。
type Listener struct {
	Name    string
	closed  bool
	mu      sync.Mutex
	accepts int
}

func (l *Listener) Close() { l.closed = true }

func (l *Listener) Accept() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.accepts++
	return !l.closed
}

// Server 是 net/http.Server 关机相关字段的最小复刻。
type Server struct {
	mu        sync.Mutex
	listeners map[string]*Listener
	activeCon map[string]*Conn

	inShutdown        atomic.Bool
	disableKeepAlives atomic.Bool

	onShutdown []func()
	hookRan    []string

	PollLog []PollEvent // 每次 closeIdleConns 的时刻与随后等待的间隔
}

// PollEvent 记录一次轮询。
type PollEvent struct {
	At       time.Duration // 相对 Shutdown 开始的时刻
	Interval time.Duration
}

// NewServer 建立空 server。
func NewServer() *Server {
	return &Server{listeners: map[string]*Listener{}, activeCon: map[string]*Conn{}}
}

// TrackListener 对应 trackListener：关机中返回 false。
func (s *Server) TrackListener(l *Listener, add bool) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if add {
		if s.shuttingDown() {
			return false
		}
		s.listeners[l.Name] = l
		return true
	}
	delete(s.listeners, l.Name)
	return true
}

// TrackConn 对应 trackConn。
func (s *Server) TrackConn(c *Conn, add bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if add {
		s.activeCon[c.ID] = c
	} else {
		delete(s.activeCon, c.ID)
	}
}

func (s *Server) shuttingDown() bool { return s.inShutdown.Load() }

// DoKeepAlives 对应 doKeepAlives：!disableKeepAlives && !shuttingDown()。
func (s *Server) DoKeepAlives() bool {
	return !s.disableKeepAlives.Load() && !s.shuttingDown()
}

// SetKeepAlivesEnabled 对应 SetKeepAlivesEnabled —— 注意它只改 disableKeepAlives，
// 无法把已经进入 shutdown 的 server 拉回「可以用 keep-alive」的状态。
func (s *Server) SetKeepAlivesEnabled(v bool) { s.disableKeepAlives.Store(!v) }

// RegisterOnShutdown 对应 RegisterOnShutdown。
func (s *Server) RegisterOnShutdown(name string, f func()) {
	s.onShutdown = append(s.onShutdown, func() {
		s.hookRan = append(s.hookRan, name)
		f()
	})
}

func (s *Server) closeListenersLocked() {
	for _, l := range s.listeners {
		l.Close()
	}
}

// closeIdleConns 对应 closeIdleConns：返回 true 表示已 quiescent。
func (s *Server) closeIdleConns(now time.Time) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	quiescent := true
	for id, c := range s.activeCon {
		st, unixSec := c.getState()
		if st == StateNew && unixSec.Before(now.Add(-stateNewIdleAfter)) {
			st = StateIdle
		}
		if st != StateIdle || unixSec.IsZero() {
			quiescent = false
			continue
		}
		c.closed = true
		c.closedBy = "Shutdown"
		delete(s.activeCon, id)
	}
	return quiescent
}

// ShutdownResult 描述 Shutdown 的结局。
type ShutdownResult struct {
	Outcome string // "ok" | "ctx"
	Events  []PollEvent
}

// Shutdown 复刻 Shutdown。jitter ∈ [0,1]，映射 rand.IntN(base/10) 的取值范围。
func (s *Server) Shutdown(deadline time.Duration, jitter float64, maxPolls int) ShutdownResult {
	s.inShutdown.Store(true)
	s.mu.Lock()
	s.closeListenersLocked()
	hooks := append([]func(){}, s.onShutdown...)
	s.mu.Unlock()
	for _, f := range hooks {
		f() // 源码是 `go f()`；这里同步执行以便检视顺序
	}

	base := time.Millisecond
	nextInterval := func() time.Duration {
		interval := base + time.Duration(float64(base/10)*jitter)
		base *= 2
		if base > shutdownPollIntervalMax {
			base = shutdownPollIntervalMax
		}
		return interval
	}

	interval := nextInterval()
	var waited time.Duration
	s.PollLog = nil
	for i := 0; i < maxPolls; i++ {
		s.PollLog = append(s.PollLog, PollEvent{At: waited, Interval: interval})
		if s.closeIdleConns(time.Now().Add(waited)) {
			return ShutdownResult{"ok", s.PollLog}
		}
		if waited+interval >= deadline {
			return ShutdownResult{"ctx", s.PollLog}
		}
		waited += interval
		interval = nextInterval()
	}
	return ShutdownResult{"ctx", s.PollLog}
}

// Close 复刻 Close：无差别关掉所有连接，不等它们回到 idle。
func (s *Server) Close() {
	s.inShutdown.Store(true)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closeListenersLocked()
	for id, c := range s.activeCon {
		c.closed = true
		c.closedBy = "Close"
		delete(s.activeCon, id)
	}
}

// ActiveIDs 返回仍未关闭的连接 id（排序后便于比对）。
func (s *Server) ActiveIDs() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]string, 0, len(s.activeCon))
	for id := range s.activeCon {
		out = append(out, id)
	}
	sort.Strings(out)
	return out
}

func main() {
	build := func() (*Server, map[string]*Conn, *Listener) {
		s := NewServer()
		l := &Listener{Name: "ln0"}
		s.TrackListener(l, true)
		now := time.Now()
		conns := map[string]*Conn{
			"active":    {ID: "active", state: StateActive, unixSec: now.Add(-10 * time.Second)},
			"idle":      {ID: "idle", state: StateIdle, unixSec: now.Add(-time.Second)},
			"stale-new": {ID: "stale-new", state: StateNew, unixSec: now.Add(-6 * time.Second)},
		}
		for _, c := range conns {
			s.TrackConn(c, true)
		}
		return s, conns, l
	}

	s1, c1, _ := build()
	r1 := s1.Shutdown(5*time.Second, 0, 200)
	fmt.Printf("Shutdown(%s) -> active=%v idle.closed=%v staleNew.closed=%v polls=%d\n",
		r1.Outcome, s1.ActiveIDs(), c1["idle"].closed, c1["stale-new"].closed, len(r1.Events))

	s2, c2, _ := build()
	s2.Close()
	fmt.Printf("Close()    -> active=%v active.closed=%v by=%s\n",
		s2.ActiveIDs(), c2["active"].closed, c2["active"].closedBy)

	s3 := NewServer()
	s3.TrackConn(&Conn{ID: "busy", state: StateActive, unixSec: time.Now().Add(-10 * time.Second)}, true)
	r3 := s3.Shutdown(5*time.Second, 0, 200)
	iv := make([]int64, 0, len(r3.Events))
	for _, e := range r3.Events {
		iv = append(iv, e.Interval.Milliseconds())
	}
	fmt.Printf("poll intervals(ms) = %v\n", iv)
}
