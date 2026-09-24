// Package main 是 bbr_model.py 的 Go 转写。
//
// 显式落地的语言差异:
//   * C 里 `(w * gain) >> BBR_SCALE` 是 u64 运算;Go 里用 uint64 并在最后
//     转回 int,避免中间溢出(源码注释说该顺序可撑到 2.9 Tbit/s)。
//   * `(cwnd + 1) & ~1U` 的取偶:Go 里 `^uint32(1)` 需要显式类型。
//   * Python 的 `//` 是向下取整;Go 的整数除法对负数会向零取整,本文件所有
//     被除数都保证非负,故语义一致。
package main

const (
	bbrScale = 8
	bbrUnit  = 1 << bbrScale // 256
	bwScale  = 24
	bwUnit   = 1 << bwScale  // 16777216
	usecPerSec = 1000000

	tcpInitCwnd = 10
	noRttSample = 0xFFFFFFFF

	cycleLen            = 8
	bbrBwRtts           = cycleLen + 2
	bbrMinRttWinSec     = 10
	bbrProbeRttModeMs   = 200
	bbrCwndMinTarget    = 4
	bbrCycleRand        = 7
	bbrPacingMarginPct  = 1
	bbrMinTsoRate       = 1200000

	bbrHighGain  = bbrUnit*2885/1000 + 1 // 739
	bbrDrainGain = bbrUnit * 1000 / 2885 // 88
	bbrCwndGain  = bbrUnit * 2           // 512

	bbrFullBwThresh = bbrUnit * 5 / 4 // 320
	bbrFullBwCnt    = 3
)

const (
	startup = iota
	drain
	probeBW
	probeRTT
)

var bbrPacingGain = [cycleLen]int{
	bbrUnit * 5 / 4, bbrUnit * 3 / 4,
	bbrUnit, bbrUnit, bbrUnit, bbrUnit, bbrUnit, bbrUnit,
}

func modeName(m int) string {
	switch m {
	case startup:
		return "STARTUP"
	case drain:
		return "DRAIN"
	case probeBW:
		return "PROBE_BW"
	case probeRTT:
		return "PROBE_RTT"
	}
	return "?"
}

// bbr_bdp():w = bw * min_rtt_us;bdp = ceil((w*gain) >> BBR_SCALE / BW_UNIT)。
func bbrBdp(bw int64, minRttUs int64, gain int) int64 {
	if minRttUs == noRttSample {
		return tcpInitCwnd
	}
	w := bw * minRttUs
	return ((w*int64(gain) >> bbrScale) + bwUnit - 1) / bwUnit
}

// bbr_quantization_budget():三步,顺序即源码顺序。
func bbrQuantizationBudget(cwnd int64, tsoSegs int, mode, cycleIdx int) int64 {
	cwnd += int64(3 * tsoSegs)
	cwnd = (cwnd + 1) & ^int64(1)
	if mode == probeBW && cycleIdx == 0 {
		cwnd += 2
	}
	return cwnd
}

// bbr_inflight() = quantize(bdp)。
func bbrInflight(bw, minRttUs int64, gain, tsoSegs, mode, cycleIdx int) int64 {
	return bbrQuantizationBudget(bbrBdp(bw, minRttUs, gain), tsoSegs, mode, cycleIdx)
}

// bbr_rate_bytes_per_sec():乘法顺序不能换,源码说这是为了不溢出 u64。
func bbrRateBytesPerSec(bw int64, mss, gain int) int64 {
	rate := bw
	rate *= int64(mss)
	rate *= int64(gain)
	rate >>= bbrScale
	rate *= int64(usecPerSec/100*(100-bbrPacingMarginPct)) // 990000
	return rate >> bwScale
}

// bbr_update_gains():返回 (pacing_gain, cwnd_gain)。
func bbrUpdateGains(mode, cycleIdx int, ltUseBw bool) (int, int) {
	switch mode {
	case startup:
		return bbrHighGain, bbrHighGain
	case drain:
		return bbrDrainGain, bbrHighGain
	case probeBW:
		if ltUseBw {
			return bbrUnit, bbrCwndGain
		}
		return bbrPacingGain[cycleIdx], bbrCwndGain
	case probeRTT:
		return bbrUnit, bbrUnit
	}
	panic("BBR bad mode")
}

// bbr_advance_cycle_phase():(idx+1) & (CYCLE_LEN-1)。
func bbrAdvanceCyclePhase(cycleIdx int) int {
	return (cycleIdx + 1) & (cycleLen - 1)
}

// bbr_reset_probe_bw_mode():起始相位 = (CYCLE_LEN-1-rand)+1,取值永不为 1。
func bbrResetProbeBwMode(randBelow int) int {
	return bbrAdvanceCyclePhase(cycleLen - 1 - randBelow)
}

// Bbr 一个 BBR 流的精简状态。
type Bbr struct {
	Mode                                     int
	CycleIdx                                 int
	FullBw, FullBwCnt                        int
	FullBwReached                            bool
	MinRttUs, MinRttStamp                    int64
	IdleRestart                              bool
	ProbeRttDoneStamp                        int64
	ProbeRttRoundDone                        bool
}

// CheckFullBw bbr_check_full_bw_reached():连续 3 轮增长 < 25% 判为 pipe full。
func (b *Bbr) CheckFullBw(maxBw int, roundStart, isAppLimited bool) bool {
	if b.FullBwReached || !roundStart || isAppLimited {
		return b.FullBwReached
	}
	bwThresh := (b.FullBw * bbrFullBwThresh) >> bbrScale
	if maxBw >= bwThresh {
		b.FullBw = maxBw
		b.FullBwCnt = 0
		return false
	}
	b.FullBwCnt++
	b.FullBwReached = b.FullBwCnt >= bbrFullBwCnt
	return b.FullBwReached
}

// UpdateMinRtt bbr_update_min_rtt():min_rtt 滤波 + PROBE_RTT 进入判据。
// 返回 true 表示本次进入了 PROBE_RTT。
func (b *Bbr) UpdateMinRtt(now, rttUs int64, isAckDelayed bool) bool {
	filterExpired := now > b.MinRttStamp+bbrMinRttWinSec
	if rttUs >= 0 && (rttUs < b.MinRttUs || (filterExpired && !isAckDelayed)) {
		b.MinRttUs = rttUs
		b.MinRttStamp = now
	}
	if bbrProbeRttModeMs > 0 && filterExpired && !b.IdleRestart && b.Mode != probeRTT {
		b.Mode = probeRTT
		b.ProbeRttDoneStamp = 0
		b.ProbeRttRoundDone = false
		return true
	}
	return false
}

// ProbeRttTick BBR_PROBE_RTT 期间的维持逻辑。
func (b *Bbr) ProbeRttTick(now int64, packetsInFlight int64, roundStart bool) string {
	if b.Mode != probeRTT {
		return ""
	}
	if b.ProbeRttDoneStamp == 0 {
		if packetsInFlight <= bbrCwndMinTarget {
			b.ProbeRttDoneStamp = now + bbrProbeRttModeMs
			b.ProbeRttRoundDone = false
			return "armed"
		}
		return "waiting_for_low_inflight"
	}
	if roundStart {
		b.ProbeRttRoundDone = true
	}
	if b.ProbeRttRoundDone && now > b.ProbeRttDoneStamp {
		if b.FullBwReached {
			b.Mode = probeBW
		} else {
			b.Mode = startup
		}
		b.ProbeRttDoneStamp = 0
		return "done"
	}
	return "holding"
}
