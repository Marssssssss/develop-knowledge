// Package main 是 dack_model.py 的 Go 转写:HZ=1000,时间一律毫秒。
//
// 转写时显式落地的语言差异:
//   * C/Go 的 `+` 优先级高于 `>>`,因此源码里 (ato>>1) + m 的括号不能省;
//     Python 侧同样是这个优先级(已在 python/dack_model.py 里踩过一次)。
//   * 内核用 u32 回绕做「有符号比较」,Go 里没有 u32 语义,这里直接用 int
//     比较;retransmits_timed_out 的负数情形用显式 (now-start-timeout) >= 0。
//   * msecs_to_jiffies / jiffies_to_msecs 在 HZ=1000 下是恒等,故全程用 ms。
package main

const (
	hz = 1000

	tcpDelackMax = hz / 5  // 200
	tcpDelackMin = hz / 25 // 40
	tcpAtoMin    = hz / 25 // 40
	tcpRtoMin    = hz / 5  // 200
	tcpRtoMax    = 120 * hz
	tcpTimeoutMin = 2
	tcpMaxQuickacks = 16

	ackPushed = 1
)

// DelackEngine 对应 icsk_ack 里与 quickack/ato 相关的字段。
type DelackEngine struct {
	RcvMss, RcvWnd int
	Rto, SrttUs    int
	Ato, Quick     int
	LrcvTime       int
	Pending        int
	Retry          int
	DelackMax      int
	DstQuickAck    bool
	Pingpong       bool
}

func newDelackEngine(rcvMss, rcvWnd int) *DelackEngine {
	return &DelackEngine{
		RcvMss: rcvMss, RcvWnd: rcvWnd,
		Rto: tcpRtoMin, DelackMax: tcpDelackMax,
	}
}

// IncrQuickack 转写 tcp_incr_quickack():配额 = rcv_wnd/(2*rcv_mss),商为 0 兜底 2。
func (e *DelackEngine) IncrQuickack(maxQuickacks int) int {
	q := e.RcvWnd / (2 * e.RcvMss)
	if q == 0 {
		q = 2
	}
	if q > maxQuickacks {
		q = maxQuickacks
	}
	if q > e.Quick {
		e.Quick = q
	}
	return e.Quick
}

// EnterQuickackMode 三个动作:incr -> 退 pingpong -> ato 压到 ATO_MIN。
func (e *DelackEngine) EnterQuickackMode(maxQuickacks int) {
	e.IncrQuickack(maxQuickacks)
	e.Pingpong = false
	e.Ato = tcpAtoMin
}

// InQuickackMode:dstQuickAck 是短路项。
func (e *DelackEngine) InQuickackMode() bool {
	return e.DstQuickAck || (e.Quick != 0 && !e.Pingpong)
}

// OnDataRecv 转写 tcp_event_data_recv() 的 ato/quick 那一段。
func (e *DelackEngine) OnDataRecv(now int) int {
	if e.Ato == 0 {
		e.IncrQuickack(tcpMaxQuickacks)
		e.Ato = tcpAtoMin
	} else {
		m := now - e.LrcvTime
		switch {
		case m <= tcpAtoMin/2:
			e.Ato = (e.Ato >> 1) + tcpAtoMin/2
		case m < e.Ato:
			e.Ato = min3((e.Ato>>1)+m, e.Rto, tcpDelackMax)
		case m > e.Rto:
			e.IncrQuickack(tcpMaxQuickacks)
		}
	}
	e.LrcvTime = now
	return e.Ato
}

// DelackTimeout 转写 tcp_send_delayed_ack()。
// applyFinalCap=false 时跳过最后那道 tcp_delack_max(),用于观察 pingpong / srtt
// 两个上限各自的贡献。
func (e *DelackEngine) DelackTimeout(applyFinalCap bool) int {
	ato := e.Ato
	if ato > tcpDelackMin {
		maxAto := hz / 2
		if e.Pingpong || (e.Pending&ackPushed) != 0 {
			maxAto = tcpDelackMax
		}
		if e.SrttUs != 0 {
			rtt := usecToMsCeil(e.SrttUs >> 3)
			if rtt < tcpDelackMin {
				rtt = tcpDelackMin
			}
			if rtt < maxAto {
				maxAto = rtt
			}
		}
		if maxAto < ato {
			ato = maxAto
		}
	}
	if applyFinalCap && e.DelackMax < ato {
		ato = e.DelackMax
	}
	return ato
}

// DelackRetryDelay 转写 alloc_skb 失败分支的指数退避,返回 (delay, 是否递增)。
func (e *DelackEngine) DelackRetryDelay() (int, bool) {
	delay := tcpDelackMax << uint(e.Retry)
	grew := delay < tcpRtoMax
	if grew {
		e.Retry++
	}
	e.Ato = tcpAtoMin
	return delay, grew
}

func usecToMsCeil(us int) int {
	if us <= 0 {
		return 0
	}
	return (us + 999) / 1000
}

func min3(a, b, c int) int {
	m := a
	if b < m {
		m = b
	}
	if c < m {
		m = c
	}
	return m
}

func ilog2(n int) int {
	r := 0
	for (1 << uint(r+1)) <= n {
		r++
	}
	return r
}

// ClampRtoToUserTimeout 转写 tcp_clamp_rto_to_user_timeout()。
// remaining <= 0 时返回 1(1 jiffy,「立刻到点」)而不是 0。
func ClampRtoToUserTimeout(userTimeout, rto, elapsed int) int {
	if userTimeout == 0 {
		return rto
	}
	remaining := userTimeout - elapsed
	if remaining <= 0 {
		return 1
	}
	if remaining < rto {
		return remaining
	}
	return rto
}

// ClampProbe0ToUserTimeout 转写 tcp_clamp_probe0_to_user_timeout():
// elapsed 为负要兜成 0,剩余时间有 TCP_TIMEOUT_MIN 下限。
func ClampProbe0ToUserTimeout(userTimeout, when, probesTstamp, now int) int {
	if userTimeout == 0 || probesTstamp == 0 {
		return when
	}
	elapsed := now - probesTstamp
	if elapsed < 0 {
		elapsed = 0
	}
	remaining := userTimeout - elapsed
	if remaining < tcpTimeoutMin {
		remaining = tcpTimeoutMin
	}
	if remaining < when {
		return remaining
	}
	return when
}

// ModelTimeout 转写 tcp_model_timeout(),返回值已是毫秒。
func ModelTimeout(boundary, rtoBase, rtoMax int) int {
	thresh := ilog2(rtoMax / rtoBase)
	if boundary <= thresh {
		return ((2 << uint(boundary)) - 1) * rtoBase
	}
	return ((2<<uint(thresh))-1)*rtoBase + (boundary-thresh)*rtoMax
}

// RetransmitsTimedOut 转写 retransmits_timed_out():先判 retransmits 再比时间。
func RetransmitsTimedOut(boundary, timeout, retransStamp, now, retransmits,
	rtoBase, rtoMax int) bool {
	if retransmits == 0 {
		return false
	}
	if timeout == 0 {
		timeout = ModelTimeout(boundary, rtoBase, rtoMax)
	}
	return (now - retransStamp - timeout) >= 0
}

// ProbeTimerDecision 转写 tcp_probe_timer() 的分支选择。
func ProbeTimerDecision(userTimeout, probesTstamp, now, probesOut, maxProbes,
	packetsOut int, hasHeadSkb bool) string {
	if packetsOut != 0 || !hasHeadSkb {
		return "reset_probes"
	}
	if probesTstamp == 0 {
		return "probe"
	}
	if userTimeout != 0 && (now-probesTstamp) >= userTimeout {
		return "abort"
	}
	if probesOut >= maxProbes {
		return "abort"
	}
	return "probe"
}
