#!/usr/bin/env python3
"""最小可检测效应(MDE)与样本量:把"``-count`` 该选多少"变成一个可解的方程。

权威口径来自 NIST/SEMATECH e-Handbook §7.2.2.2(Sample sizes required)原文:

- σ 已知(正态):
  ``N = (z_{1-α/2} + z_{1-β})² (σ/δ)²``  (双侧)
  ``N = (z_{1-α}  + z_{1-β})² (σ/δ)²``  (单侧)
- σ 未知(用样本标准差 s,走 t 分布):
  ``N = (t_{1-α/2} + t_{1-β})² (s/δ)²``
  原文特别强调:"The drawback is that critical values of the t distribution depend on
  known degrees of freedom, which in turn depend upon the sample size which we are trying
  to estimate. Iterate on the initial estimate using critical values from the t table."

本 demo 的三条主线:

1. **复现 NIST 的两个原例**(σ 已知的 8.567→9,与迭代一次的 10.6→11),把"迭代"真的做出来。
2. **用功效模拟把单样本公式推广到两组**,而不是硬背一个系数 2:两组的非中心参数
   ``δ/(σ√(2/n))`` 与单样本 ``δ/(σ/√n)`` 相等时解出的 n 恰好是 2 倍,再用蒙特卡洛验证。
3. **把公式翻过来回答工程问题** —— 给定变异系数 CoV 与目标回归幅度 MDE,
   反解出每侧需要几次 ``-count``;并核对 benchstat 官方"至少 10 次、理想 20 次"的建议
   在这个方程里对应多大的 MDE。

无第三方依赖;固定种子保证自检可复现。
"""

from __future__ import annotations

import math
import random

from mde_stats import mannwhitney_u_p, norm_ppf, norm_sf, t_ppf, welch_t

ALPHA = 0.05
POWER = 0.80


def n_one_sample_z(alpha: float, power: float, sigma_over_delta: float,
                   two_sided: bool = True) -> float:
    """NIST §7.2.2.2 的 σ 已知公式(原始形态,不取整)。"""
    z_a = norm_ppf(1 - alpha / 2) if two_sided else norm_ppf(1 - alpha)
    z_b = norm_ppf(power)
    return (z_a + z_b) ** 2 * sigma_over_delta ** 2


def n_one_sample_t_iterative(alpha: float, power: float, sigma_over_delta: float,
                             two_sided: bool = True, max_iter: int = 20) -> int:
    """σ 未知时的迭代解:先按正态估一版,再用 df=N-1 的 t 临界值反复代入。"""
    n = n_one_sample_z(alpha, power, sigma_over_delta, two_sided)
    cur = max(2, math.ceil(n))
    for _ in range(max_iter):
        df = cur - 1
        t_a = t_ppf(1 - alpha / 2, df) if two_sided else t_ppf(1 - alpha, df)
        t_b = t_ppf(power, df)
        nxt = max(2, math.ceil((t_a + t_b) ** 2 * sigma_over_delta ** 2))
        if nxt == cur:
            return cur
        cur = nxt
    return cur


def n_two_sample_z(alpha: float, power: float, sigma_over_delta: float,
                   two_sided: bool = True) -> float:
    """等样本量两组的每侧 n。

    推导(不是背系数):两组的均值差标准误是 ``σ√(2/n)``,单样本的是 ``σ/√n``。
    要让检验的非中心参数 ``δ/SE`` 相同,需 ``√(2/n_two) = √(1/n_one)``,即 ``n_two = 2·n_one``。
    下面直接用这个等式,自检里再用蒙特卡洛把它验一遍。
    """
    return 2.0 * n_one_sample_z(alpha, power, sigma_over_delta, two_sided)


def mde_rel_from_n(alpha: float, power: float, cov: float, n_per_group: int) -> float:
    """给定每侧样本量 n 与变异系数 CoV,反解能检出的最小**相对**回归。

    把 ``σ/δ = CoV/MDE_rel`` 代入 ``n = 2(z_a+z_b)²(σ/δ)²`` 解出 MDE_rel。
    """
    z_a = norm_ppf(1 - alpha / 2)
    z_b = norm_ppf(power)
    return (z_a + z_b) * cov * math.sqrt(2.0 / n_per_group)


def n_from_cov_mde(alpha: float, power: float, cov: float, mde_rel: float) -> int:
    """给定 CoV 与目标 MDE,反解每侧需要几次 ``-count``。"""
    z_a = norm_ppf(1 - alpha / 2)
    z_b = norm_ppf(power)
    return math.ceil(2.0 * (z_a + z_b) ** 2 * (cov / mde_rel) ** 2)


def empirical_power_two_sample(n_per_group: int, delta_over_sigma: float,
                               alpha: float, trials: int, seed: int,
                               use_u: bool = False) -> float:
    """蒙特卡洛功效:真效应为 ``delta_over_sigma``·σ 时,被判显著的比例。"""
    rng = random.Random(seed)
    z_a = norm_ppf(1 - alpha / 2)
    hits = 0
    for _ in range(trials):
        xs = [rng.gauss(0.0, 1.0) for _ in range(n_per_group)]
        ys = [rng.gauss(delta_over_sigma, 1.0) for _ in range(n_per_group)]
        if use_u:
            hits += 1 if mannwhitney_u_p(xs, ys) < alpha else 0
        else:
            _, p, _ = welch_t(xs, ys)
            hits += 1 if p < alpha else 0
    _ = z_a
    return hits / trials


# --------------------------------------------------------------------------- 自检

def _self_test() -> None:
    # 1) NIST §7.2.2.2 原例一:单侧 α=0.05、β=0.10、δ=σ、σ 已知
    nist_z = n_one_sample_z(0.05, 0.90, 1.0, two_sided=False)
    assert 8.55 < nist_z < 8.58, nist_z          # 原文打印 8.567(用四舍五入的 z 值)
    assert math.ceil(nist_z) == 9
    print(f"[1] NIST 单侧原例 N=(1.645+1.282)²={nist_z:.4f} -> ceil={math.ceil(nist_z)}")

    # 2) NIST §7.2.2.2 原例二:σ 未知,用 df=N-1 迭代一次
    nist_t = n_one_sample_t_iterative(0.05, 0.90, 1.0, two_sided=False)
    assert nist_t == 11, nist_t                   # 原文:N=9 偏低 -> df=8 -> 10.6 -> 11
    df8 = t_ppf(0.95, 8) + t_ppf(0.90, 8)
    assert 10.55 < df8 ** 2 < 10.65, df8 ** 2     # 原文打印 10.6
    print(f"[2] NIST 迭代:df=8 时 (t+t)²={df8**2:.4f} -> N={nist_t}(一次迭代即收敛)")

    # 3) 单样本 -> 两组的 ×2 关系,由蒙特卡洛验证而不是硬背
    d = 1.0
    n1 = n_one_sample_z(ALPHA, POWER, 1.0 / d)
    n2 = n_two_sample_z(ALPHA, POWER, 1.0 / d)
    assert abs(n2 - 2 * n1) < 1e-9
    assert math.ceil(n2) == 16, n2
    pw = empirical_power_two_sample(16, d, ALPHA, trials=4000, seed=20260919)
    assert 0.76 < pw < 0.85, pw                   # 理论值 0.807
    print(f"[3] δ=σ:单样本 n={n1:.2f},两组每侧 n={n2:.2f}(恰 2 倍);"
          f"n=16 的蒙特卡洛功效 {pw:.3f}(理论 0.807)")

    # 4) MDE 与 n 互为逆运算(闭环保验)
    cov, mde = 0.01, 0.005
    need_exact = 2.0 * (norm_ppf(1 - ALPHA / 2) + norm_ppf(POWER)) ** 2 * (cov / mde) ** 2
    n_need = math.ceil(need_exact)
    back = mde_rel_from_n(ALPHA, POWER, cov, n_need)
    # 向上取整意味着实际达到的 MDE 只会**优于**目标,不可能恰好相等 —— 断言方向而非等值
    assert back <= mde + 1e-12, (n_need, back)
    assert back > mde * (1 - 1.0 / n_need), (n_need, back)
    assert mde_rel_from_n(ALPHA, POWER, cov, need_exact) == mde or \
        abs(mde_rel_from_n(ALPHA, POWER, cov, need_exact) - mde) < 1e-9
    print(f"[4] CoV=1%、MDE=0.5% -> 精确解 {need_exact:.2f} 次 -> 取整 {n_need} 次;"
          f"反解回 MDE={back:.6f}(≤ 目标,取整的必然方向)")

    # 5) 样本量与效应平方成反比:效应减半,样本量 ×4
    n_full = n_from_cov_mde(ALPHA, POWER, 0.02, 0.02)
    n_half = n_from_cov_mde(ALPHA, POWER, 0.02, 0.01)
    assert n_half >= 3.5 * n_full, (n_full, n_half)
    # σ/δ 从 1 翻到 2(即目标效应 δ 减半) -> 所需样本量 ×4
    ratio = n_two_sample_z(ALPHA, POWER, 2.0) / n_two_sample_z(ALPHA, POWER, 1.0)
    assert abs(ratio - 4.0) < 1e-9, ratio
    print(f"[5] MDE 减半 -> 样本量 ×{ratio:.3f}(CoV=2% 时 {n_full} -> {n_half})")

    # 6) benchstat 的"至少 10 次、理想 20 次"在这个方程里对应多大的 MDE
    m10 = mde_rel_from_n(ALPHA, POWER, 0.01, 10)
    m20 = mde_rel_from_n(ALPHA, POWER, 0.01, 20)
    assert abs(m10 / m20 - math.sqrt(2.0)) < 1e-9
    assert abs(m10 - 0.012533) < 1e-5, m10
    assert abs(m20 - 0.008862) < 1e-5, m20
    print(f"[6] CoV=1% 时:-count=10 -> MDE={m10:.3%};-count=20 -> MDE={m20:.3%}"
          f"(比值 √2)")

    # 7) 噪声才是瓶颈:CoV 每翻一倍,MDE 门槛同步翻一倍
    for c in (0.005, 0.01, 0.02, 0.05):
        assert abs(mde_rel_from_n(ALPHA, POWER, c, 20) / c - 0.8862) < 1e-3
    print("[7] -count=20 时 MDE ≈ 0.886 × CoV:想检出 1% 回归,得先把 CoV 压到 ~1.1% 以下")

    # 8) n=1 的死穴:自由度 0,t 分位数根本不存在
    try:
        t_ppf(0.975, 0)
        raise AssertionError("df=0 应当报错")
    except ValueError:
        pass
    print("[8] 每侧 1 个样本 -> Welch 自由度 0 -> t 分位数无定义(不是不精确,是无解)")

    # 9) 非参数检验要付一点功效代价(benchstat 默认走 U 检验)
    pw_t = empirical_power_two_sample(16, 1.0, ALPHA, trials=4000, seed=20260919)
    pw_u = empirical_power_two_sample(16, 1.0, ALPHA, trials=4000, seed=20260919, use_u=True)
    assert 0.70 < pw_u < 0.85, pw_u
    assert abs(pw_u - pw_t) < 0.08, (pw_t, pw_u)
    print(f"[9] n=16 时 Welch t 功效 {pw_t:.3f} vs Mann-Whitney U 功效 {pw_u:.3f}"
          f"(非参数略低,故公式给的 n 应视为下界)")


if __name__ == "__main__":
    _self_test()
    print("\nmde_sample: 全部自检通过")
