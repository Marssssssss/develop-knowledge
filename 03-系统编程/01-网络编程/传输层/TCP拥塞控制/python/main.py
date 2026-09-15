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


# ---------------------------------------------------------------- 自检
def self_check() -> int:
    ok = 0

    # 1. K 的定义自洽：W_cubic(K) 必须正好回到 W_max
    for w_max in (50.0, 200.0, 1000.0):
        k = cubic_k(w_max, w_max * BETA_CUBIC)
        assert abs(w_cubic(k, k, w_max) - w_max) < 1e-9, "W_cubic(K) != W_max"
    ok += 1

    # 2. 拥塞事件瞬间 cwnd == W_max * beta_cubic（0.7，而不是 Reno 的 0.5）
    c = Cubic()
    run_until_first_loss(c, 200.0)
    assert abs(c.cwnd - c.w_max * BETA_CUBIC) < 1e-9, "beta_cubic 不是 0.7"
    assert abs(c.cwnd_epoch - c.cwnd) < 1e-9, "cwnd_epoch 应为减后的 cwnd"
    ok += 1

    # 3. Reno 拥塞避免阶段每 RTT 恰好 +1 段（AIMD 的加性增）
    r = Reno()
    r.cwnd, r.ssthresh = 100.0, 1.0           # 强制进入拥塞避免
    r.on_round(loss=False)
    assert abs(r.cwnd - 101.0) < 1e-9, "Reno 拥塞避免增长不等于 +1/RTT"
    ok += 1

    # 4. Reno 快重传后 ssthresh = max(cwnd/2, 2)，且 cwnd 放气到 ssthresh
    r2 = Reno()
    r2.cwnd = 100.0
    r2.on_round(loss=True)
    assert r2.ssthresh == 50.0 and r2.cwnd == 50.0, "Reno 快恢复减半错误"
    ok += 1

    # 5. Reno 慢启动阶段一个 RTT 窗口翻倍
    r3 = Reno()
    r3.cwnd, r3.ssthresh = 8.0, 1e9
    r3.on_round(loss=False)
    assert r3.cwnd == 16.0, "慢启动未翻倍"
    ok += 1

    # 6. CUBIC 凹段初期增速快于 Reno 的 +1 段/RTT（"高 BDP 利用率更高"的机理）
    c2 = Cubic()
    run_until_first_loss(c2, 400.0)
    start = c2.cwnd
    c2.on_round(loss=False)
    assert c2.cwnd - start > 1.0, f"CUBIC 凹段增速 {c2.cwnd - start:.2f} 应 > 1"
    ok += 1

    # 7. CUBIC 恰好花 K 个 RTT 从 cwnd_epoch 回到平台 W_max
    c3 = Cubic()
    run_until_first_loss(c3, 400.0)
    k = c3.k
    reached = None
    for i in range(1, 200):
        c3.on_round(loss=False)
        if reached is None and c3.cwnd >= c3.w_max - 1e-6:
            reached = i
            break
    assert reached is not None and abs(reached - k) <= 1.0, \
        f"回到 W_max 用了 {reached} RTT，K={k:.2f}"
    ok += 1

    # 8. 凹 → 平台 → 凸：每轮窗口增量先递减、在 t≈K 处触底、之后递增
    c4 = Cubic()
    run_until_first_loss(c4, 400.0)
    gains = []
    for _ in range(int(c4.k) + 14):
        prev = c4.cwnd
        c4.on_round(loss=False)
        gains.append(c4.cwnd - prev)
    trough = gains.index(min(gains)) + 1          # 增速最低点的 RTT 序号
    assert abs(trough - c4.k) <= 3, f"增速触底于第 {trough} 轮，K={c4.k:.2f}"
    assert gains[0] > gains[trough - 1] and gains[-1] > gains[trough - 1], \
        "平台两侧的增速都应高于平台处"
    ok += 1

    # 9. fast convergence：cwnd < W_max 时再丢包会额外收缩 W_max
    c5 = Cubic()
    run_until_first_loss(c5, 400.0)
    first_w_max = c5.w_max
    c5.on_round(loss=False)                   # 只收回一点点 (< W_max)
    w_after = c5.cwnd
    c5.on_round(loss=True)
    assert c5.w_max < first_w_max, "fast convergence 未收缩 W_max"
    assert abs(c5.w_max - w_after * (1 + BETA_CUBIC) / 2) < 1e-9, "FC 公式错误"
    c6 = Cubic(fast_convergence=False)
    run_until_first_loss(c6, 400.0)
    w1 = c6.w_max
    c6.on_round(loss=False)
    w_after2 = c6.cwnd
    c6.on_round(loss=True)
    assert abs(c6.w_max - w_after2) < 1e-9 and c6.w_max < w1, "关闭 FC 后应取当前 cwnd"
    ok += 1

    # 10. RTO：两者 cwnd 都落到 1 段；CUBIC 的 ssthresh 用 β_cubic
    r4 = Reno()
    r4.cwnd = 80.0
    r4.on_round(loss=False, timeout=True)
    assert r4.cwnd == 1.0 and r4.ssthresh == 40.0, "Reno RTO 处理错误"
    c7 = Cubic()
    run_until_first_loss(c7, 400.0)
    c7.cwnd = 80.0
    c7.on_round(loss=False, timeout=True)
    assert c7.cwnd == 1.0, "CUBIC RTO 后 cwnd 应为 1 段"
    assert abs(c7.ssthresh - 56.0) < 1e-9, "CUBIC RTO 的 ssthresh 应用 β_cubic=0.7"
    assert c7.k == 0.0, "RTO 后 K 应置 0"
    ok += 1

    # 11. 高 BDP：CUBIC 平均窗口 > Reno（RFC 9438 §1 的核心论断）
    (_, r_traj, _), (_, c_traj, _) = head_to_head(400.0, 400)
    r_avg = sum(r_traj) / len(r_traj)
    c_avg = sum(c_traj) / len(c_traj)
    assert c_avg > r_avg, f"CUBIC 平均窗口 {c_avg:.1f} 未超过 Reno {r_avg:.1f}"
    ok += 1

    # 12. 低 BDP（Reno 舒适区）CUBIC 不应明显劣于 Reno（Reno-friendly 区域）
    (_, r_small, _), (_, c_small, _) = head_to_head(40.0, 400)
    r_s = sum(r_small) / len(r_small)
    c_s = sum(c_small) / len(c_small)
    assert c_s >= r_s * 0.98, f"小 BDP 下 CUBIC {c_s:.1f} 明显差于 Reno {r_s:.1f}"
    ok += 1

    print(f"[self-check] {ok}/12 项断言全部通过")
    return ok


def main() -> None:
    self_check()

    print("\n=== 1) 高 BDP 长肥管道（饱和窗口 400 段）：窗口轨迹对照 ===")
    (reno, r_traj, r_ev), (cubic, c_traj, c_ev) = head_to_head(400.0, 400)
    report_curves(r_traj, c_traj)
    print(f"\n首次丢包后前 4 个事件 (RTT, 事件, cwnd, ssthresh)：")
    print(f"  Reno : {r_ev[:4]}")
    print(f"  CUBIC: {c_ev[:4]}")
    r_avg, c_avg = sum(r_traj) / len(r_traj), sum(c_traj) / len(c_traj)
    print(f"\n平均窗口 Reno {r_avg:6.1f} 段 → {avg_mbps(r_traj):6.2f} Mbit/s")
    print(f"         CUBIC {c_avg:6.1f} 段 → {avg_mbps(c_traj):6.2f} Mbit/s"
          f"   (+{(c_avg / r_avg - 1) * 100:.1f}% 吞吐)")

    print("\n=== 2) 丢包后爬回原窗口需要多少 RTT（W_max = 640 段）===")
    r_need, c_need = rtts_to_plateau(400.0)
    print(f"  Reno : {r_need:>3} 个 RTT（掉到 W_max/2，拥塞避免每 RTT 只 +1 段）")
    print(f"  CUBIC: {c_need:>3} 个 RTT（只掉到 β·W_max = 0.7·W_max，K = cbrt(0.3·W_max/C)）")
    print(f"  加速比 ≈ {r_need / max(c_need, 1):.0f}×   ← RFC 9438 §1 的核心论断："
          f"Reno 在大 BDP 下窗口恢复太慢")

    print("\n=== 3) 丢包检测方式的影响（饱和窗口 200 段）===")
    for cc_cls in (Reno, Cubic):
        cc = cc_cls()
        while cc.cwnd <= 200.0:
            cc.on_round(loss=False)
        before = cc.cwnd
        cc.on_round(loss=False, timeout=True)
        print(f"  {cc.name:<6} cwnd {before:6.1f} 段 --RTO--> cwnd {cc.cwnd:.0f} 段, "
              f"ssthresh {cc.ssthresh:6.1f} 段")

    print("\n=== 4) CUBIC 三区域分布（400 轮）===")
    for sat in (400.0, 60.0):
        c8 = Cubic()
        counts = {"slow_start": 0, "reno_friendly": 0, "concave": 0,
                  "convex": 0, "fast_retransmit": 0}
        for _ in range(400):
            counts[c8.on_round(loss=c8.cwnd > sat)] += 1
        label = "高 BDP (饱和窗口 400 段)" if sat > 200 else "小 BDP (饱和窗口 60 段)"
        inner = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
        print(f"  {label:<24} {inner}")
    print("  说明：concave 段贴着平台 W_max 走（增速触底），是 CUBIC 维持高链路利用率"
          "的关键；\n        凸段从平台外侧重新加速，用于探测新出现的带宽。"
          "\n        单流模拟里 W_est 几乎从不占优——Reno-friendly 区域的意义在于"
          "与 Reno 流\n        竞争时不吃亏（断言 12 是它的回归测试：小 BDP 下 CUBIC 不得劣于 Reno）。")


if __name__ == "__main__":
    main()
