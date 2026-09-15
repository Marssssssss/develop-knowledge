// cc_model.go — 常量、自检脚手架与 Reno/CUBIC 状态机（从 main.go 拆出）。
//
// 与 main.go 同属 package main：Go 里同包多文件共享命名空间，
// 搬运不改变任何语义，也不需要 import 对方。
package main

import (
	"fmt"
	"math"
)

const (
	rtt       = 1.0  // 归一化往返时延（秒），1 个 RTT = 1 个模拟轮
	smss      = 1460 // 最大段长度（字节）
	cCubic    = 0.4  // RFC 9438 §5：C SHOULD be set to 0.4
	betaCubic = 0.7  // RFC 9438 §3.4：乘法递减因子 SHOULD be 0.7
	// RFC 9438 §4.3：为达到与 AIMD(1,0.5) 相同平均窗口，α = 3(1-β)/(1+β)
	alphaCubic = 3.0 * (1.0 - betaCubic) / (1.0 + betaCubic)
	iw         = 10.0 // 初始窗口（段），放大以便观察图形
)

var failures, checks int

func check(cond bool, msg string) {
	checks++
	if !cond {
		fmt.Printf("  [FAIL] %s\n", msg)
		failures++
	}
}

// cubicK —— RFC 9438 §4.2：K = cbrt((W_max - cwnd_epoch)/C)
// K 是把窗口从 cwnd_epoch 涨回 W_max 所需的时间（秒）。
func cubicK(wMax, cwndEpoch float64) float64 {
	delta := math.Max(wMax-cwndEpoch, 0)
	return math.Cbrt(delta / cCubic)
}

// wCubic —— RFC 9438 §4.2 Figure 1：W_cubic(t) = C*(t-K)^3 + W_max
// t < K 为凹段（增速递减、逼近平台），t > K 转凸（增速递增、探测新带宽）。
func wCubic(t, k, wMax float64) float64 {
	d := t - k
	return cCubic*d*d*d + wMax
}

// ---------------------------------------------------------------- Reno
type Reno struct {
	cwnd, ssthresh, t float64
	losses, rtoEvents int
}

func newReno() *Reno { return &Reno{cwnd: iw, ssthresh: math.Inf(1)} }

func (r *Reno) cwndOf() float64 { return r.cwnd }

// round 推进 1 个 RTT。loss = 快重传（3 dup ACK），timeout = RTO。
func (r *Reno) round(loss, timeout bool) {
	r.t += rtt
	switch {
	case timeout:
		// RFC 5681 §3.1 公式(4)：ssthresh = max(FlightSize/2, 2*SMSS)
		r.ssthresh = math.Max(r.cwnd/2, 2)
		r.cwnd = 1 // LW = 1 个满尺寸段，回到慢启动
		r.rtoEvents++
		r.losses++
	case loss:
		r.losses++
		r.ssthresh = math.Max(r.cwnd/2, 2)
		// §3.2 规则 3/6：先膨胀 3 段再放气，净效果 = 窗口乘性减半
		r.cwnd = r.ssthresh
	case r.cwnd < r.ssthresh:
		r.cwnd *= 2 // 慢启动：一个 RTT 内窗口翻倍
	default:
		r.cwnd++ // 拥塞避免：每 RTT +1 段（加性增）
	}
}

// --------------------------------------------------------------- CUBIC
type Cubic struct {
	cwnd, ssthresh, t          float64
	wMax, cwndPrior, cwndEpoch float64
	tEpoch, wEst, alpha, k     float64
	losses, rtoEvents          int
	enteredCA, fastConvergence bool
}

func newCubic(fastConvergence bool) *Cubic {
	return &Cubic{cwnd: iw, ssthresh: math.Inf(1), fastConvergence: fastConvergence}
}

func (c *Cubic) onLoss(timeout bool) {
	c.losses++
	if timeout {
		// §4.8：cwnd 按 Reno 降到 1 段，但 ssthresh 用 β_cubic；K 置 0，
		// W_max = 本阶段起始 cwnd。
		c.cwndPrior = c.cwnd
		c.ssthresh = math.Max(c.cwnd*betaCubic, 2)
		c.cwnd = 1
		c.rtoEvents++
		c.wMax = c.cwnd
		c.cwndEpoch = c.cwnd
		c.k = 0
	} else {
		// §4.7 fast convergence：拥塞时若 cwnd < W_max，说明饱和点在下移，
		// 主动多让出带宽 → W_max 再乘 (1+β)/2。
		if c.fastConvergence && c.wMax > 0 && c.cwnd < c.wMax {
			c.wMax = c.cwnd * (1 + betaCubic) / 2
		} else {
			c.wMax = c.cwnd
		}
		if c.enteredCA {
			c.cwndPrior = c.cwnd
		}
		c.cwnd *= betaCubic // §4.6 乘性减（不是 Reno 的 0.5）
		c.cwndEpoch = c.cwnd
		c.ssthresh = math.Max(c.cwnd, 2)
		c.k = cubicK(c.wMax, c.cwndEpoch)
	}
	c.enteredCA = true
	c.tEpoch = c.t
	c.wEst = c.cwnd // §4.3：W_est 初值 = cwnd_epoch
	c.alpha = alphaCubic
}

func (c *Cubic) round(loss, timeout bool) {
	c.t += rtt
	if loss || timeout {
		c.onLoss(timeout)
		return
	}
	if c.cwnd < c.ssthresh { // 慢启动不变
		c.cwnd *= 2
		return
	}
	elapsed := c.t - c.tEpoch
	target := math.Min(wCubic(elapsed, c.k, c.wMax), 1.5*c.cwnd) // 上界：不超慢启动
	if target > c.cwnd {                                        // 下界：增速非递减
		c.cwnd = target
	}
	// §4.3 Reno-friendly：W_est 线性增长，追上 cwndPrior 后 α 降为 1
	c.wEst += c.alpha
	if c.wEst >= c.cwndPrior {
		c.alpha = 1
	}
	if c.wEst > c.cwnd {
		c.cwnd = c.wEst
	}
}

// -------------------------------------------------------------- 驱动
type cc interface {
	round(loss, timeout bool)
	cwndOf() float64
}

// runFirstLoss 跑到第一次丢包，返回丢包前的 cwnd。
func runFirstLoss(c cc, satWindow float64) float64 {
	for i := 0; i < 100000 && c.cwndOf() <= satWindow; i++ {
		c.round(false, false)
	}
	wBefore := c.cwndOf()
	c.round(true, false)
	return wBefore
}

// recoveryRTTs 窗口为 wMax 时丢 1 段，测量爬回 wMax 所需的 RTT 数。
func recoveryRTTs(newCC func() cc, wMax float64) int {
	c := newCC()
	// 直接把窗口顶到 wMax 再制造一次拥塞事件
	switch v := c.(type) {
	case *Reno:
		v.cwnd = wMax
	case *Cubic:
		v.cwnd = wMax
	}
	c.round(true, false)
	n := 0
	for n < 100000 && c.cwndOf() < wMax {
		c.round(false, false)
		n++
	}
	return n
}

func avgMbps(traj []float64) float64 {
	s := 0.0
	for _, v := range traj {
		s += v
	}
	return s / float64(len(traj)) * smss * 8 / rtt / 1e6
}

