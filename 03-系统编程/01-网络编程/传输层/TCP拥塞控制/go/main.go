// TCP 拥塞控制模拟：Reno(RFC 5681) vs CUBIC(RFC 9438)
//
// 纯窗口动力学模拟，不涉及 socket：按 RTT 轮次推进，每轮发送 cwnd 个 SMSS
// 段；当 cwnd 超过链路饱和窗口 satWindow 时丢弃 1 段，发送方通过 3 个重复
// ACK 触发快重传/快恢复。两种算法都保留慢启动，CUBIC 只替换拥塞避免阶段的
// 窗口增长函数。
//
// 运行：go run main.go
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

// -------------------------------------------------------------- 自检
func selfCheck() {
	// 1. K 自洽：W_cubic(K) 必须正好回到 W_max
	allOK := true
	for _, w := range []float64{50, 200, 1000} {
		k := cubicK(w, w*betaCubic)
		if math.Abs(wCubic(k, k, w)-w) > 1e-9 {
			allOK = false
		}
	}
	check(allOK, "W_cubic(K) 应正好等于 W_max")

	// 2. 拥塞事件瞬间 cwnd == W_max * beta_cubic
	c := newCubic(true)
	runFirstLoss(c, 200)
	check(math.Abs(c.cwnd-c.wMax*betaCubic) < 1e-9, "beta_cubic 不是 0.7")
	check(math.Abs(c.cwndEpoch-c.cwnd) < 1e-9, "cwndEpoch 应为减后的 cwnd")

	// 3. Reno 拥塞避免每 RTT 恰好 +1 段
	r := newReno()
	r.cwnd, r.ssthresh = 100, 1
	r.round(false, false)
	check(math.Abs(r.cwnd-101) < 1e-9, "Reno 拥塞避免增长不等于 +1/RTT")

	// 4. Reno 快重传减半
	r = newReno()
	r.cwnd = 100
	r.round(true, false)
	check(r.ssthresh == 50 && r.cwnd == 50, "Reno 快恢复减半错误")

	// 5. 慢启动翻倍
	r = newReno()
	r.cwnd, r.ssthresh = 8, 1e9
	r.round(false, false)
	check(r.cwnd == 16, "慢启动未翻倍")

	// 6. CUBIC 凹段初期增速快于 Reno 的 +1 段/RTT
	c2 := newCubic(true)
	runFirstLoss(c2, 400)
	start := c2.cwnd
	c2.round(false, false)
	check(c2.cwnd-start > 1, "CUBIC 凹段初期增速应 > 1 段/RTT")

	// 7. CUBIC 恰好花 K 个 RTT 回到平台 W_max
	c3 := newCubic(true)
	runFirstLoss(c3, 400)
	reached := -1
	for j := 1; j <= 200; j++ {
		c3.round(false, false)
		if c3.cwnd >= c3.wMax-1e-6 {
			reached = j
			break
		}
	}
	check(reached > 0 && math.Abs(float64(reached)-c3.k) <= 1,
		"回到 W_max 的 RTT 数应近似等于 K")

	// 8. fast convergence：cwnd < W_max 时再丢包会额外收缩 W_max
	c5 := newCubic(true)
	runFirstLoss(c5, 400)
	w1 := c5.wMax
	c5.round(false, false)
	wAfter := c5.cwnd
	c5.round(true, false)
	check(c5.wMax < w1, "fast convergence 未收缩 W_max")
	check(math.Abs(c5.wMax-wAfter*(1+betaCubic)/2) < 1e-9, "fast convergence 公式错误")
	c6 := newCubic(false)
	runFirstLoss(c6, 400)
	c6.round(false, false)
	wAfter2 := c6.cwnd
	c6.round(true, false)
	check(math.Abs(c6.wMax-wAfter2) < 1e-9, "关闭 FC 后 W_max 应取当前 cwnd")

	// 9. RTO：两者 cwnd 落到 1 段；CUBIC 的 ssthresh 用 β_cubic
	r = newReno()
	r.cwnd = 80
	r.round(false, true)
	check(r.cwnd == 1 && r.ssthresh == 40, "Reno RTO 处理错误")
	c7 := newCubic(true)
	runFirstLoss(c7, 400)
	c7.cwnd = 80
	c7.round(false, true)
	check(c7.cwnd == 1, "CUBIC RTO 后 cwnd 应为 1 段")
	check(math.Abs(c7.ssthresh-56) < 1e-9, "CUBIC RTO 的 ssthresh 应用 β_cubic")
	check(c7.k == 0, "RTO 后 K 应置 0")

	// 10. 高 BDP：CUBIC 平均窗口 > Reno
	{
		rr, cc := newReno(), newCubic(true)
		var rs, cs float64
		for i := 0; i < 400; i++ {
			rr.round(rr.cwnd > 400, false)
			cc.round(cc.cwnd > 400, false)
			rs += rr.cwnd
			cs += cc.cwnd
		}
		check(cs > rs, "高 BDP 下 CUBIC 平均窗口应超过 Reno")
	}

	// 11. 低 BDP：CUBIC 不应明显劣于 Reno（Reno-friendly 区域）
	{
		rr, cc := newReno(), newCubic(true)
		var rs, cs float64
		for i := 0; i < 400; i++ {
			rr.round(rr.cwnd > 40, false)
			cc.round(cc.cwnd > 40, false)
			rs += rr.cwnd
			cs += cc.cwnd
		}
		check(cs >= rs*0.98, "小 BDP 下 CUBIC 不应明显差于 Reno")
	}

	fmt.Printf("[self-check] %d 项断言, %d 项失败\n", checks, failures)
}

func main() {
	selfCheck()

	// 1) 轨迹对照
	rr, cc := newReno(), newCubic(true)
	rt := make([]float64, 400)
	ct := make([]float64, 400)
	var rs, cs float64
	for i := 0; i < 400; i++ {
		rr.round(rr.cwnd > 400, false)
		cc.round(cc.cwnd > 400, false)
		rt[i], ct[i] = rr.cwnd, cc.cwnd
		rs += rt[i]
		cs += ct[i]
	}
	fmt.Println("\n=== 1) 高 BDP 长肥管道（饱和窗口 400 段）窗口轨迹 ===")
	fmt.Printf("%4s %9s %9s\n", "RTT", "Reno", "CUBIC")
	for i := 0; i < 400; i += 20 {
		fmt.Printf("%4d %9.1f %9.1f\n", i, rt[i], ct[i])
	}
	fmt.Printf("\n平均窗口 Reno %.1f 段 (%.2f Mbit/s) | CUBIC %.1f 段 (%.2f Mbit/s) | +%.1f%%\n",
		rs/400, avgMbps(rt), cs/400, avgMbps(ct), (cs/rs-1)*100)

	// 2) 恢复代价
	rr = newReno()
	wMax := runFirstLoss(rr, 400)
	renoNeed := recoveryRTTs(func() cc { return newReno() }, wMax)
	cubicNeed := recoveryRTTs(func() cc { return newCubic(true) }, wMax)
	fmt.Printf("\n=== 2) 丢包后爬回原窗口需要多少 RTT（W_max = %.0f 段）===\n", wMax)
	fmt.Printf("  Reno : %3d 个 RTT（掉到 W_max/2，拥塞避免每 RTT 只 +1 段）\n", renoNeed)
	fmt.Printf("  CUBIC: %3d 个 RTT（只掉到 0.7*W_max，K = cbrt(0.3*W_max/C)）\n", cubicNeed)
	fmt.Printf("  加速比约 %.0f 倍\n", float64(renoNeed)/math.Max(float64(cubicNeed), 1))

	// 3) RTO 对照
	fmt.Println("\n=== 3) 丢包检测方式的影响（饱和窗口 200 段）===")
	rr, cc = newReno(), newCubic(true)
	for rr.cwnd <= 200 {
		rr.round(false, false)
	}
	for cc.cwnd <= 200 {
		cc.round(false, false)
	}
	rb, cb := rr.cwnd, cc.cwnd
	rr.round(false, true)
	cc.round(false, true)
	fmt.Printf("  Reno  cwnd %6.1f -> RTO -> cwnd %.0f, ssthresh %.1f\n", rb, rr.cwnd, rr.ssthresh)
	fmt.Printf("  CUBIC cwnd %6.1f -> RTO -> cwnd %.0f, ssthresh %.1f\n", cb, cc.cwnd, cc.ssthresh)

	if failures > 0 {
		fmt.Printf("\n有 %d 项自检失败\n", failures)
	}
}
