// Package main 是 urg_model.py 的 Go 转写。
//
// 转写时显式落地的语言差异:
//   * 内核的 before()/after() 依赖 u32 的无符号回绕;Go 里用 int64 保存
//     序列号,并用 Sub 手动取模到 32 位再比最高位,不依赖 uint32 的自动回绕。
//   * Python 的 `self.urg_data == TCP_URG_READ` 是**精确相等**比较(源码
//     就是 `urg_data == TCP_URG_READ`,不是按位与),Go 侧同样用 ==。
package main

const (
	mask     = int64(0xFFFFFFFF)
	signBit  = int64(0x80000000)
	maskPlus = int64(0x100000000)

	urgValid  = 0x0100
	urgNotYet = 0x0200
	urgRead   = 0x0400

	eInval   = 22
	eNotConn = 107
	msgOob   = 0x01
	msgTrunc = 0x20

	epollPri = 0x002
)

// Before 内核的 (s32)(a-b) < 0。
func Before(a, b int64) bool { return ((a-b)%maskPlus+maskPlus)%maskPlus >= signBit }

// After 内核的 (s32)(b-a) < 0。
func After(a, b int64) bool { return Before(b, a) }

// SegOut 收一个段的结果。
type SegOut struct {
	Result string
	SigUrg bool
	Byte   int
	HasByte bool
}

// TcpUrgSock 一个 TCP 接收端的紧急数据状态。
type TcpUrgSock struct {
	CopiedSeq, RcvNxt, UrgSeq int64
	UrgData                   int
	OobInline, StdUrg         bool
	SockDone                  bool
	State                     string
	SigUrg                    int
	RecvShutdown              bool
}

func newUrgSock(copiedSeq, rcvNxt int64, oobInline, stdUrg bool) *TcpUrgSock {
	return &TcpUrgSock{CopiedSeq: copiedSeq & mask, RcvNxt: rcvNxt & mask,
		OobInline: oobInline, StdUrg: stdUrg, State: "ESTABLISHED"}
}

// OnSegment 转写 tcp_check_urg() + tcp_urg()。
func (s *TcpUrgSock) OnSegment(seq int64, urgPtr int, payload []byte,
	doff int, syn int) SegOut {
	out := SegOut{Result: "no_urg"}
	if urgPtr != 0 {
		ptr := int64(urgPtr)
		if ptr != 0 && !s.StdUrg {
			ptr--
		}
		ptr = (ptr + seq) & mask
		if After(s.CopiedSeq, ptr) {
			out.Result = "ignored_already_read"
			return out
		}
		if Before(ptr, s.RcvNxt) {
			out.Result = "ignored_replay"
			return out
		}
		if s.UrgData != 0 && !After(ptr, s.UrgSeq) {
			out.Result = "ignored_duplicate"
			return out
		}
		s.SigUrg++
		out.SigUrg = true
		if s.UrgSeq == s.CopiedSeq && s.UrgData != 0 && !s.OobInline &&
			s.CopiedSeq != s.RcvNxt {
			s.CopiedSeq = (s.CopiedSeq + 1) & mask
			out.Result = "accepted_advance_copied_seq"
		} else {
			out.Result = "accepted"
		}
		s.UrgData = urgNotYet
		s.UrgSeq = ptr
	}
	if s.UrgData == urgNotYet {
		hdr := int64(doff * 4)
		p := s.UrgSeq - seq + hdr - int64(syn)
		skbLen := hdr + int64(len(payload))
		if p >= 0 && p < skbLen {
			off := p - hdr + int64(syn)
			b := int(payload[off])
			s.UrgData = urgValid | b
			out.Byte = b
			out.HasByte = true
		}
	}
	return out
}

// Sockatmark tcp_ioctl():urg_data && urg_seq == copied_seq。
func (s *TcpUrgSock) Sockatmark() int {
	if s.UrgData != 0 && s.UrgSeq == s.CopiedSeq {
		return 1
	}
	return 0
}

// Inq tcp_inq():有未处理紧急数据时截断到标记处。
func (s *TcpUrgSock) Inq() int64 {
	if s.OobInline || s.UrgData == 0 || Before(s.UrgSeq, s.CopiedSeq) ||
		!Before(s.UrgSeq, s.RcvNxt) {
		answ := (s.RcvNxt - s.CopiedSeq) & mask
		if answ != 0 && s.SockDone {
			answ--
		}
		return answ
	}
	return (s.UrgSeq - s.CopiedSeq) & mask
}

// RecvLimit `used = urg_offset` 截断,与 OobInline 无关。
func (s *TcpUrgSock) RecvLimit(seq int64, avail int64) int64 {
	if s.UrgData != 0 {
		off := (s.UrgSeq - seq) & mask
		if off < avail {
			return off
		}
	}
	return avail
}

// ReadChunk 返回 (拷贝到用户态的字节数, urg_hole)。
func (s *TcpUrgSock) ReadChunk(seq int64, avail int64) (int64, int) {
	if s.UrgData == 0 {
		return avail, 0
	}
	off := (s.UrgSeq - seq) & mask
	if off >= avail {
		return avail, 0
	}
	if off == 0 {
		if s.OobInline {
			return avail, 0
		}
		return avail - 1, 1
	}
	return off, 0
}

// RecvOob tcp_recv_urg():返回 (返回值, msg_flags)。
func (s *TcpUrgSock) RecvOob(length int, peek bool) (int, int) {
	if s.OobInline || s.UrgData == 0 || s.UrgData == urgRead {
		return -eInval, 0
	}
	if s.State == "CLOSE" && !s.SockDone {
		return -eNotConn, 0
	}
	if (s.UrgData & urgValid) != 0 {
		if !peek {
			s.UrgData = urgRead
		}
		if length > 0 {
			return 1, msgOob
		}
		return 0, msgOob | msgTrunc
	}
	return 0, 0
}

// EpollMask tcp_poll():EPOLLPRI 与 rcvlowat 目标的 +1。
func (s *TcpUrgSock) EpollMask(target int) (int, int) {
	t := target
	m := 0
	if s.UrgData != 0 && s.UrgSeq == s.CopiedSeq && !s.OobInline {
		t++
	}
	if (s.UrgData & urgValid) != 0 {
		m |= epollPri
	}
	return m, t
}
