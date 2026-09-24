"""TCP 延时确认(TCP_QUICKACK)与用户超时(TCP_USER_TIMEOUT)的忠实转写。

口径(全部来自实际读到的源码,见 README「参考资料」):
  * 时间单位统一取 **毫秒**,并假定 HZ == 1000。该假定下
    `msecs_to_jiffies` / `jiffies_to_msecs` 都是恒等映射
    (`_msecs_to_jiffies(m) = (m + MSEC_PER_SEC/HZ - 1) / (MSEC_PER_SEC/HZ)` = m),
    `usecs_to_jiffies(u)` = ceil(u / 1000)。若内核配置成 HZ=250,所有常量要重算。
  * 常量取自 include/net/tcp.h(HZ=1000 分支):
        TCP_DELACK_MAX  = HZ/5   = 200
        TCP_DELACK_MIN  = HZ/25  = 4
        TCP_ATO_MIN     = HZ/25  = 4
        TCP_RTO_MIN     = HZ/5   = 200
        TCP_RTO_MAX     = 120*HZ = 120000
        TCP_TIMEOUT_MIN = 2
        TCP_MAX_QUICKACKS = 16
    注意 tcp.h 里对同一批常量有 `#if` 分支(HZ 很小时退化为 4U 的无量纲写法),
    本模块只实现 HZ=1000 那一支。
"""

HZ = 1000

TCP_DELACK_MAX = HZ // 5        # 200 ms
TCP_DELACK_MIN = HZ // 25       # 40 ms —— 这就是「Linux 延时 ACK 最短 40 ms」的出处
TCP_ATO_MIN = HZ // 25          # 40 ms
TCP_RTO_MIN = HZ // 5           # 200 ms
TCP_RTO_MAX = 120 * HZ          # 120000 ms
TCP_TIMEOUT_MIN = 2             # 2 jiffies
TCP_MAX_QUICKACKS = 16


# ---------------------------------------------------------------- 延时 ACK 引擎
class DelackEngine:
    """转写 icsk_ack 中与 quickack / ato 相关的那几个字段。

    对应内核结构 inet_connection_sock 的 `icsk_ack` 联合体成员:
        ato, quick, lrcvtime, rcv_mss, pending
    以及 inet_connection_sock 层的 `pingpong`(ICSK_ACK_PUSHED 等标志)与
    tcp_sock 的 rcv_wnd / srtt_us。
    """

    ACK_PUSHED = 1
    ACK_PUSHED2 = 2
    ACK_TIMER = 4

    def __init__(self, rcv_mss=1460, rcv_wnd=65536, rto=TCP_RTO_MIN, srtt_us=0,
                 delack_max=TCP_DELACK_MAX):
        self.rcv_mss = rcv_mss
        self.rcv_wnd = rcv_wnd
        self.rto = rto
        self.srtt_us = srtt_us
        self.delack_max = delack_max    # icsk->icsk_delack_max,默认 TCP_DELACK_MAX
        self.ato = 0
        self.quick = 0
        self.lrcvtime = 0
        self.pending = 0
        self.retry = 0
        self.dst_quick_ack = False
        self.pingpong = False

    # tcp_incr_quickack(): net/ipv4/tcp_input.c
    def incr_quickack(self, max_quickacks):
        quickacks = self.rcv_wnd // (2 * self.rcv_mss)
        if quickacks == 0:
            quickacks = 2
        quickacks = min(quickacks, max_quickacks)
        if quickacks > self.quick:
            self.quick = quickacks
        return self.quick

    # tcp_enter_quickack_mode(): 三个动作,顺序即源码顺序
    def enter_quickack_mode(self, max_quickacks):
        self.incr_quickack(max_quickacks)
        self.pingpong = False              # inet_csk_exit_pingpong_mode
        self.ato = TCP_ATO_MIN

    # tcp_in_quickack_mode(): dst_quick_ack 是短路项
    def in_quickack_mode(self):
        return bool(self.dst_quick_ack or (self.quick and not self.pingpong))

    # tcp_event_data_recv(): 只保留 ato/quick 那一段
    def on_data_recv(self, now):
        if self.ato == 0:
            self.incr_quickack(TCP_MAX_QUICKACKS)
            self.ato = TCP_ATO_MIN
        else:
            m = now - self.lrcvtime
            if m <= TCP_ATO_MIN // 2:
                self.ato = (self.ato >> 1) + TCP_ATO_MIN // 2
            elif m < self.ato:
                # 注意括号:C 的源码写作 (ato >> 1) + (u32)m;Python 里 `+`
                # 的优先级**高于** `>>`,漏掉括号会变成 ato >> (1+m) 即 0
                self.ato = min((self.ato >> 1) + m, self.rto, TCP_DELACK_MAX)
            elif m > self.rto:
                self.incr_quickack(TCP_MAX_QUICKACKS)
        self.lrcvtime = now
        return self.ato

    # tcp_send_delayed_ack(): 计算本轮 delack 定时器的相对超时(ms)
    #   apply_final_cap=False 时只返回 max_ato 钳制后的结果,用于观察
    #   pingpong / srtt 两个上限各自的贡献(最终那道 tcp_delack_max() 会把
    #   两者都压到 200 ms,差异会被抹平)。
    def delack_timeout(self, apply_final_cap=True):
        ato = self.ato
        if ato > TCP_DELACK_MIN:
            max_ato = HZ // 2                                   # 500 ms
            if self.pingpong or (self.pending & self.ACK_PUSHED):
                max_ato = TCP_DELACK_MAX
            if self.srtt_us:
                rtt = max(usec_to_ms_ceil(self.srtt_us >> 3), TCP_DELACK_MIN)
                if rtt < max_ato:
                    max_ato = rtt
            ato = min(ato, max_ato)
        return min(ato, self.delack_max) if apply_final_cap else ato

    # tcp_send_ack() 之后内核会清 pending 并把 quick 递减(在
    # tcp_delack_timer_handler 的 __tcp_send_ack 路径之外另行处理),
    # 这里把「发出一个 ACK」封装成一个动作以便模型自洽。
    def send_ack(self):
        self.pending = 0
        self.retry = 0
        if self.quick:
            self.quick -= 1

    # alloc_skb 失败分支:tcp_delack_timer_handler 里的指数退避
    def delack_retry_delay(self):
        delay = TCP_DELACK_MAX << self.retry
        grew = delay < TCP_RTO_MAX
        if grew:
            self.retry += 1
        self.ato = TCP_ATO_MIN
        return delay, grew


def usec_to_ms_ceil(us):
    """usecs_to_jiffies(u) 在 HZ=1000 下等于 ceil(u/1000)。"""
    return -(-us // 1000)


# ------------------------------------------------------------- TCP_USER_TIMEOUT
def clamp_rto_to_user_timeout(user_timeout_ms, rto_ms, elapsed_ms):
    """tcp_clamp_rto_to_user_timeout():Linux 6.x 版。

    返回下一次重传定时器的装填值(ms)。注意 `remaining <= 0` 时返回 **1**
    (1 jiffy,即「立刻到点」)而不是 0——0 在 sk_reset_timer 里没有意义。
    """
    if not user_timeout_ms:
        return rto_ms
    remaining = user_timeout_ms - elapsed_ms
    if remaining <= 0:
        return 1
    return min(rto_ms, remaining)            # msecs_to_jiffies 恒等


def clamp_probe0_to_user_timeout(user_timeout_ms, when_ms, probes_tstamp, now):
    """tcp_clamp_probe0_to_user_timeout():零窗口探测的钳制。

    `remaining` 与 `when` 都被当成 s32 有符号量比较,且剩余时间有
    TCP_TIMEOUT_MIN(2 jiffies)下限。
    """
    if not user_timeout_ms or not probes_tstamp:
        return when_ms
    elapsed = now - probes_tstamp
    if elapsed < 0:
        elapsed = 0
    remaining = user_timeout_ms - elapsed
    remaining = max(remaining, TCP_TIMEOUT_MIN)
    return min(remaining, when_ms)


def ilog2(n):
    """返回 floor(log2(n));只用于非负整数。"""
    assert n > 0
    r = 0
    while (1 << (r + 1)) <= n:
        r += 1
    return r


def model_timeout(boundary, rto_base=TCP_RTO_MIN, rto_max=TCP_RTO_MAX):
    """tcp_model_timeout():返回 **毫秒**(源码末尾有 jiffies_to_msecs)。"""
    linear_backoff_thresh = ilog2(rto_max // rto_base)
    if boundary <= linear_backoff_thresh:
        timeout = ((2 << boundary) - 1) * rto_base
    else:
        timeout = ((2 << linear_backoff_thresh) - 1) * rto_base + \
                  (boundary - linear_backoff_thresh) * rto_max
    return timeout


def retransmits_timed_out(boundary, timeout_ms, retrans_stamp, now, retransmits,
                          rto_base=TCP_RTO_MIN, rto_max=TCP_RTO_MAX):
    """retransmits_timed_out():**先**判 retransmits 再比时间。

    `timeout == 0` 表示「用默认模型」,此时完全忽略调用方给的 boundary 之外的东西。
    比较用 `s32` 有符号减法(源码 `(s32)(now - start_ts - timeout) >= 0`)。
    """
    if not retransmits:
        return False
    if timeout_ms == 0:
        timeout_ms = model_timeout(boundary, rto_base, rto_max)
    return (now - retrans_stamp - timeout_ms) >= 0


def probe_timer_decision(user_timeout_ms, probes_tstamp, now, probes_out,
                         max_probes, packets_out, has_head_skb):
    """tcp_probe_timer():返回 'reset_probes' | 'probe' | 'abort'。

    RFC 1122 4.2.2.17 要求「只要对端还在回探测就一直等下去」,默认靠 ACK 复位
    icsk_probes_out;但设了 TCP_USER_TIMEOUT 就会按时间上限直接 kill。
    """
    if packets_out or not has_head_skb:
        return "reset_probes"
    if not probes_tstamp:
        return "probe"
    if user_timeout_ms and (now - probes_tstamp) >= user_timeout_ms:
        return "abort"
    if probes_out >= max_probes:
        return "abort"
    return "probe"
