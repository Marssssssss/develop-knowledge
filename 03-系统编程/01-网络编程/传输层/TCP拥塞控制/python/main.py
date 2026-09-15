#!/usr/bin/env python3
"""TCP 拥塞控制自检：场景编排与断言（模型在 cc_model.py）。

运行：python3 main.py      （自带断言自检，失败即非零退出）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cc_model import *  # noqa: E402,F401,F403
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
