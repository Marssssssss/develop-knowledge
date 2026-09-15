#!/usr/bin/env python3
"""统计检验的选择: 什么时候用均值 ± t 区间, 什么时候必须用中位数 + 分位数/秩检验。

结论先行(与 README 一一对应):
  * t 检验回答"两组**均值**是否不同", 前提是独立同分布、小样本时还要近似正态;
    重尾下均值本身不稳定 —— NIST 明确写"均值缺乏 robustness of validity", 而 Cauchy
    分布下"收集更多数据并不能更准地估计均值"。
  * Mann-Whitney U 回答"一组是否**随机大于**另一组"(P(X>Y) != 0.5), 不做分布假设,
    代价是对正态数据损失效率(ARE = 3/π ≈ 0.955)。
  * 两者回答不同问题, 所以会出现"t 显著、U 不显著"这种看似矛盾的结果 —— 选检验
    的前提是先决定你要回答哪个问题。

统计原语在 stats_core.py, 本文件只做场景实证与断言。
"""

from __future__ import annotations

import math
import random
import statistics
from typing import List, Tuple

from stats_core import (ALPHA, mann_whitney_p, mean_ci_t, median_ci_order_statistic,
                        t_critical, t_two_sided_p, trimmed_mean, welch_t, winsorized_mean)


def _deterministic_groups(n: int = 40) -> Tuple[List[float], List[float]]:
    """无随机性的两组: B 是等间距序列, A 与 B 相同但每 5 个样本被抬高到 140。"""
    b = [100 + (i - (n - 1) / 2) * 0.25 for i in range(n)]
    a = list(b)
    for idx in range(0, n, 5):
        a[idx] = 140.0
    return a, b


def _self_test() -> None:
    # 1) t 分布锚点
    tc = t_critical(10, 0.05)
    assert abs(tc - 2.228139) < 1e-5, tc
    assert abs(t_two_sided_p(2.228139, 10) - 0.05) < 1e-6
    assert abs(t_two_sided_p(1.96, 1e7) - 0.05) < 2e-3  # 大自由度退化到正态
    print(f"[锚点] t 分布 df=10 双尾临界值 {tc:.6f}(教科书 2.228139)")

    # 2) 复现 NIST 手册 7.3.1 的 Welch 算例
    m1, sd1, n1 = 36.0909, 4.9082, 11
    m2, sd2, n2 = 32.2222, 2.5386, 9
    v1, v2 = sd1 ** 2 / n1, sd2 ** 2 / n2
    t = (m1 - m2) / math.sqrt(v1 + v2)
    df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
    assert abs(t - 2.2694) < 2e-3, t
    assert 15.4 < df < 15.7, df
    print(f"[锚点] NIST 7.3.1 算例: t={t:.4f}(手册 2.2694), ν={df:.2f}(手册 15.5)")

    # 3) 场景 A: 整组平移(真实均值差异) -> 两种检验都显著
    a, b = _deterministic_groups()
    shifted = [v + 3.0 for v in b]
    p_t_a = welch_t(b, shifted)[2]
    p_u_a = mann_whitney_p(b, shifted)[1]
    assert p_t_a < 1e-3 and p_u_a < 1e-3, (p_t_a, p_u_a)
    print(f"[场景A 整组平移] t p={p_t_a:.2e}, U p={p_u_a:.2e} -> 都显著")

    # 4) 场景 B: 8/40 个样本被抬高 40 -> 均值被拉动(t 显著), 秩几乎不变(U 不显著)
    p_t_b = welch_t(a, b)[2]
    p_u_b = mann_whitney_p(a, b)[1]
    assert p_t_b < 0.05 and p_u_b > 0.05, (p_t_b, p_u_b)
    print(f"[场景B 局部抬高] t p={p_t_b:.4f}(显著), U p={p_u_b:.4f}(不显著)")
    print("     同一批数据两种结论 —— t 检测均值, U 检测随机优势(P(X>Y)-0.5)")

    # 5) 场景 C: 重尾下各位置估计量的稳定性, 以及单个极端值对均值/中位数的影响
    rng = random.Random(7)
    spikes = [math.exp(rng.gauss(0.0, 1.5)) for _ in range(21)]
    spikes[0] = spikes[0] * 1000
    reps = 300
    estimators = {"mean": statistics.fmean, "median": statistics.median,
                  "trimmed5%": trimmed_mean, "winsorized5%": winsorized_mean}
    spread = {}
    for name, fn in estimators.items():
        vals = []
        for _ in range(reps):
            s = [math.exp(rng.gauss(0.0, 1.5)) for _ in range(21)]
            s[rng.randrange(21)] = s[0] * 1000
            vals.append(fn(s))
        spread[name] = statistics.stdev(vals)
    assert spread["mean"] > 3 * spread["median"], spread
    assert spread["trimmed5%"] < spread["mean"] and spread["winsorized5%"] < spread["mean"]
    print("[场景C 重尾] 位置估计量散布(越小越稳): " +
          ", ".join(f"{k}={v:.3f}" for k, v in sorted(spread.items(), key=lambda kv: kv[1])))
    print(f"     单个 1000 倍离群点: 均值 {statistics.fmean(spikes):.1f} vs 中位数 "
          f"{statistics.median(spikes):.3f}(真值 ≈1)")

    # 6) 场景 D: 正态下 U 相对 t 的效率。ARE 是 n→∞ 的极限, 有限样本功效比不等于它
    ratios = {}
    for n, effect, trials in ((10, 0.6, 3000), (150, 0.25, 2000)):
        hit_t = hit_u = 0
        for _ in range(trials):
            s1 = [rng.gauss(0, 1) for _ in range(n)]
            s2 = [rng.gauss(effect, 1) for _ in range(n)]
            hit_t += 1 if welch_t(s1, s2)[2] < ALPHA else 0
            hit_u += 1 if mann_whitney_p(s1, s2)[1] < ALPHA else 0
        ratios[n] = (hit_t / trials, hit_u / trials, hit_u / hit_t)
    assert 0.85 <= ratios[10][2] <= 1.02, ratios[10]
    assert ratios[150][2] >= 0.93, ratios[150]
    for n in (10, 150):
        pt, pu, r = ratios[n]
        print(f"[场景D 效率] n={n:<4} 功效 t={pt:.3f} U={pu:.3f} -> 比值 {r:.3f}")
    print(f"     ARE 理论 3/π ≈ {3 / math.pi:.3f}: n=10 的 {ratios[10][2]:.3f} 明显偏离, "
          f"n=150 的 {ratios[150][2]:.3f} 才靠近")

    # 7) 场景 E: 强偏态下均值 t 区间覆盖率失真, 中位数区间保持有效
    cov_t = cov_med = 0
    trials_e = 600
    for _ in range(trials_e):
        s = [math.exp(rng.gauss(0.0, 1.5)) for _ in range(10)]
        true_mean, true_med = math.exp(1.5 ** 2 / 2), 1.0
        lo, hi = mean_ci_t(s)
        cov_t += 1 if lo <= true_mean <= hi else 0
        mlo, mhi = median_ci_order_statistic(s)
        cov_med += 1 if mlo <= true_med <= mhi else 0
    cov_t, cov_med = cov_t / trials_e, cov_med / trials_e
    assert cov_t < 0.92, cov_t
    assert cov_med >= 0.90, cov_med
    print(f"[场景E 覆盖率] 对数正态(σ=1.5, n=10): 均值 t 区间 {cov_t:.3f}(名义 0.95), "
          f"中位数区间 {cov_med:.3f}")

    # 8) 场景 F: Cauchy —— 加样本量也不能改善均值估计(NIST 原文结论)
    spreads = []
    for n in (10, 1000):
        vals = [statistics.fmean([rng.gauss(0, 1) / rng.gauss(0, 1) for _ in range(n)])
                for _ in range(120)]
        spreads.append(statistics.stdev(vals))
    med_vals = [statistics.median([rng.gauss(0, 1) / rng.gauss(0, 1) for _ in range(1000)])
                for _ in range(120)]
    assert spreads[1] > 0.5 * spreads[0], spreads
    assert statistics.stdev(med_vals) < 0.3 * spreads[1]
    print(f"[场景F Cauchy] 均值散布 n=10 -> {spreads[0]:.2f}, n=1000 -> {spreads[1]:.2f}"
          f"(不下降); 中位数(n=1000) -> {statistics.stdev(med_vals):.2f}")


if __name__ == "__main__":
    _self_test()
    print("\ntest_selection: 全部自检通过")
