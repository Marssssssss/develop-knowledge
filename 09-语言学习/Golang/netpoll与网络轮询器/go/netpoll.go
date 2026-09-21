// Package main 用 Go 复刻 runtime 的网络轮询器状态机（src/runtime/netpoll.go）。
//
// 官方实现里 rg/wg 是 atomic.Uintptr、pd 有 mutex、还有写屏障与 netpollWaiters 原子计数；
// 这里全部剥掉，只保留**状态机与计数**，便于单线程观察。函数名与官方一一对应。
package main

import "fmt"

// 二值信号量的四个取值。官方注释：rg/wg 里存的是 pdNil / pdReady / pdWait / *g。
const (
	pdNil uintptr = iota
	pdReady
	pdWait
)

// gBase：G 指针用大于 pdWait 的值模拟。
const gBase = 1000

// mode 用的是字符常量：'r' / 'w' / 'r'+'w'。
const (
	modeR  = 'r'
	modeW  = 'w'
	modeRW = 'r' + 'w'
)

// 错误码。
const (
	pollNoError = iota
	pollErrClosing
	pollErrTimeout
	pollErrNotPollable
)

const maxDeadline = 1<<63 - 1

// PollDesc 对应 runtime.pollDesc。
type PollDesc struct {
	fd      uintptr
	rg, wg  uintptr
	closing bool
	rd, wd  int64 // 绝对时刻；-1 表示已过期
	expiredRead, expiredWrite bool
	rrun, wrun   bool
	rseq, wseq   uintptr
	fdseq        uintptr

	parked []uintptr // 模型附加
	ready  []uintptr // 模型附加
	nextG  uintptr   // 模型附加
}

func (pd *PollDesc) newG() uintptr {
	pd.nextG++
	if pd.nextG < gBase {
		pd.nextG = gBase + 1
	}
	return pd.nextG
}

func (pd *PollDesc) load(mode int) uintptr {
	if mode == modeR {
		return pd.rg
	}
	return pd.wg
}

func (pd *PollDesc) store(mode int, v uintptr) {
	if mode == modeR {
		pd.rg = v
		return
	}
	pd.wg = v
}

// Netpoll 对应全局 netpollWaiters 等状态。
type Netpoll struct {
	Waiters int32
	WakeSig uint32 // netpollWakeSig：0/1，用于 netpollBreak 去重
}

// AdjustWaiters 对应 netpollAdjustWaiters。
func (np *Netpoll) AdjustWaiters(delta int32) {
	if delta != 0 {
		np.Waiters += delta
	}
}

// netpollBreak 对应 netpoll_epoll.go 的 netpollBreak：CAS(0,1) 去重。
func (np *Netpoll) netpollBreak() bool {
	if np.WakeSig == 0 {
		np.WakeSig = 1
		return true
	}
	return false
}

// netpollcheckerr 对应 netpoll.go 的 netpollcheckerr。
func netpollcheckerr(pd *PollDesc, mode int) int {
	if pd.closing {
		return pollErrClosing
	}
	if (mode == modeR && pd.expiredRead) || (mode == modeW && pd.expiredWrite) {
		return pollErrTimeout
	}
	return pollNoError
}

// netpollblock 对应 netpoll.go 的 netpollblock。
func netpollblock(np *Netpoll, pd *PollDesc, mode int, waitio bool) bool {
	for {
		// 消费已挂起的通知
		if pd.load(mode) == pdReady {
			pd.store(mode, pdNil)
			return true
		}
		if pd.load(mode) == pdNil {
			pd.store(mode, pdWait)
			break
		}
		if pd.load(mode) != pdReady {
			panic("runtime: double wait")
		}
	}
	// gopark(netpollblockcommit...)：commit 里 CAS(pdWait → gp)。
	// gopark 之后的代码只在被唤醒后执行，所以「仍是 G 指针」= 还没人唤醒。
	if waitio || netpollcheckerr(pd, mode) == pollNoError {
		g := pd.newG()
		if pd.load(mode) == pdWait {
			pd.store(mode, g)
			np.AdjustWaiters(1)
			pd.parked = append(pd.parked, g)
		}
		if pd.load(mode) > pdWait {
			return false // 仍处于阻塞态
		}
	}
	old := pd.load(mode)
	pd.store(mode, pdNil)
	if old > pdWait {
		panic("runtime: corrupted polldesc")
	}
	return old == pdReady
}

// netpollunblock 对应 netpoll.go 的 netpollunblock。
func netpollunblock(pd *PollDesc, mode int, ioready bool, delta int32) (uintptr, int32) {
	old := pd.load(mode)
	if old == pdReady {
		return 0, delta
	}
	if old == pdNil && !ioready {
		// 只有真 IO 就绪才设 pdReady；超时/关闭留给 pollWait 自己检查
		return 0, delta
	}
	n := pdNil
	if ioready {
		n = pdReady
	}
	pd.store(mode, n)
	if old == pdWait {
		old = pdNil
	} else if old != pdNil {
		delta--
	}
	if old > pdWait {
		return old, delta
	}
	return 0, delta
}

// netpollready 对应 netpoll.go 的 netpollready。
func netpollready(np *Netpoll, pd *PollDesc, mode int) ([]uintptr, int32) {
	var toRun []uintptr
	var delta int32
	if mode == modeR || mode == modeRW {
		g, d := netpollunblock(pd, modeR, true, delta)
		delta = d
		if g != 0 {
			toRun = append(toRun, g)
		}
	}
	if mode == modeW || mode == modeRW {
		g, d := netpollunblock(pd, modeW, true, delta)
		delta = d
		if g != 0 {
			toRun = append(toRun, g)
		}
	}
	np.AdjustWaiters(delta)
	return toRun, delta
}

// pollRuntimePollReset 对应 poll_runtime_pollReset。
func pollRuntimePollReset(pd *PollDesc, mode int) int {
	if errcode := netpollcheckerr(pd, mode); errcode != pollNoError {
		return errcode
	}
	pd.store(mode, pdNil)
	return pollNoError
}

// pollRuntimePollWait 对应 poll_runtime_pollWait：循环重试直到就绪或出错。
func pollRuntimePollWait(np *Netpoll, pd *PollDesc, mode int) int {
	if errcode := netpollcheckerr(pd, mode); errcode != pollNoError {
		return errcode
	}
	for !netpollblock(np, pd, mode, false) {
		if errcode := netpollcheckerr(pd, mode); errcode != pollNoError {
			return errcode
		}
		// 官方注释：timeout 已唤醒我们但还没跑到，deadline 又被重置 → 重试
	}
	return pollNoError
}

func toInt64(v int64) int64 {
	// Go 的 int64 静默回绕；这里用位运算显式模拟，便于对照
	return v
}

// describe 把信号量值打印成官方注释里的名字。
func describe(v uintptr) string {
	switch v {
	case pdNil:
		return "pdNil"
	case pdReady:
		return "pdReady"
	case pdWait:
		return "pdWait"
	}
	return fmt.Sprintf("G(%d)", v)
}
