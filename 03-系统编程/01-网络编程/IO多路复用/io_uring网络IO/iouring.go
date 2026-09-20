// io_uring 网络 IO —— Go 版模型
//
// 与 iouring_model.py 同题。Go 侧重点：res 的 -errno 单通道报错在 Go 里
// 需要显式还原成 error，正好对照传统 errno 双通道。
package main

import (
	"errors"
	"fmt"
)

// errno（Linux/x86-64）
const (
	EAgain      = 11
	EConnReset  = 104
	EInval      = 22
)

// 操作码
const (
	OpAccept = iota
	OpRecv
	OpSend
	OpPollAdd
	OpRead
)

var opNames = map[int]string{
	OpAccept: "ACCEPT", OpRecv: "RECV", OpSend: "SEND",
	OpPollAdd: "POLL_ADD", OpRead: "READ",
}

const (
	IosqeIoLink        = 1 << 0
	IoringSetupSqPoll  = 1 << 1
	IoringSetupIoPoll  = 1 << 0
	IoringSqNeedWakeup = 1 << 0
	IoringEnterGetevents = 1
)

// SQE 提交队列项
type SQE struct {
	Opcode   int
	Fd       int
	UserData int
	Flags    int
	BufLen   int
	Payload  []byte
}

// CQE 完成队列项：res 为系统调用返回值，出错时为 -errno
type CQE struct {
	UserData int
	Res      int
	Opcode   int
}

func (c CQE) Ok() bool    { return c.Res >= 0 }
func (c CQE) Errno() int  { if c.Res < 0 { return -c.Res }; return 0 }

// Sock 被建模的 socket
type Sock struct {
	Fd      int
	Rcv     []byte
	Backlog []int
	Reset   bool
}

// Ring io_uring 实例
type Ring struct {
	Entries      int
	SetupFlags   int
	SqThreadIdle float64
	Sq           []*SQE
	Cq           []CQE
	Pending      []*SQE
	SqFlags      int
	IdleSince    float64
	ThreadAsleep bool
	Syscalls     int
	ExecLog      []int
	Reverse      bool
	Socks        map[int]*Sock
}

func NewRing(entries, flags int, idle float64) *Ring {
	return &Ring{Entries: entries, SetupFlags: flags, SqThreadIdle: idle,
		Socks: map[int]*Sock{}}
}

func (r *Ring) SqPoll() bool { return r.SetupFlags&IoringSetupSqPoll != 0 }
func (r *Ring) IoPoll() bool { return r.SetupFlags&IoringSetupIoPoll != 0 }

// GetSqe 准备一个 SQE；IOPOLL 环上禁止网络操作
func (r *Ring) GetSqe(opcode, fd, userData int) (*SQE, error) {
	if r.IoPoll() && opcode != OpRead {
		return nil, errors.New("IORING_SETUP_IOPOLL 只用于 O_DIRECT 的 READ/WRITE")
	}
	return &SQE{Opcode: opcode, Fd: fd, UserData: userData}, nil
}

// Prep 把 SQE 放到 SQ 尾部（纯用户态内存写，不算系统调用）
func (r *Ring) Prep(s *SQE) { r.Sq = append(r.Sq, s) }

// Submit 对应 io_uring_enter(2)
func (r *Ring) Submit(minComplete, flags int) int {
	r.Syscalls++
	r.IdleSince = 0
	r.ThreadAsleep = false
	r.SqFlags &^= IoringSqNeedWakeup
	return r.runKernel(minComplete, flags)
}

// SqPollTick 内核轮询线程自己消费 SQ，应用不进内核
func (r *Ring) SqPollTick() int {
	if !r.SqPoll() || r.ThreadAsleep {
		return 0
	}
	return r.runKernel(0, 0)
}

func (r *Ring) runKernel(minComplete, flags int) int {
	batch := r.Sq
	r.Sq = nil
	chains := splitChains(batch)
	groups := [][]CQE{}
	for _, chain := range chains {
		results := []CQE{}
		for _, sqe := range chain {
			r.ExecLog = append(r.ExecLog, sqe.UserData)
			if cqe, ok := r.doIO(sqe); ok {
				results = append(results, cqe)
			}
		}
		groups = append(groups, results)
	}
	if r.Reverse {
		for i, j := 0, len(groups)-1; i < j; i, j = i+1, j-1 {
			groups[i], groups[j] = groups[j], groups[i]
		}
	}
	for _, g := range groups {
		r.Cq = append(r.Cq, g...)
	}
	if flags&IoringEnterGetevents != 0 && minComplete > len(r.Cq) {
		r.flushPending()
	}
	return len(batch)
}

// splitChains IOSQE_IO_LINK 把 SQE 与下一个绑成链；未链接的自成一组
func splitChains(batch []*SQE) [][]*SQE {
	chains := [][]*SQE{}
	cur := []*SQE{}
	for i, sqe := range batch {
		cur = append(cur, sqe)
		if sqe.Flags&IosqeIoLink != 0 && i != len(batch)-1 {
			continue
		}
		chains = append(chains, cur)
		cur = []*SQE{}
	}
	if len(cur) > 0 {
		chains = append(chains, cur)
	}
	return chains
}

func (r *Ring) doIO(s *SQE) (CQE, bool) {
	sock, ok := r.Socks[s.Fd]
	if !ok {
		sock = &Sock{Fd: s.Fd}
		r.Socks[s.Fd] = sock
	}
	if sock.Reset {
		return CQE{s.UserData, -EConnReset, s.Opcode}, true
	}
	switch s.Opcode {
	case OpRecv:
		if len(sock.Rcv) == 0 {
			r.Pending = append(r.Pending, s)
			return CQE{}, false
		}
		n := s.BufLen
		if len(sock.Rcv) < n {
			n = len(sock.Rcv)
		}
		sock.Rcv = sock.Rcv[n:]
		return CQE{s.UserData, n, s.Opcode}, true
	case OpSend:
		return CQE{s.UserData, len(s.Payload), s.Opcode}, true
	case OpAccept:
		if len(sock.Backlog) == 0 {
			r.Pending = append(r.Pending, s)
			return CQE{}, false
		}
		fd := sock.Backlog[0]
		sock.Backlog = sock.Backlog[1:]
		return CQE{s.UserData, fd, s.Opcode}, true
	}
	return CQE{s.UserData, -EInval, s.Opcode}, true
}

func (r *Ring) flushPending() {
	still := []*SQE{}
	for _, sqe := range r.Pending {
		if cqe, ok := r.doIO(sqe); ok {
			r.Cq = append(r.Cq, cqe)
		} else {
			still = append(still, sqe)
		}
	}
	r.Pending = still
}

// Feed 模拟网络侧到达
func (r *Ring) Feed(fd int, data []byte) {
	sock := r.Socks[fd]
	if sock == nil {
		sock = &Sock{Fd: fd}
		r.Socks[fd] = sock
	}
	sock.Rcv = append(sock.Rcv, data...)
	r.flushPending()
}

func (r *Ring) ReapAll() []CQE {
	out := r.Cq
	r.Cq = nil
	return out
}

// Tick 推进时间，模拟轮询线程空闲超时
func (r *Ring) Tick(dt float64) {
	if !r.SqPoll() {
		return
	}
	if len(r.Sq) == 0 && len(r.Pending) == 0 {
		r.IdleSince += dt
		if r.IdleSince > r.SqThreadIdle {
			r.ThreadAsleep = true
			r.SqFlags |= IoringSqNeedWakeup
		}
	} else {
		r.IdleSince = 0
		r.ThreadAsleep = false
		r.SqFlags &^= IoringSqNeedWakeup
	}
}

func (r *Ring) NeedWakeup() bool { return r.SqFlags&IoringSqNeedWakeup != 0 }
