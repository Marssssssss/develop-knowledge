// Package tcp5961 逐行转写 Linux net/ipv4/tcp_input.c 的
// tcp_validate_incoming / tcp_sequence / tcp_reset_check /
// tcp_send_challenge_ack / tcp_challenge_ack_allowed /
// __tcp_oow_rate_limited，并与 RFC 5961 的规范文本对拍。
//
// 与 Python 版的差异显式落地：
//   - u32 序列号回绕用 uint32 天然溢出，比较统一走 seqBefore/seqAfter；
//   - 内核 (s32)(a-b) 的有符号判定 -> int32(a-b)；
//   - Go 没有可选参数，随机源用 RandU32 接口注入。
package main

import "math"

const (
	mask32 = 0xFFFFFFFF
	intMax = math.MaxInt32

	tcpEstablished = 1
	tcpSynRecv     = 3
	tcpCloseWait   = 5
	tcpLastAck     = 9
	tcpClosing     = 11
)

// resetCheckStates 是 TCPF_CLOSE_WAIT|TCPF_LAST_ACK|TCPF_CLOSING。
const resetCheckStates = (1 << tcpCloseWait) | (1 << tcpLastAck) | (1 << tcpClosing)

func seqBefore(a, b uint32) bool { return int32(a-b) < 0 }
func seqAfter(a, b uint32) bool  { return seqBefore(b, a) }

// RandU32 返回 [lo, hi] 闭区间内的值，对应内核 get_random_u32_inclusive。
type RandU32 func(lo, hi uint32) uint32

func fixedRand(v uint32) RandU32 {
	return func(lo, hi uint32) uint32 {
		if v < lo {
			return lo
		}
		if v > hi {
			return hi
		}
		return v
	}
}

// Netns 对应 net->ipv4 里与 challenge ACK 相关的字段。
type Netns struct {
	ChallengeAckLimit uint32
	InvalidRateLimit uint32
	ChallengeTs      uint32
	ChallengeCount   uint32
}

func NewNetns(challengeAckLimit, invalidRateLimit uint32) *Netns {
	return &Netns{ChallengeAckLimit: challengeAckLimit, InvalidRateLimit: invalidRateLimit}
}

// ChallengeAckAllowed 对应 tcp_challenge_ack_allowed()：
// 每秒把计数复位成一个**随机值** [half, ack_limit+half-1]。
func (n *Netns) ChallengeAckAllowed(nowSec uint32, rnd RandU32) bool {
	if n.ChallengeAckLimit == uint32(intMax) {
		return true
	}
	if nowSec != n.ChallengeTs {
		half := (n.ChallengeAckLimit + 1) >> 1
		n.ChallengeTs = nowSec
		n.ChallengeCount = rnd(half, n.ChallengeAckLimit+half-1)
	}
	if n.ChallengeCount > 0 {
		n.ChallengeCount--
		return true
	}
	return false
}

// OowRateLimited 对应 __tcp_oow_rate_limited()。
func (n *Netns) OowRateLimited(nowJ, last uint32) (bool, uint32) {
	if last != 0 {
		elapsed := int32(nowJ - last)
		if elapsed >= 0 && uint32(elapsed) < n.InvalidRateLimit {
			return true, last
		}
	}
	return false, nowJ
}

type SackBlock struct{ Start, End uint32 }

// TcpSock 是 tcp_sock 中被 tcp_validate_incoming 用到的字段子集。
type TcpSock struct {
	RcvNxt      uint32
	RcvWup      uint32
	RcvWnd      uint32
	SndUna      uint32
	SndNxt      uint32
	MaxSndWnd   uint32
	State       int
	Sacks       []SackBlock
	LastOowTime uint32
	RcvQueLen   int
}

func NewSock(rcvNxt, rcvWnd, sndUna, sndNxt, maxSndWnd uint32, state int) *TcpSock {
	return &TcpSock{RcvNxt: rcvNxt, RcvWup: rcvNxt, RcvWnd: rcvWnd,
		SndUna: sndUna, SndNxt: sndNxt, MaxSndWnd: maxSndWnd, State: state}
}

type Segment struct {
	Seq, EndSeq uint32
	RST, SYN, ACK, FIN bool
	AckSeq  uint32
}

const (
	ActPass      = "pass"
	ActReset     = "reset"
	ActDiscard   = "discard"
	ActChallenge = "challenge_ack"
)

func (sk *TcpSock) ResetCheck(s *Segment) bool {
	return s.Seq == sk.RcvNxt-1 && ((1 << uint(sk.State)) & resetCheckStates) != 0
}

// TcpSequence 对应 tcp_sequence()：nil 表示可接受。
func (sk *TcpSock) TcpSequence(s *Segment) *string {
	var fin uint32
	if s.FIN {
		fin = 1
	}
	if seqBefore(s.EndSeq, sk.RcvWup) {
		r := "OLD_SEQUENCE"
		return &r
	}
	seqLimit := sk.RcvNxt + sk.RcvWnd
	if seqAfter(s.EndSeq, seqLimit) {
		if !seqAfter(s.EndSeq-fin, seqLimit) {
			return nil
		}
		if seqAfter(s.Seq, seqLimit) {
			r := "INVALID_SEQUENCE"
			return &r
		}
		if sk.RcvQueLen > 0 {
			r := "INVALID_END_SEQUENCE"
			return &r
		}
	}
	return nil
}

// ValidateIncoming 对应 tcp_validate_incoming()，返回 (action, detail)。
func (sk *TcpSock) ValidateIncoming(s *Segment, net *Netns, nowSec, nowJ uint32, rnd RandU32) (string, string) {
	challenge := func(kind string) (string, string) {
		limited, t := net.OowRateLimited(nowJ, sk.LastOowTime)
		sk.LastOowTime = t
		if limited {
			return ActDiscard, kind + "/oow_rate_limited"
		}
		if net.ChallengeAckAllowed(nowSec, rnd) {
			return ActChallenge, kind
		}
		return ActDiscard, kind + "/netns_rate_limited"
	}

	if reason := sk.TcpSequence(s); reason != nil {
		if !s.RST {
			if s.SYN {
				return challenge("syn_challenge")
			}
			return ActDiscard, "dupack/" + *reason
		}
		if sk.ResetCheck(s) {
			return ActReset, "reset_check_out_of_window"
		}
		return ActDiscard, "silent/" + *reason
	}

	if s.RST {
		if s.Seq == sk.RcvNxt || sk.ResetCheck(s) {
			return ActReset, "seq_matches_rcv_nxt"
		}
		if len(sk.Sacks) > 0 {
			maxSack := sk.Sacks[0].End
			for _, sp := range sk.Sacks[1:] {
				if seqAfter(sp.End, maxSack) {
					maxSack = sp.End
				}
			}
			if s.Seq == maxSack {
				return ActReset, "seq_matches_max_sack_edge"
			}
		}
		return challenge("rst_challenge")
	}

	if s.SYN {
		if sk.State == tcpSynRecv && s.ACK && s.Seq+1 == s.EndSeq &&
			s.Seq+1 == sk.RcvNxt && s.AckSeq == sk.SndNxt {
			return ActPass, "syn_recv_retransmitted_ack"
		}
		return challenge("syn_challenge")
	}
	return ActPass, "ok"
}

// AckAcceptableRFC793 旧判据：(SND.UNA-(2^31-1)) <= SEG.ACK <= SND.NXT
func (sk *TcpSock) AckAcceptableRFC793(ack uint32) bool {
	lo := sk.SndUna - (1<<31 - 1)
	return !seqBefore(ack, lo) && !seqAfter(ack, sk.SndNxt)
}

// AckAcceptableRFC5961 RFC 5961 §5.2：(SND.UNA-MAX.SND.WND) <= SEG.ACK <= SND.NXT
func (sk *TcpSock) AckAcceptableRFC5961(ack uint32) bool {
	lo := sk.SndUna - sk.MaxSndWnd
	return !seqBefore(ack, lo) && !seqAfter(ack, sk.SndNxt)
}

// AckWindowSize 可被判为可接受的 ACK 取值个数（含两端点）。
func (sk *TcpSock) AckWindowSize(rfc5961 bool) uint64 {
	span := sk.MaxSndWnd
	if !rfc5961 {
		span = 1<<31 - 1
	}
	return uint64(span) + 1 + uint64(int32(sk.SndNxt-sk.SndUna))
}

// SweepClosedForm 命中下标闭式（1 起算），与 python 版同式。
func SweepClosedForm(spaceBits uint, wnd uint32, exact bool, nxt, start uint32) uint64 {
	n := uint64(1) << spaceBits
	d := uint64((nxt - start) & (uint32(n) - 1))
	if exact {
		return d + 1
	}
	k, r := d/uint64(wnd), d%uint64(wnd)
	off := uint64(0)
	if r != 0 {
		off = 1
	}
	return (k+off)%(n/uint64(wnd)) + 1
}

// MeanTriesContinuous 文档口径：无缓解 N/(2*WND)，缓解后 N/2。
func MeanTriesContinuous(spaceBits uint, wnd uint32, exact bool) float64 {
	n := float64(uint64(1) << spaceBits)
	if exact {
		return n / 2.0
	}
	return n / (2.0 * float64(wnd))
}
