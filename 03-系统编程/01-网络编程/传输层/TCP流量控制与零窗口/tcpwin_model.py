# -*- coding: utf-8 -*-
"""TCP 流量控制、零窗口探测与窗口扩大 —— 模型层

原文依据（均已实读 RFC 全文）：

RFC 9293 §3.8.6.2.1（发送端 SWS）
    可用窗口 ``U = SND.UNA + SND.WND - SND.NXT``，四条发送判据，
    ``Fs`` 推荐 1/2，override timeout 建议 0.1–1.0 秒。

RFC 9293 §3.8.6.2.2（接收端 SWS）
    ``RCV.BUFF - RCV.USER - RCV.WND >= min(Fr * RCV.BUFF, Eff.snd.MSS)``
    满足时 ``RCV.WND = RCV.BUFF - RCV.USER``；``Fr`` 推荐 1/2。
    并明确：*"Keeping the right window edge fixed as data arrives and is
    acknowledged requires that the receiver offer less than its full buffer
    space"*。

RFC 9293 §3.8.6.1（零窗口探测 ZWP）
    * "Probing of zero (offered) windows MUST be supported (MUST-36)."
    * "A TCP implementation MAY keep its offered receive window closed
      indefinitely (MAY-8)."
    * "the sending TCP peer MUST allow the connection to stay open (MUST-37)"
    * "When the receiving TCP peer has a zero window and a segment arrives,
      it must still send an acknowledgment showing its next expected sequence
      number and current window (zero)."
    * 首个探测在零窗口持续 RTO 之后，之后间隔指数增长。

RFC 7323 §2.2/§2.3（窗口扩大）
    * shift.cnt 上限 14，最大接收窗口 2^(14+16) = 1 GiB
    * ``SND.WND = SEG.WND << Snd.Wind.Shift``
    * ``SEG.WND = RCV.WND >> Rcv.Wind.Shift``
    * "The window field in a segment where the SYN bit is set ... MUST NOT
      be scaled."

RFC 7323 §2.4（窗口回缩）
    非零 scale 下右移会舍掉低位，"Implementations MUST ensure that they
    handle a shrinking window"。
"""

MSS = 1460                 # Eff.snd.MSS，典型以太网
FS = 0.5                   # 发送端 SWS 分数，RFC 推荐 1/2
FR = 0.5                   # 接收端 SWS 分数，RFC 推荐 1/2
OVERRIDE_MIN, OVERRIDE_MAX = 0.1, 1.0   # override timeout 建议区间（秒）

MAX_SHIFT = 14
MAX_SCALED_WINDOW = 1 << (MAX_SHIFT + 16)     # 2^30 = 1 GiB
UNSCALED_MAX = (1 << 16) - 1                  # 65535


# ---------------------------------------------------------------- 接收端
class Receiver:
    """建模 RCV.BUFF / RCV.USER / RCV.NXT / RCV.WND 四元组。"""

    def __init__(self, buff, mss=MSS, shift=0):
        self.buff = buff
        self.mss = mss
        self.shift = shift             # Rcv.Wind.Shift
        self.user = 0                  # 已确认但应用未取走
        self.nxt = 1000                # RCV.NXT
        self.wnd = buff                # RCV.WND（当前通告的真实窗口）
        self.updates = 0               # 实际发出窗口更新的次数
        self.acks_sent = 0

    @property
    def reduction(self):
        """第 3 段「可用但未通告」的空间。"""
        return self.buff - self.user - self.wnd

    def maybe_update_window(self):
        if self.reduction >= min(FR * self.buff, self.mss):
            self.wnd = self.buff - self.user
            self.updates += 1
            return True
        return False

    def receive(self, n):
        """收到并按序确认 n 字节：右边界 RCV.NXT+RCV.WND 保持不动。"""
        room = self.buff - self.user
        if n > room:
            n = room                   # 超出缓冲的字节被丢弃
        self.user += n
        self.nxt += n
        self.wnd = max(0, self.wnd - n)
        return self.maybe_update_window()

    def consume(self, n):
        """应用读走 n 字节：腾出空间，可能触发窗口更新。"""
        if n > self.user:
            n = self.user
        self.user -= n
        return self.maybe_update_window()

    def advertise(self, syn=False):
        """产生线上 16 位 SEG.WND；SYN 段不缩放。"""
        self.acks_sent += 1
        if syn:
            return min(self.wnd, UNSCALED_MAX)     # MUST NOT be scaled
        return self.wnd >> self.shift

    @property
    def zero_window(self):
        return self.wnd == 0


# ---------------------------------------------------------------- 发送端
class Sender:
    """建模 SND.UNA / SND.NXT / SND.WND / Max(SND.WND)。"""

    def __init__(self, mss=MSS, shift=0):
        self.mss = mss
        self.shift = shift             # Snd.Wind.Shift
        self.una = 0
        self.nxt = 0
        self.wnd = 0
        self.max_wnd = 0

    def on_ack(self, ackno, seg_wnd, syn=False):
        self.una = ackno
        self.wnd = seg_wnd if syn else (seg_wnd << self.shift)
        if self.wnd > self.max_wnd:
            self.max_wnd = self.wnd

    def usable(self):
        """U = SND.UNA + SND.WND - SND.NXT"""
        return self.una + self.wnd - self.nxt

    def may_send(self, D, pushed=False, override=False):
        """RFC 9293 §3.8.6.2.1 四条判据，任一成立即发。"""
        U = self.usable()
        if U <= 0:
            return False
        if min(D, U) >= self.mss:                                   # (1)
            return True
        if pushed and self.nxt == self.una and D <= U:              # (2)
            return True
        if self.nxt == self.una and min(D, U) >= FS * self.max_wnd:  # (3)
            return True
        return bool(override)                                       # (4)

    def send(self, n):
        """真正发出 n 字节（调用方需先用 may_send 判定）。"""
        U = self.usable()
        k = min(n, U)
        self.nxt += k
        return k


# ------------------------------------------------------- 零窗口探测调度
def probe_schedule(rto, count):
    """返回前 count 次探测相对于「零窗口起始时刻」的时刻表。

    首个探测在 RTO 之后，之后间隔指数增长：RTO, 2RTO, 4RTO, ...
    """
    times = []
    t = 0.0
    interval = rto
    for _ in range(count):
        t += interval
        times.append(t)
        interval *= 2
    return times


class ZeroWindowProber:
    """零窗口探测状态机。"""

    def __init__(self, rto):
        self.rto = rto
        self.interval = rto
        self.next_at = None            # None = 尚未进入零窗口
        self.sent = 0

    def observe(self, now, window_zero):
        """在每个时刻问一次：此刻该不该发探测？返回 bool。

        零窗口解除就重置状态机，下次重新从 RTO 开始。
        """
        if not window_zero:
            self.next_at = None
            self.interval = self.rto
            return False
        if self.next_at is None:
            self.next_at = now + self.rto
            return False
        if now < self.next_at:
            return False
        self.sent += 1
        self.interval *= 2
        self.next_at = now + self.interval
        return True


# ---------------------------------------------------------------- 缩放工具
def announced_window(rcv_wnd, shift):
    """接收端把真实窗口右移后放进 16 位字段。"""
    return rcv_wnd >> shift


def effective_window(seg_wnd, shift):
    """发送端左移还原，得到真实窗口；低位信息已不可逆地丢失。"""
    return seg_wnd << shift


def quantization_loss(rcv_wnd, shift):
    """右移再左移造成的窗口损失（真实能用的比本来少多少）。"""
    return rcv_wnd - effective_window(announced_window(rcv_wnd, shift), shift)


def shift_for(buff):
    """按接收缓冲大小选 shift.cnt（上限 14）。"""
    s = 0
    while s < MAX_SHIFT and (buff >> s) > UNSCALED_MAX:
        s += 1
    return s
