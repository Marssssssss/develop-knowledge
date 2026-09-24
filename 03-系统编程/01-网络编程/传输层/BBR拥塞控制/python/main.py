"""演示入口:BBR 的定点增益、BDP 量化与状态机迁移。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bbr_model import (                                   # noqa: E402
    Bbr, BBR_UNIT, BW_SCALE, CYCLE_LEN,
    bbr_high_gain, bbr_drain_gain, bbr_cwnd_gain, bbr_pacing_gain,
    bbr_bdp, bbr_inflight, bbr_rate_bytes_per_sec, bbr_update_gains,
    bbr_reset_probe_bw_mode, bbr_full_bw_thresh,
    STARTUP, DRAIN, PROBE_BW, PROBE_RTT, MODE_NAMES,
)

BW = (1 << BW_SCALE) * 12 // 100     # 0.12 pkts/us 的定点表示
MSS = 1200


def main():
    print("== ① 定点增益(BBR_SCALE=8,BBR_UNIT=256) ==")
    print("  %-14s %-8s %s" % ("名称", "定点值", "浮点值"))
    for name, v in (("high_gain", bbr_high_gain), ("drain_gain", bbr_drain_gain),
                    ("cwnd_gain", bbr_cwnd_gain)):
        print("  %-14s %-8d %.4f" % (name, v, v / BBR_UNIT))
    print("  high_gain × drain_gain >> 8 = %d  (≈1,但不是精确 1)"
          % ((bbr_high_gain * bbr_drain_gain) >> 8))

    print("\n== ② PROBE_BW 的 8 相增益循环 ==")
    print("  %-8s %-10s %s" % ("相位", "定点", "含义"))
    for i, g in enumerate(bbr_pacing_gain):
        tag = {0: "探更多带宽", 1: "排空/让出带宽"}.get(i, "以 1.0x 巡航")
        print("  %-8d %-10d %.4f  %s" % (i, g, g / BBR_UNIT, tag))
    print("  随机起始相位集合 = %s  <- **永不含 1**"
          % sorted({bbr_reset_probe_bw_mode(r) for r in range(7)}))

    print("\n== ③ bbr_bdp / bbr_inflight(bw=0.12 pkt/us, min_rtt=1ms) ==")
    print("  %-12s %-8s %-10s %-10s" % ("增益", "定点", "bdp", "inflight"))
    for name, g in (("1.0(UNIT)", BBR_UNIT), ("2.0(cwnd)", bbr_cwnd_gain),
                    ("2.887(high)", bbr_high_gain)):
        print("  %-12s %-8d %-10d %-10d"
              % (name, g, bbr_bdp(BW, 1000, g), bbr_inflight(BW, 1000, g)))
    print("  bdp(1.0)=120 正是 0.12 pkt/us × 1000 us;量化后 +3*TSO 并抬到偶数")

    print("\n== ④ pacing rate(含 1%% 留白,MSS=%d) ==" % MSS)
    print("  %-14s %-12s %s" % ("增益", "bytes/s", "MB/s"))
    for name, g in (("0.75", bbr_pacing_gain[1]), ("1.0", BBR_UNIT),
                    ("1.25", bbr_pacing_gain[0])):
        r = bbr_rate_bytes_per_sec(BW, MSS, g)
        print("  %-14s %-12d %.2f" % (name, r, r / 1e6))
    print("  解析值 0.12×1e6×%d×0.99 = %.2f MB/s(差值来自 2^24 定点量化)"
          % (MSS, 0.12 * 1e6 * MSS * 0.99 / 1e6))

    print("\n== ⑤ 状态机:full_bw 判据(连续 3 轮增长 < 25%) ==")
    b = Bbr()
    b.full_bw = 100
    print("  %-10s %-10s %-10s %s" % ("轮次", "max_bw", "阈值", "结论"))
    for i, mb in enumerate((130, 120, 120, 120), 1):
        thresh = (b.full_bw * bbr_full_bw_thresh) >> 8
        reached = b.check_full_bw(mb)
        print("  %-10d %-10d %-10d full_bw=%d cnt=%d reached=%s"
              % (i, mb, thresh, b.full_bw, b.full_bw_cnt, reached))

    print("\n== ⑥ STARTUP -> DRAIN -> PROBE_BW 的 fall through ==")
    for pin in (5000, 100):
        e = Bbr()
        e.mode = STARTUP
        e.full_bw_reached = True
        ev = e.check_drain(packets_in_net_at_edt=pin, max_bw=BW, min_rtt_us=1000)
        print("  在网 %-5d 包 -> %-10s 事件=%s"
              % (pin, MODE_NAMES[e.mode], [x[0] for x in ev]))

    print("\n== ⑦ min_rtt 窗口 10 s 与 PROBE_RTT ==")
    f = Bbr()
    print("  %-8s %-10s %-12s %s" % ("now", "rtt(us)", "min_rtt", "mode"))
    for now, rtt in ((1, 50000), (2, 60000), (5, 30000), (20, 90000)):
        f.update_min_rtt(now=now, rtt_us=rtt)
        print("  %-8d %-10d %-12s %s" % (now, rtt, f.min_rtt_us, MODE_NAMES[f.mode]))

    print("\n== ⑧ PROBE_RTT:维持 min(200ms, 1 round) 在 4 包 ==")
    p = Bbr()
    p.mode = PROBE_RTT
    print("  %-8s %-10s %s" % ("now", "在飞", "结果"))
    for now, infl, rs in ((1000, 50, False), (1001, 4, False), (1100, 4, False),
                          (1201, 4, False), (1201, 4, True), (1202, 4, True)):
        print("  %-8d %-10d %s" % (now, infl, p.probe_rtt_tick(now, infl, rs)))
    print("  最终 mode = %s" % MODE_NAMES[p.mode])

    print("\n== ⑨ 一句话 ==")
    print("  BBR 的核心不是「看到丢包/时延就退」,而是「估 BDP 并按增益表去探」。")
    print("  pacing_rate = gain × bw × 99%,cwnd = quantize(ceil(bw × min_rtt × gain))。")


if __name__ == "__main__":
    main()
