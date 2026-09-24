"""BBR 拥塞控制(Linux net/ipv4/tcp_bbr.c)的定点算术与状态机模型。

口径:全部常量与公式取自实际读到的 Linux 源码(见 README「参考资料」)。
  * 增益是 **定点** 的:`BBR_SCALE = 8`,`BBR_UNIT = 1 << 8 = 256`。
  * 带宽 bw 的单位是 「packets/μs」 左移 `BW_SCALE = 24` 位(`BW_UNIT = 1<<24`)。
  * 文本未定量说明的分支(如 `bbr_tso_segs_goal`)以参数形式留在外面。
"""

BBR_SCALE = 8
BBR_UNIT = 1 << BBR_SCALE                 # 256
BW_SCALE = 24
BW_UNIT = 1 << BW_SCALE                   # 16777216
USEC_PER_SEC = 1000000

TCP_INIT_CWND = 10
NO_RTT_SAMPLE = 0xFFFFFFFF

CYCLE_LEN = 8
bbr_bw_rtts = CYCLE_LEN + 2               # 10 个 round
bbr_min_rtt_win_sec = 10
bbr_probe_rtt_mode_ms = 200
bbr_cwnd_min_target = 4
bbr_cycle_rand = 7
bbr_pacing_margin_percent = 1
bbr_min_tso_rate = 1200000

# 源码: BBR_UNIT * 2885 / 1000 + 1
bbr_high_gain = BBR_UNIT * 2885 // 1000 + 1        # 739 -> 2.887x
# 源码: BBR_UNIT * 1000 / 2885
bbr_drain_gain = BBR_UNIT * 1000 // 2885           # 88  -> 0.344x
bbr_cwnd_gain = BBR_UNIT * 2                       # 512 -> 2.0x

bbr_pacing_gain = [
    BBR_UNIT * 5 // 4,    # 320  probe for more available bw
    BBR_UNIT * 3 // 4,    # 192  drain queue and/or yield bw
    BBR_UNIT, BBR_UNIT, BBR_UNIT,          # 256
    BBR_UNIT, BBR_UNIT, BBR_UNIT,          # 256
]

bbr_full_bw_thresh = BBR_UNIT * 5 // 4     # 320 -> 1.25x
bbr_full_bw_cnt = 3

bbr_lt_intvl_min_rtts = 4
bbr_lt_loss_thresh = 50
bbr_lt_bw_ratio = BBR_UNIT // 8            # 32 -> 1/8
bbr_lt_bw_diff = 4000 // 8                 # 500 B/s
bbr_lt_bw_max_rtts = 48
bbr_extra_acked_win_rtts = 5
bbr_extra_acked_max_us = 100 * 1000

STARTUP, DRAIN, PROBE_BW, PROBE_RTT = 0, 1, 2, 3
MODE_NAMES = {STARTUP: "STARTUP", DRAIN: "DRAIN",
              PROBE_BW: "PROBE_BW", PROBE_RTT: "PROBE_RTT"}


def bbr_bdp(bw, min_rtt_us, gain):
    """bbr_bdp():BDP 上界。

    w = bw * min_rtt_us; bdp = ((w * gain) >> BBR_SCALE + BW_UNIT - 1) / BW_UNIT
    注意最后那次除法是**向上取整**,源码注释说这是为了避免负反馈环。
    """
    if min_rtt_us == NO_RTT_SAMPLE:
        return TCP_INIT_CWND
    w = bw * min_rtt_us
    return (((w * gain) >> BBR_SCALE) + BW_UNIT - 1) // BW_UNIT


def bbr_quantization_budget(cwnd, tso_segs=2, mode=PROBE_BW, cycle_idx=0):
    """bbr_quantization_budget():三步,顺序即源码顺序。"""
    cwnd += 3 * tso_segs                 # 允许足够多的整包以喂满两端
    cwnd = (cwnd + 1) & ~1               # 向上取偶,减少延时 ACK
    if mode == PROBE_BW and cycle_idx == 0:
        cwnd += 2                        # 小 BDP 时也要能把 inflight 抬过 BDP
    return cwnd


def bbr_inflight(bw, min_rtt_us, gain, tso_segs=2, mode=PROBE_BW, cycle_idx=0):
    return bbr_quantization_budget(bbr_bdp(bw, min_rtt_us, gain),
                                   tso_segs, mode, cycle_idx)


def bbr_rate_bytes_per_sec(bw, mss, gain):
    """bbr_rate_bytes_per_sec():顺序不能换,源码注释说这是为了不溢出 u64。

    rate *= mss; rate *= gain; rate >>= BBR_SCALE;
    rate *= USEC_PER_SEC/100 * (100 - margin); rate >>= BW_SCALE;
    """
    rate = bw
    rate *= mss
    rate *= gain
    rate >>= BBR_SCALE
    rate *= USEC_PER_SEC // 100 * (100 - bbr_pacing_margin_percent)   # 990000
    return rate >> BW_SCALE


def bbr_update_gains(mode, cycle_idx=0, lt_use_bw=False):
    """bbr_update_gains():返回 (pacing_gain, cwnd_gain)。"""
    if mode == STARTUP:
        return bbr_high_gain, bbr_high_gain
    if mode == DRAIN:
        return bbr_drain_gain, bbr_high_gain      # 慢发但**保持** cwnd
    if mode == PROBE_BW:
        return (BBR_UNIT if lt_use_bw else bbr_pacing_gain[cycle_idx]), bbr_cwnd_gain
    if mode == PROBE_RTT:
        return BBR_UNIT, BBR_UNIT
    raise ValueError("BBR bad mode")


def bbr_advance_cycle_phase(cycle_idx):
    return (cycle_idx + 1) & (CYCLE_LEN - 1)


def bbr_reset_probe_bw_mode(rand_below_cycle_rand):
    """bbr_reset_probe_bw_mode():cycle_idx = CYCLE_LEN-1-rand,**紧接着**就
    bbr_advance_cycle_phase() 一次,所以真正的起始相位在 [0,2..7] 里取值。"""
    idx = CYCLE_LEN - 1 - rand_below_cycle_rand
    return bbr_advance_cycle_phase(idx)


class Bbr:
    """一个 BBR 流的精简状态。"""

    def __init__(self):
        self.mode = STARTUP
        self.cycle_idx = 0
        self.full_bw = 0
        self.full_bw_cnt = 0
        self.full_bw_reached = False
        self.min_rtt_us = NO_RTT_SAMPLE
        self.min_rtt_stamp = 0
        self.idle_restart = False
        self.probe_rtt_done_stamp = 0
        self.probe_rtt_round_done = False
        self.lt_use_bw = False

    # bbr_check_full_bw_reached()
    def check_full_bw(self, max_bw, round_start=True, is_app_limited=False):
        if self.full_bw_reached or not round_start or is_app_limited:
            return self.full_bw_reached
        bw_thresh = (self.full_bw * bbr_full_bw_thresh) >> BBR_SCALE
        if max_bw >= bw_thresh:
            self.full_bw = max_bw
            self.full_bw_cnt = 0
            return False
        self.full_bw_cnt += 1
        self.full_bw_reached = self.full_bw_cnt >= bbr_full_bw_cnt
        return self.full_bw_reached

    # bbr_check_drain():先进入 DRAIN,再看是否已排空
    def check_drain(self, packets_in_net_at_edt, max_bw, min_rtt_us,
                    tso_segs=2):
        events = []
        if self.mode == STARTUP and self.full_bw_reached:
            self.mode = DRAIN
            events.append(("enter_drain",
                           bbr_inflight(max_bw, min_rtt_us, BBR_UNIT,
                                        tso_segs, self.mode, self.cycle_idx)))
        if self.mode == DRAIN and packets_in_net_at_edt <= bbr_bdp(
                max_bw, min_rtt_us, BBR_UNIT):
            self.mode = PROBE_BW
            events.append(("enter_probe_bw", None))
        return events

    # bbr_update_min_rtt() 的 min_rtt 滤波 + PROBE_RTT 进入判据
    def update_min_rtt(self, now, rtt_us, is_ack_delayed=False):
        filter_expired = now > self.min_rtt_stamp + bbr_min_rtt_win_sec
        if rtt_us >= 0 and (rtt_us < self.min_rtt_us or
                            (filter_expired and not is_ack_delayed)):
            self.min_rtt_us = rtt_us
            self.min_rtt_stamp = now
        if (bbr_probe_rtt_mode_ms > 0 and filter_expired
                and not self.idle_restart and self.mode != PROBE_RTT):
            self.mode = PROBE_RTT
            self.probe_rtt_done_stamp = 0
            self.probe_rtt_round_done = False
            return True
        return False

    # BBR_PROBE_RTT 期间:维持 min(200ms, 1 round) 在 cwnd_min_target 个包
    def probe_rtt_tick(self, now, packets_in_flight, round_start=False):
        if self.mode != PROBE_RTT:
            return None
        if not self.probe_rtt_done_stamp:
            if packets_in_flight <= bbr_cwnd_min_target:
                self.probe_rtt_done_stamp = now + bbr_probe_rtt_mode_ms
                self.probe_rtt_round_done = False
                return "armed"
            return "waiting_for_low_inflight"
        if round_start:
            self.probe_rtt_round_done = True
        if self.probe_rtt_round_done and now > self.probe_rtt_done_stamp:
            self.mode = PROBE_BW if self.full_bw_reached else STARTUP
            self.probe_rtt_done_stamp = 0
            return "done"
        return "holding"
