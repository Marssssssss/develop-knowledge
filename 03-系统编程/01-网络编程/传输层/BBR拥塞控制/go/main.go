package main

import "fmt"

var scCount int
var scFails []string

func ok(cond bool, msg string) {
	scCount++
	if !cond {
		scFails = append(scFails, msg)
	}
}

func eq(got, want int, msg string)    { ok(got == want, msg) }
func eqi(got, want int64, msg string) { ok(got == want, msg) }

func selfcheck() (int, []string) {
	scCount, scFails = 0, nil

	eq(bbrScale, 8, "BBR_SCALE")
	eq(bbrUnit, 256, "BBR_UNIT")
	eq(bwScale, 24, "BW_SCALE")
	eq(bbrHighGain, 739, "high_gain")
	eq(bbrDrainGain, 88, "drain_gain")
	eq(bbrCwndGain, 512, "cwnd_gain")
	eq((bbrHighGain*bbrDrainGain)>>bbrScale, 254, "drain × high 不是精确 1")
	eq(bbrPacingGain[0], 320, "相位 0")
	eq(bbrPacingGain[1], 192, "相位 1")
	for i := 2; i < cycleLen; i++ {
		eq(bbrPacingGain[i], bbrUnit, "巡航相位")
	}

	bw := int64(1<<bwScale) * 12 / 100 // 0.12 pkts/us
	eqi(bbrBdp(bw, 1000, bbrUnit), 120, "bdp(1.0)")
	eqi(bbrBdp(bw, 1000, bbrCwndGain), 240, "bdp(2.0)")
	eqi(bbrBdp(bw, 1000, bbrHighGain), 347, "bdp(2.887) 向上取整")
	eqi(bbrBdp(bw, noRttSample, bbrUnit), tcpInitCwnd, "无 RTT 样本 -> 10")
	bw1 := int64(1<<bwScale) * 1 / 1000
	eqi(bbrBdp(bw1, 1500, bbrUnit), 2, "1.5 包 -> 2")
	eqi(bbrBdp(bw1, 1000, bbrUnit), 1, "0.99998 包 -> 1")

	eqi(bbrQuantizationBudget(120, 2, probeBW, 0), 128, "量化 +2")
	eqi(bbrQuantizationBudget(120, 2, probeBW, 3), 126, "非 0 相位不加 2")
	eqi(bbrInflight(bw, 1000, bbrUnit, 2, probeBW, 0), 128, "inflight(1.0)")

	r := bbrRateBytesPerSec(bw, 1200, bbrUnit)
	ok(r > 142360000 && r < 142760000, "pacing rate ≈ 142.56 MB/s")

	pg, cg := bbrUpdateGains(startup, 0, false)
	eq(pg, 739, "STARTUP pacing")
	eq(cg, 739, "STARTUP cwnd")
	pg, cg = bbrUpdateGains(drain, 0, false)
	eq(pg, 88, "DRAIN pacing")
	eq(cg, 739, "DRAIN 仍保持 cwnd")
	pg, _ = bbrUpdateGains(probeBW, 0, false)
	eq(pg, 320, "PROBE_BW idx=0")
	pg, _ = bbrUpdateGains(probeBW, 1, false)
	eq(pg, 192, "PROBE_BW idx=1")
	pg, _ = bbrUpdateGains(probeBW, 0, true)
	eq(pg, bbrUnit, "lt_use_bw 强制 1.0")
	eq(bbrAdvanceCyclePhase(7), 0, "相位回绕")
	seen := map[int]bool{}
	for i := 0; i < bbrCycleRand; i++ {
		seen[bbrResetProbeBwMode(i)] = true
	}
	ok(!seen[1], "起始相位永不为 1")

	b := &Bbr{}
	b.FullBw = 100
	ok(!b.CheckFullBw(130, true, false), "130 >= 125 -> 复位计数")
	eq(b.FullBw, 130, "full_bw 抬高")
	ok(!b.CheckFullBw(120, true, false), "计数 1")
	ok(!b.CheckFullBw(120, true, false), "计数 2")
	ok(b.CheckFullBw(120, true, false), "计数 3 -> 达成")

	f := &Bbr{}
	ok(!f.UpdateMinRtt(1, 50000, false), "首个样本")
	eqi(f.MinRttUs, 50000, "min_rtt")
	ok(f.UpdateMinRtt(20, 90000, false), "15s 后过期 -> PROBE_RTT")
	eqi(f.MinRttUs, 90000, "过期后重置")
	eq(f.Mode, probeRTT, "mode")

	p := &Bbr{Mode: probeRTT}
	eq(p.ProbeRttTick(1000, 50, false), "waiting_for_low_inflight", "在飞过多")
	eq(p.ProbeRttTick(1001, 4, false), "armed", "降到 4 包起表")
	eqi(p.ProbeRttDoneStamp, 1201, "到点 = 1001+200")
	eq(p.ProbeRttTick(1100, 4, true), "holding", "先置 round_done")
	eq(p.ProbeRttTick(1202, 4, true), "done", "超时且走完 round")
	eq(p.Mode, startup, "未达 full_bw -> 回 STARTUP")

	return scCount, scFails
}

func main() {
	fmt.Println("== Go 侧自检 ==")
	n, fails := selfcheck()
	fmt.Printf("  assertions=%d  fails=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL:", f)
	}

	fmt.Println("\n== 8 相增益循环 ==")
	for i, g := range bbrPacingGain {
		fmt.Printf("  idx=%d gain=%-4d %.4f\n", i, g, float64(g)/float64(bbrUnit))
	}
	fmt.Println("\n== bdp / inflight (bw=0.12 pkt/us, min_rtt=1ms) ==")
	bw := int64(1<<bwScale) * 12 / 100
	for _, g := range []int{bbrUnit, bbrCwndGain, bbrHighGain} {
		fmt.Printf("  gain=%-4d bdp=%-5d inflight=%d\n", g,
			bbrBdp(bw, 1000, g), bbrInflight(bw, 1000, g, 2, probeBW, 0))
	}
}
