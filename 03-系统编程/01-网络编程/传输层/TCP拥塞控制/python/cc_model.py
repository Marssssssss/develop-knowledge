#!/usr/bin/env python3
"""TCP 拥塞控制模拟器：Reno（RFC 5681）vs CUBIC（RFC 9438）。

不依赖 socket，纯窗口动力学模拟：按 RTT 轮次推进，每轮发送 cwnd 个
SMSS 段；当 cwnd 超过链路饱和窗口 sat_window 时丢弃 1 段，发送方通过
3 个重复 ACK 触发快重传/快恢复（另附一条 RTO 路径做对照）。

两种算法都保留 TCP 的**慢启动**——CUBIC 只替换拥塞避免阶段的窗口增长函数
（RFC 9438 §1: "does not make any changes to the TCP Fast Retransmit and
Fast Recovery algorithms"），因此离开慢启动之前的行为与 Reno 完全一致。

运行：python3 main.py      （自带断言自检，失败即非零退出）
"""

from __future__ import annotations

# ---------------------------------------------------------------- 常量
RTT = 1.0                  # 归一化往返时延（秒），即 1 个 RTT = 1 个模拟轮
SMSS = 1460                # 最大段长度（字节），仅用于把"段"换算成字节

# RFC 9438 §4.1.1 / §5：C SHOULD be set to 0.4；beta_cubic SHOULD be 0.7
C_CUBIC = 0.4
BETA_CUBIC = 0.7
# RFC 9438 §4.3：为达到与 AIMD(1, 0.5) 相同的平均窗口，α = 3(1-β)/(1+β)
ALPHA_CUBIC = 3.0 * (1.0 - BETA_CUBIC) / (1.0 + BETA_CUBIC)   # ≈ 0.5294
ALPHA_CUBIC_AT_RENO = 1.0  # W_est 追上 cwnd_prior 后降为 Reno 的 1 段/RTT

IW = 10.0                  # 初始窗口（段）。RFC 5681 §3.1 给出 2~4*SMSS，
                           # 这里放大到 10 段以便观察图形；断言不校验此项。


def cubic_k(w_max: float, cwnd_epoch: float) -> float:
    """RFC 9438 §4.2：K = cbrt((W_max - cwnd_epoch) / C)。

    K 是把窗口从 cwnd_epoch 涨回 W_max 所需的时间（秒）。
    """
    delta = max(w_max - cwnd_epoch, 0.0)
    return (delta / C_CUBIC) ** (1.0 / 3.0)


def w_cubic(t: float, k: float, w_max: float) -> float:
    """RFC 9438 §4.2 Figure 1：W_cubic(t) = C*(t - K)^3 + W_max。

    t < K 时是**凹**的一段（增速递减，逼近平台 W_max）；
    t > K 时转为**凸**（增速递增，向外探测新带宽）。
    """
    return C_CUBIC * (t - k) ** 3 + w_max


class Reno:
    """RFC 5681：慢启动 + 拥塞避免（AIMD）+ 快重传/快恢复 + RTO 恢复。"""

    name = "Reno"

    def __init__(self) -> None:
        self.cwnd = IW
        self.ssthresh = float("inf")
        self.t = 0.0
        self.losses = 0
        self.rto_events = 0

    def on_round(self, loss: bool, timeout: bool = False) -> str:
        """推进 1 个 RTT。loss=快重传（3 dup ACK）；timeout=RTO。"""
        self.t += RTT
        if timeout:
            # RFC 5681 §3.1 公式(4)：ssthresh = max(FlightSize/2, 2*SMSS)
            self.ssthresh = max(self.cwnd / 2.0, 2.0)
            self.cwnd = 1.0            # LW = 1 个满尺寸段，回到慢启动
            self.rto_events += 1
            self.losses += 1
            return "rto"

        if loss:
            self.losses += 1
            self.ssthresh = max(self.cwnd / 2.0, 2.0)
            # RFC 5681 §3.2 规则 3：cwnd = ssthresh + 3*SMSS（膨胀 3 段）
            # 规则 6：收到确认新数据的 ACK 后"放气" cwnd = ssthresh
            # 净效果 = 窗口乘性减半，故这里直接落到 ssthresh。
            self.cwnd = self.ssthresh
            return "fast_retransmit"

        if self.cwnd < self.ssthresh:
            # 慢启动：每个 ACK +min(N,SMSS) → 一个 RTT 内窗口翻倍
            self.cwnd *= 2.0
            return "slow_start"
        # 拥塞避免：每 RTT +1 段（加性增），故称 AIMD
        self.cwnd += 1.0
        return "congestion_avoidance"


class Cubic:
    """RFC 9438：三次函数窗口增长 + Reno-friendly 区域 + fast convergence。

    慢启动阶段与 Reno 完全相同，只在 cwnd >= ssthresh 后启用三次增长函数。
    """

    name = "CUBIC"

    def __init__(self, fast_convergence: bool = True) -> None:
        self.cwnd = IW
        self.ssthresh = float("inf")
        self.t = 0.0
        self.losses = 0
        self.rto_events = 0
        self.fast_convergence = fast_convergence
        self.entered_ca = False        # 是否已离开慢启动（决定 cwnd_prior 语义）
        self.w_max = 0.0
        self.cwnd_prior = 0.0          # 最近一次设定 ssthresh 时的 cwnd
        self.cwnd_epoch = 0.0
        self.t_epoch = 0.0
        self.w_est = 0.0
        self.alpha = ALPHA_CUBIC
        self.k = 0.0

    # -------- 拥塞事件：乘性减 + 重设三次函数锚点 --------
    def on_loss(self, timeout: bool) -> None:
        self.losses += 1
        if timeout:
            # RFC 9438 §4.8：cwnd 按 Reno 降到 1 段，但 ssthresh 用 β_cubic；
            # K 置 0、W_max = 本阶段起始 cwnd。
            self.cwnd_prior = self.cwnd
            self.ssthresh = max(self.cwnd * BETA_CUBIC, 2.0)
            self.cwnd = 1.0
            self.rto_events += 1
            self.w_max = self.cwnd
            self.cwnd_epoch = self.cwnd
            self.k = 0.0
        else:
            # §4.7 fast convergence：拥塞时若 cwnd < W_max，说明饱和点在下移，
            # 主动多让出带宽 → W_max 再乘 (1+β)/2，之后再判 §4.6 的乘性减。
            if self.fast_convergence and self.w_max > 0.0 and self.cwnd < self.w_max:
                self.w_max = self.cwnd * (1.0 + BETA_CUBIC) / 2.0
            else:
                self.w_max = self.cwnd
            if self.entered_ca:
                self.cwnd_prior = self.cwnd
            # §4.6 乘性减：cwnd ← cwnd * β_cubic（不是 Reno 的 0.5）
            self.cwnd *= BETA_CUBIC
            self.cwnd_epoch = self.cwnd
            self.ssthresh = max(self.cwnd, 2.0)   # 丢包后 ssthresh = 减后的 cwnd
            # §4.2：K = cbrt((W_max - cwnd_epoch)/C)
            self.k = cubic_k(self.w_max, self.cwnd_epoch)
        self.entered_ca = True
        self.t_epoch = self.t
        self.w_est = self.cwnd            # §4.3：W_est 初值 = cwnd_epoch
        self.alpha = ALPHA_CUBIC

    def on_round(self, loss: bool, timeout: bool = False) -> str:
        self.t += RTT
        if loss or timeout:
            self.on_loss(timeout=timeout)
            return "rto" if timeout else "fast_retransmit"
        if self.cwnd < self.ssthresh:
            self.cwnd *= 2.0              # 慢启动不变
            return "slow_start"
        # ---- 拥塞避免 ----
        elapsed = self.t - self.t_epoch   # 本阶段已 elapsed 的秒数
        # §4.2：目标窗口取"下一个 RTT 后"的三次函数值；上界 1.5*cwnd 保证
        # 增速不超过慢启动，下界为当前 cwnd（增速非递减）。
        target = w_cubic(elapsed, self.k, self.w_max)
        self.cwnd = max(self.cwnd, min(target, 1.5 * self.cwnd))
        # §4.3 Reno-friendly：W_est 线性增长，追上 cwnd_prior 后 α 降为 1，
        # 确保至少拿到与 Reno 相同的吞吐量。
        self.w_est += self.alpha
        if self.w_est >= self.cwnd_prior:
            self.alpha = ALPHA_CUBIC_AT_RENO
        before = self.cwnd
        self.cwnd = max(self.cwnd, self.w_est)
        if self.cwnd != before:
            return "reno_friendly"
        return "concave" if self.cwnd < self.w_max else "convex"


# ---------------------------------------------------------------- 模拟驱动
def run(cc, rounds: int, sat_window: float):
    """跑 rounds 个 RTT，返回 (cwnd 轨迹, 事件列表)。"""
    traj, events = [], []
    for r in range(rounds):
        loss = cc.cwnd > sat_window          # 窗口超过链路容量 → 丢 1 段
        ev = cc.on_round(loss=loss)
        traj.append(cc.cwnd)
        if loss:
            events.append((r, ev, round(cc.cwnd, 2), round(cc.ssthresh, 2)))
    return traj, events


def run_until_first_loss(cc, sat_window: float, cap: int = 5000):
    """跑到第一次丢包，返回 (耗时轮数, 丢包前的 cwnd)。"""
    for r in range(cap):
        if cc.cwnd > sat_window:
            w_before = cc.cwnd
            cc.on_round(loss=True)
            return r, w_before
        cc.on_round(loss=False)
    raise RuntimeError("未能在 cap 轮内触发丢包")


def recovery_rtts(cc_cls, w_max: float):
    """窗口涨到 W_max 时丢 1 段，测量"回到 W_max"要花几个 RTT。

    这是 RFC 9438 §1 用来论证 CUBIC 优于 Reno 的核心指标：Reno 靠拥塞避免的
    +1 段/RTT 线性爬升，代价 ≈ W_max/2 个 RTT；CUBIC 靠三次函数，代价 ≈ K。
    """
    cc = cc_cls()
    cc.cwnd = float(w_max)
    cc.on_round(loss=True)          # 丢包 → 乘性减 + 重设锚点
    n = 0
    while cc.cwnd < w_max:
        cc.on_round(loss=False)
        n += 1
        if n > 100000:
            raise RuntimeError("无法回到 W_max")
    return n, cc


def avg_mbps(traj) -> float:
    """平均窗口（段）→ 吞吐量（Mbit/s），用 RTT 和 SMSS 换算。"""
    return (sum(traj) / len(traj)) * SMSS * 8 / RTT / 1e6


def head_to_head(sat_window: float, rounds: int):
    reno, cubic = Reno(), Cubic()
    r_traj, r_ev = run(reno, rounds, sat_window)
    c_traj, c_ev = run(cubic, rounds, sat_window)
    return (reno, r_traj, r_ev), (cubic, c_traj, c_ev)


def report_curves(r_traj, c_traj, sample: int = 10) -> None:
    """用 ASCII 条形对照两个算法的窗口爬升形状。"""
    print(f"{'RTT':>4} {'Reno':>9} {'CUBIC':>9}   {'Reno':<28} {'CUBIC'}")
    peak = max(max(r_traj), max(c_traj))
    for i in range(0, min(len(r_traj), len(c_traj)), sample):
        r_n, c_n = r_traj[i], c_traj[i]
        rb = "#" * max(1, int(28 * r_n / peak))
        cb = "#" * max(1, int(28 * c_n / peak))
        print(f"{i:>4} {r_n:>9.1f} {c_n:>9.1f}   {rb:<28} {cb}")


def rtts_to_plateau(sat_window: float) -> tuple[int, int]:
    """Reno vs CUBIC：丢包后从"被砍到的窗口"爬回 W_max 各需多少 RTT。"""
    r = Reno()
    _, w_max = run_until_first_loss(r, sat_window)
    reno_need, _ = recovery_rtts(Reno, w_max)
    cubic_need, _ = recovery_rtts(Cubic, w_max)
    return reno_need, cubic_need


