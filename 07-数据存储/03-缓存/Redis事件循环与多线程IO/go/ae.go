// Redis ae.c / ae.h 事件循环的 Go 转写。
//
// 语言差异（显式落地）：
//   - C 用函数指针，Go 的 func 值不可比较（只能与 nil 比），因此把回调包装成
//     *FileProc 指针，用指针相等替代 C 的 `fe->wfileProc != fe->rfileProc`。
//   - 真实 epoll 换成注入的 PollFn，保证确定性；tvp 用 *int64，nil = 无限等待。
//   - 单调时钟换成 Clock 接口，测试可手动推进。
package main

const (
	AeNone     = 0
	AeReadable = 1
	AeWritable = 2
	AeBarrier  = 4

	AeFileEvents      = 1 << 0
	AeTimeEvents      = 1 << 1
	AeAllEvents       = AeFileEvents | AeTimeEvents
	AeDontWait        = 1 << 2
	AeCallBeforeSleep = 1 << 3
	AeCallAfterSleep  = 1 << 4

	AeNoMore         = -1
	AeDeletedEventID = -1
	AeOK             = 0
	AeErr            = -1

	InitialEvent = 16
)

// Clock 是可手动推进的单调时钟（微秒）。
type Clock interface {
	GetMonotonicUs() int64
}

// ManualClock 供测试手动推进时间。
type ManualClock struct{ Now int64 }

func (c *ManualClock) GetMonotonicUs() int64 { return c.Now }
func (c *ManualClock) Advance(us int64)      { c.Now += us }

// FileProc 包装一个文件事件回调，指针相等即代表 C 里的函数指针相等。
type FileProc struct {
	Call func(el *EventLoop, fd int, data interface{}, mask int)
}

type FileEvent struct {
	Mask       int
	RFileProc  *FileProc
	WFileProc  *FileProc
	ClientData interface{}
}

type FiredEvent struct {
	Fd   int
	Mask int
}

type TimeEvent struct {
	ID            int64
	When          int64
	TimeProc      func(el *EventLoop, id int64, data interface{}) int64
	FinalizerProc func(el *EventLoop, data interface{})
	ClientData    interface{}
	Prev          *TimeEvent
	Next          *TimeEvent
	Refcount      int
}

type EventLoop struct {
	Maxfd           int
	Setsize         int
	TimeEventNextID int64
	Nevent          int
	Events          []*FileEvent
	TimeEventHead   *TimeEvent
	Stop            bool
	Beforesleep     func(el *EventLoop)
	Aftersleep      func(el *EventLoop)
	Flags           int
	Clock           Clock
	PollFn          func(el *EventLoop, tvp *int64) []FiredEvent

	// 观测点：本轮 poll 实际使用的超时
	TvpSet      bool
	TvpInfinite bool
	TvpUs       int64
}

func NewEventLoop(setsize int, clock Clock, pollFn func(*EventLoop, *int64) []FiredEvent) *EventLoop {
	nevents := setsize
	if nevents > InitialEvent {
		nevents = InitialEvent
	}
	el := &EventLoop{
		Maxfd:   -1,
		Setsize: setsize,
		Nevent:  nevents,
		Stop:    false,
		Flags:   0,
		Clock:   clock,
		PollFn:  pollFn,
	}
	for i := 0; i < nevents; i++ {
		el.Events = append(el.Events, &FileEvent{Mask: AeNone})
	}
	return el
}

func growEvents(el *EventLoop, fd int) {
	newnevents := el.Nevent
	if newnevents*2 > fd+1 {
		newnevents = newnevents * 2
	} else {
		newnevents = fd + 1
	}
	if newnevents > el.Setsize {
		newnevents = el.Setsize
	}
	for len(el.Events) < newnevents {
		el.Events = append(el.Events, &FileEvent{Mask: AeNone})
	}
	el.Nevent = newnevents
}

func AeCreateFileEvent(el *EventLoop, fd int, mask int, proc *FileProc, data interface{}) int {
	if fd >= el.Setsize {
		return AeErr
	}
	if fd >= el.Nevent {
		growEvents(el, fd)
	}
	fe := el.Events[fd]
	fe.Mask |= mask
	if mask&AeReadable != 0 {
		fe.RFileProc = proc
	}
	if mask&AeWritable != 0 {
		fe.WFileProc = proc
	}
	fe.ClientData = data
	if fd > el.Maxfd {
		el.Maxfd = fd
	}
	return AeOK
}

func AeDeleteFileEvent(el *EventLoop, fd int, mask int) {
	if fd >= el.Setsize {
		return
	}
	fe := el.Events[fd]
	if fe.Mask == AeNone {
		return
	}
	// 删除 AE_WRITABLE 时 AE_BARRIER 一并清除
	if mask&AeWritable != 0 {
		mask |= AeBarrier
	}
	fe.Mask = fe.Mask & ^mask
	if fd == el.Maxfd && fe.Mask == AeNone {
		j := fd - 1
		for j >= 0 {
			if el.Events[j].Mask != AeNone {
				break
			}
			j--
		}
		el.Maxfd = j
	}
}

func AeProcessEvents(el *EventLoop, flags int) int {
	processed := 0

	if flags&AeTimeEvents == 0 && flags&AeFileEvents == 0 {
		return 0
	}

	el.TvpSet = false

	if el.Maxfd != -1 || ((flags&AeTimeEvents) != 0 && (flags&AeDontWait) == 0) {
		var tvp *int64

		if el.Beforesleep != nil && flags&AeCallBeforeSleep != 0 {
			el.Beforesleep(el)
		}

		// 参数 flags 优先级高于 eventLoop->flags
		if flags&AeDontWait != 0 || el.Flags&AeDontWait != 0 {
			zero := int64(0)
			tvp = &zero
		} else if flags&AeTimeEvents != 0 {
			us := UsUntilEarliestTimer(el)
			if us >= 0 {
				usCopy := us
				tvp = &usCopy
			}
		}

		el.TvpSet = true
		el.TvpInfinite = tvp == nil
		if tvp != nil {
			el.TvpUs = *tvp
		}

		fired := el.PollFn(el, tvp)

		if flags&AeFileEvents == 0 {
			fired = nil
		}

		if el.Aftersleep != nil && flags&AeCallAfterSleep != 0 {
			el.Aftersleep(el)
		}

		for _, f := range fired {
			fd := f.Fd
			mask := f.Mask
			fe := el.Events[fd]
			nFired := 0
			invert := fe.Mask&AeBarrier != 0

			if !invert && fe.Mask&mask&AeReadable != 0 {
				fe.RFileProc.Call(el, fd, fe.ClientData, mask)
				nFired++
				fe = el.Events[fd] // 回调可能触发扩容，重新取址
			}

			if fe.Mask&mask&AeWritable != 0 {
				if nFired == 0 || fe.WFileProc != fe.RFileProc {
					fe.WFileProc.Call(el, fd, fe.ClientData, mask)
					nFired++
				}
			}

			if invert {
				fe = el.Events[fd]
				if fe.Mask&mask&AeReadable != 0 && (nFired == 0 || fe.WFileProc != fe.RFileProc) {
					fe.RFileProc.Call(el, fd, fe.ClientData, mask)
					nFired++
				}
			}

			processed++
		}
	}

	if flags&AeTimeEvents != 0 {
		processed += ProcessTimeEvents(el)
	}
	return processed
}

func AeMain(el *EventLoop, maxIterations int) int {
	el.Stop = false
	n := 0
	for !el.Stop {
		AeProcessEvents(el, AeAllEvents|AeCallBeforeSleep|AeCallAfterSleep)
		n++
		if maxIterations > 0 && n >= maxIterations {
			break
		}
	}
	return n
}
