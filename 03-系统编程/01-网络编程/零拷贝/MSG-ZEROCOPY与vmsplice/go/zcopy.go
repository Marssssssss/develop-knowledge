// Package main 是 zcopy_model.py 的 Go 转写。
//
// 显式落地的语言差异:
//   * 内核的 counter 是 unsigned 32 位,Go 侧用 int64 保存并手动取模
//     (UINT32_MOD),避免依赖 uint32 的回绕语义带来的阅读歧义。
//   * Python 的 pop(0) 对应 Go 的 q[0] + q = q[1:],注意不要复用底层数组。
package main

const (
	iovMax    = 1024
	pageSize  = 4096
	uint32Mod = int64(0x100000000)
	uint32Max = int64(0xFFFFFFFF)

	soEeOriginZerocopy     = 5
	soEeCodeZerocopyCopied = 1

	eNoBufs = 105
	eBadFd  = 9
	eInval  = 22
	eAgain  = 11

	spliceFMove     = 1
	spliceFNonblock = 2
	spliceFMore     = 4
	spliceFGift     = 8
)

// ExtErr 对应 struct sock_extended_err 中与零拷贝通知相关的字段。
type ExtErr struct {
	Errno  int
	Origin int
	Code   int
	Info   int64 // ee_info = 区间下界
	Data   int64 // ee_data = 区间上界
}

// Range 一条 outstanding 通知的闭区间 [Lo, Hi]。
type Range struct {
	Lo, Hi int64
	Copied bool
}

// ToExtErr 转成用户空间看到的那几个字段。ee_errno 恒为 0。
func (r *Range) ToExtErr() ExtErr {
	code := 0
	if r.Copied {
		code = soEeCodeZerocopyCopied
	}
	return ExtErr{Errno: 0, Origin: soEeOriginZerocopy, Code: code,
		Info: r.Lo, Data: r.Hi}
}

// ZerocopySocket 一个开了 SO_ZEROCOPY 的 TCP socket 的通知记账。
type ZerocopySocket struct {
	ZcEnabled bool
	Loopback  bool
	Counter   int64
	Queue     []Range
}

// Send 返回 (返回值, 本次分配的 counter, 是否分配了 counter)。
// 未开 SO_ZEROCOPY / length == 0 / 失败 三种情况都不分配。
func (s *ZerocopySocket) Send(length int, zerocopy, enobufs bool) (int, int64, bool) {
	if !(s.ZcEnabled && zerocopy) {
		return length, 0, false
	}
	if length == 0 {
		return 0, 0, false
	}
	if enobufs {
		return -eNoBufs, 0, false
	}
	s.Counter = (s.Counter + 1) % uint32Mod
	return length, s.Counter, true
}

// Complete 内核释放共享页时入队通知;返回 true 表示新起了一个包。
func (s *ZerocopySocket) Complete(value int64, copied *bool) bool {
	c := s.Loopback
	if copied != nil {
		c = *copied
	}
	if n := len(s.Queue); n > 0 {
		tail := &s.Queue[n-1]
		if (tail.Hi+1)%uint32Mod == value {
			tail.Hi = value
			tail.Copied = tail.Copied || c
			return false
		}
	}
	s.Queue = append(s.Queue, Range{Lo: value, Hi: value, Copied: c})
	return true
}

// RecvErrqueue 取走队首通知;空队列返回 (ExtErr{}, false)。
func (s *ZerocopySocket) RecvErrqueue() (ExtErr, bool) {
	if len(s.Queue) == 0 {
		return ExtErr{}, false
	}
	r := s.Queue[0]
	s.Queue = s.Queue[1:]
	return r.ToExtErr(), true
}

// Outstanding 当前 outstanding 通知条数。
func (s *ZerocopySocket) Outstanding() int { return len(s.Queue) }

// Iovec 一段用户内存:虚拟地址 + 长度。
type Iovec struct {
	Base, Length int
}

// Vmsplice 返回 (传输字节数, errno)。
func Vmsplice(fdIsPipe bool, iovs []Iovec, flags int, wouldBlock bool) (int, int) {
	if !fdIsPipe {
		return -1, eBadFd
	}
	if len(iovs) > iovMax {
		return -1, eInval
	}
	if wouldBlock && (flags&spliceFNonblock) != 0 {
		return -1, eAgain
	}
	if (flags & spliceFGift) != 0 {
		for _, iv := range iovs {
			if iv.Base%pageSize != 0 || iv.Length%pageSize != 0 {
				return -1, eInval
			}
		}
	}
	total := 0
	for _, iv := range iovs {
		total += iv.Length
	}
	return total, 0
}

// VmspliceDirection 写端是真 splice,读端实际只是拷贝。
func VmspliceDirection(fdOpenedForWrite bool) string {
	if fdOpenedForWrite {
		return "splice"
	}
	return "copy"
}

// ZerocopyIsWorthIt 文档只给「around 10 KB」这个量级,没有精确阈值。
func ZerocopyIsWorthIt(n int) bool { return n > 10*1024 }
