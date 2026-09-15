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
