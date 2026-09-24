"""自检:BBR 定点算术与状态机。纯计算断言。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bbr_model import (                                   # noqa: E402
    Bbr, BBR_UNIT, BBR_SCALE, BW_SCALE, BW_UNIT, CYCLE_LEN,
    bbr_high_gain, bbr_drain_gain, bbr_cwnd_gain, bbr_pacing_gain,
    bbr_full_bw_thresh, bbr_full_bw_cnt, bbr_cycle_rand, bbr_cwnd_min_target,
    bbr_bw_rtts, bbr_min_rtt_win_sec, bbr_probe_rtt_mode_ms,
    bbr_pacing_margin_percent, TCP_INIT_CWND, NO_RTT_SAMPLE,
    bbr_bdp, bbr_quantization_budget, bbr_inflight, bbr_rate_bytes_per_sec,
    bbr_update_gains, bbr_advance_cycle_phase, bbr_reset_probe_bw_mode,
    STARTUP, DRAIN, PROBE_BW, PROBE_RTT,
)

N = 0


def ok(cond, msg):
    global N
    N += 1
    if not cond:
        raise AssertionError("FAIL #%d: %s" % (N, msg))


def eq(got, want, msg):
    ok(got == want, "%s -> got %r want %r" % (msg, got, want))


def close(got, want, tol, msg):
    ok(abs(got - want) <= tol, "%s -> got %r want %r±%r" % (msg, got, want, tol))


# ---------------------------------------------------------------- 常量
eq(BBR_SCALE, 8, "BBR_SCALE")
eq(BBR_UNIT, 256, "BBR_UNIT")
eq(BW_SCALE, 24, "BW_SCALE")
eq(BW_UNIT, 16777216, "BW_UNIT")
eq(bbr_high_gain, 739, "high_gain = BBR_UNIT*2885/1000 + 1")
close(bbr_high_gain / BBR_UNIT, 2 / 0.693147, 0.002, "high_gain ≈ 2/ln(2)")
eq(bbr_drain_gain, 88, "drain_gain = BBR_UNIT*1000/2885")
# 注意:drain_gain 不是 high_gain 的**精确**倒数 —— 两处都做了整数除法
eq(bbr_drain_gain * bbr_high_gain, 65032, "drain × high 的定点乘积")
eq((bbr_drain_gain * bbr_high_gain) >> BBR_SCALE, 254,
   "乘积 ≈ 254 而不是 256:drain 略慢于精确倒数(整数截断)")
eq(bbr_cwnd_gain, 512, "cwnd_gain = 2*BBR_UNIT")
eq(bbr_pacing_gain, [320, 192, 256, 256, 256, 256, 256, 256], "pacing gain cycle")
eq(len(bbr_pacing_gain), CYCLE_LEN, "CYCLE_LEN")
eq(bbr_bw_rtts, 10, "bw 滤波窗口 = CYCLE_LEN+2")
eq(bbr_min_rtt_win_sec, 10, "min_rtt 窗口 10s")
eq(bbr_probe_rtt_mode_ms, 200, "PROBE_RTT 最短 200ms")
eq(bbr_cwnd_min_target, 4, "PROBE_RTT 目标 4 包")
eq(bbr_cycle_rand, 7, "起始相位随机范围")
eq(bbr_pacing_margin_percent, 1, "pacing 比估计带宽低 1%")
eq(bbr_full_bw_thresh, 320, "full_bw 阈值 1.25x")
eq(bbr_full_bw_cnt, 3, "full_bw 需要连数 3 轮")
eq(TCP_INIT_CWND, 10, "TCP_INIT_CWND")

# ------------------------------------------------------- bbr_bdp 定点算术
BW = (1 << BW_SCALE) * 12 // 100        # 0.12 pkts/us 的定点表示
eq(bbr_bdp(BW, 1000, BBR_UNIT), 120, "0.12 pkt/us × 1000us × 1.0 = 120")
eq(bbr_bdp(BW, 1000, bbr_cwnd_gain), 240, "cwnd_gain=2 -> 240")
eq(bbr_bdp(BW, 1000, bbr_high_gain), 347, "high_gain=2.887 -> ceil(346.4)=347")
eq(bbr_bdp(BW, NO_RTT_SAMPLE, BBR_UNIT), TCP_INIT_CWND, "没有 RTT 样本 -> 10")
# 向上取整:1.5 包 -> 2,0.99998 包 -> 1
BW1 = (1 << BW_SCALE) * 1 // 1000      # 0.001 pkts/us
eq(bbr_bdp(BW1, 1500, BBR_UNIT), 2, "1.5 包向上取整成 2")
eq(bbr_bdp(BW1, 1000, BBR_UNIT), 1, "0.99998 包向上取整成 1(成对用例)")

# --------------------------------------------- bbr_quantization_budget 三步
eq(bbr_quantization_budget(120, tso_segs=2, mode=PROBE_BW, cycle_idx=0), 128,
   "120+6=126 -> 取偶 126 -> cycle_idx=0 再 +2 = 128")
eq(bbr_quantization_budget(120, tso_segs=2, mode=PROBE_BW, cycle_idx=3), 126,
   "同一 cwnd 下 cycle_idx != 0 时不 +2(成对用例)")
eq(bbr_quantization_budget(120, tso_segs=2, mode=STARTUP, cycle_idx=0), 126,
   "只有 PROBE_BW 才看 cycle_idx(成对用例)")
eq(bbr_quantization_budget(121, tso_segs=2, mode=PROBE_BW, cycle_idx=3), 128,
   "121+6=127 -> 取偶抬到 128")
eq(bbr_quantization_budget(120, tso_segs=1, mode=PROBE_BW, cycle_idx=3), 124,
   "tso_segs=1 -> 123 -> 取偶 124")
eq(bbr_inflight(BW, 1000, BBR_UNIT), 128, "bbr_inflight = quantize(bdp)=120+6→126→+2")
eq(bbr_inflight(BW, 1000, bbr_cwnd_gain), 248, "cwnd_gain=2 -> bdp 240 -> 量化 248")

# ------------------------------------------------------ pacing rate
MSS = 1200
r1 = bbr_rate_bytes_per_sec(BW, MSS, BBR_UNIT)
close(r1, 142560000, 200000, "0.12 pkt/us × 1e6 × 1200 B × 0.99 = 142.56 MB/s")
r_up = bbr_rate_bytes_per_sec(BW, MSS, bbr_pacing_gain[0])
r_dn = bbr_rate_bytes_per_sec(BW, MSS, bbr_pacing_gain[1])
ok(r_up > r1 > r_dn, "5/4 > 1.0 > 3/4 的序关系")
close(r_up / r1, 1.25, 0.01, "上行相位 ≈ 1.25x")
close(r_dn / r1, 0.75, 0.01, "下行相位 ≈ 0.75x")

# ------------------------------------------------------------ 增益表
eq(bbr_update_gains(STARTUP), (739, 739), "STARTUP:pacing=cwnd=high_gain")
eq(bbr_update_gains(DRAIN), (88, 739), "DRAIN:慢发但保持 cwnd")
eq(bbr_update_gains(PROBE_BW, cycle_idx=0), (320, 512), "PROBE_BW idx=0 -> 1.25x")
eq(bbr_update_gains(PROBE_BW, cycle_idx=1), (192, 512), "PROBE_BW idx=1 -> 0.75x")
for i in (2, 3, 4, 5, 6, 7):
    eq(bbr_update_gains(PROBE_BW, cycle_idx=i), (256, 512),
       "PROBE_BW idx=%d -> 1.0x" % i)
eq(bbr_update_gains(PROBE_BW, cycle_idx=0, lt_use_bw=True), (256, 512),
   "lt_use_bw 时强制 1.0x(成对用例)")
eq(bbr_update_gains(PROBE_RTT), (256, 256), "PROBE_RTT:两个增益都是 1.0")

# -------------------------------------------------- 增益循环与起始相位
eq(bbr_advance_cycle_phase(7), 0, "相位回绕(7+1)&7=0")
eq(bbr_advance_cycle_phase(0), 1, "0 -> 1")
starts = sorted({bbr_reset_probe_bw_mode(r) for r in range(bbr_cycle_rand)})
eq(starts, [0, 2, 3, 4, 5, 6, 7], "起始相位取值集合")
ok(1 not in starts, "**永远不可能**以相位 1 起步")

# ------------------------------------------------------- full_bw 判据
b = Bbr()
b.full_bw = 100
ok(b.check_full_bw(130) is False, "max_bw 130 >= 125 -> 重置计数,未达成")
eq(b.full_bw, 130, "full_bw 被抬到 130")
eq(b.full_bw_cnt, 0, "计数清零")
bw_thresh = (130 * bbr_full_bw_thresh) >> BBR_SCALE
eq(bw_thresh, 162, "130 × 1.25 = 162.5 -> 162")
ok(b.check_full_bw(120) is False, "120 < 162 -> 计数 +1")
ok(b.check_full_bw(120) is False, "第二次仍未达成")
ok(b.check_full_bw(120) is True, "第三次达成 -> pipe full")
ok(b.check_full_bw(300) is True, "已达成后不再变化")
c = Bbr()
c.full_bw = 100
ok(c.check_full_bw(120, is_app_limited=True) is False, "app-limited 的轮次不算")
eq(c.full_bw_cnt, 0, "计数未动")
d = Bbr()
d.full_bw = 100
ok(d.check_full_bw(120, round_start=False) is False, "非 round_start 也不算")
eq(d.full_bw_cnt, 0, "计数未动")

# --------------------------------------------------------- DRAIN 退出
e = Bbr()
e.mode = STARTUP
e.full_bw_reached = True
ev = e.check_drain(packets_in_net_at_edt=5000, max_bw=BW, min_rtt_us=1000)
eq(e.mode, DRAIN, "STARTUP + full_bw_reached -> DRAIN")
eq(ev[0][0], "enter_drain", "事件里带着 ssthresh")
eq(ev[0][1], 126, "ssthresh = inflight(max_bw, 1.0),DRAIN 态不加那 2 包")
# 源码注释里的 "fall through":同一轮里 in-flight 已经足够小就直接进 PROBE_BW
e2 = Bbr()
e2.mode = STARTUP
e2.full_bw_reached = True
ev = e2.check_drain(packets_in_net_at_edt=100, max_bw=BW, min_rtt_us=1000)
eq(e2.mode, PROBE_BW, "STARTUP -> DRAIN -> PROBE_BW 可能发生在同一轮(fall through)")
eq([x[0] for x in ev], ["enter_drain", "enter_probe_bw"], "两个事件都产生")
e3 = Bbr()
e3.mode = DRAIN
ev = e3.check_drain(packets_in_net_at_edt=5000, max_bw=BW, min_rtt_us=1000)
eq(e3.mode, DRAIN, "队列还没排空 -> 留在 DRAIN(成对用例)")

# ----------------------------------------------------------- min_rtt 窗口
f = Bbr()
ok(f.update_min_rtt(now=1, rtt_us=50000) is False, "首个样本:更新 min_rtt,窗口未过期")
eq(f.min_rtt_us, 50000, "min_rtt = 50ms")
ok(f.update_min_rtt(now=2, rtt_us=60000) is False, "更大且不比现有小 -> 不更新")
eq(f.min_rtt_us, 50000, "min_rtt 保持")
ok(f.update_min_rtt(now=5, rtt_us=30000) is False, "更小 -> 更新")
eq(f.min_rtt_us, 30000, "min_rtt 降到 30ms")
eq(f.min_rtt_stamp, 5, "min_rtt_stamp 跟着走")
# 距上次更新 15s > 10s:窗口过期,**更大**的样本也会被采纳
ok(f.update_min_rtt(now=20, rtt_us=90000) is True, "过期 -> 重置并进入 PROBE_RTT")
eq(f.min_rtt_us, 90000, "过期后用新样本重置(即使更大)")
eq(f.mode, PROBE_RTT, "mode = PROBE_RTT")
# 窗口未过期时,更大的样本不会被采纳
f2 = Bbr()
f2.min_rtt_us = 50000
ok(f2.update_min_rtt(now=5, rtt_us=90000) is False, "未过期 -> 大样本不采纳")
eq(f2.min_rtt_us, 50000, "min_rtt 保持")
# delayed ACK:关掉的是「用大样本重置」这条路径,PROBE_RTT 照样进
f3 = Bbr()
f3.min_rtt_us = 50000
ok(f3.update_min_rtt(now=100, rtt_us=200000, is_ack_delayed=True) is True,
   "过期 + delayed ACK:仍进入 PROBE_RTT")
eq(f3.min_rtt_us, 50000, "delayed ACK 的样本不用来重置 min_rtt")
eq(f3.mode, PROBE_RTT, "两条判据是分开的:is_ack_delayed 只管重置")
h = Bbr()
h.min_rtt_stamp = 0
h.min_rtt_us = 50000
h.idle_restart = True
ok(h.update_min_rtt(now=100, rtt_us=50000) is False, "idle_restart 时不进(成对用例)")
eq(h.mode, STARTUP, "仍留在 STARTUP")
i = Bbr()
i.mode = PROBE_RTT
i.min_rtt_stamp = 0
i.min_rtt_us = 50000
ok(i.update_min_rtt(now=100, rtt_us=50000) is False, "已在 PROBE_RTT -> 不重复进")

# ------------------------------------------------------------- PROBE_RTT
p = Bbr()
p.mode = PROBE_RTT
eq(p.probe_rtt_tick(now=1000, packets_in_flight=50), "waiting_for_low_inflight",
   "在网包数 > 4 -> 继续等")
eq(p.probe_rtt_done_stamp, 0, "还没起表")
eq(p.probe_rtt_tick(now=1001, packets_in_flight=4), "armed", "降到 4 包 -> 起表")
eq(p.probe_rtt_done_stamp, 1001 + bbr_probe_rtt_mode_ms, "到点时刻 = now+200ms")
eq(p.probe_rtt_tick(now=1100, packets_in_flight=4), "holding", "时间未到")
eq(p.probe_rtt_tick(now=1201, packets_in_flight=4), "holding",
   "时间到了但还没走完一个 round")
eq(p.probe_rtt_tick(now=1201, packets_in_flight=4, round_start=True), "holding",
   "本轮先置 round_done,下一轮才判定")
q = Bbr()
q.mode = PROBE_RTT
q.probe_rtt_tick(now=1000, packets_in_flight=4)
q.probe_rtt_tick(now=1100, packets_in_flight=4, round_start=True)
eq(q.probe_rtt_tick(now=1201, packets_in_flight=4), "done",
   "超过 200ms 且走完一个 round -> 退出")
eq(q.mode, STARTUP, "未达成 full_bw -> 退回 STARTUP")
r = Bbr()
r.mode = PROBE_RTT
r.full_bw_reached = True
r.probe_rtt_tick(now=1000, packets_in_flight=4)
r.probe_rtt_tick(now=1100, packets_in_flight=4, round_start=True)
r.probe_rtt_tick(now=1201, packets_in_flight=4)
eq(r.mode, PROBE_BW, "已达 full_bw -> 回 PROBE_BW(成对用例)")
s = Bbr()
s.mode = STARTUP
eq(s.probe_rtt_tick(now=1, packets_in_flight=4), None, "非 PROBE_RTT 时无动作")

print("selfcheck OK: %d assertions" % N)
