#!/usr/bin/env python3
"""变异系数(CoV)、噪声地板与降噪:为什么"先降噪,再谈判据"。

三条来自工程一线的权威事实被程序化核对:

1. **Dropbox Apogee** 的原始结论 —— 官方博客原文:
   "we were able to get the variability down **from 60%** in the worst case
   **to less than 5%** for all of our tests. This means we can confidently detect
   5% or greater regressions." 并且 "We were able to improve this further by
   rejecting one outlier per five runs of a test."
   本 demo 用 [MDE与样本量估算/](../MDE与样本量估算/) 的公式核对这句话:**在 α=0.05、
   power=0.8 下,CoV=5% 要检出 5% 的回归,每侧需要 16 次而不是"想当然的几次"**;
   若每侧只有 benchstat 建议的 10 次,MDE 是 6.26% > 5%。官方那句话成立的前提是
   样本量够——这是口径差异,记录下来但不替它改正。

2. **离群点剔除会引入方向性偏差** —— Apogee "rejecting one outlier per five runs"
   剔的是**最差的那次**,这必然让均值变好(偏低)。对正态样本,每 5 个里丢掉最大值,
   理论偏差 = -E[max(Z₁..Z₅)]·σ/5/0.8 = -0.2908σ,**且不随样本量增大而消失**
   (它是 O(σ) 而不是 O(σ/√n))。程序化实测与理论值对齐。

3. **交错运行 vs 顺序运行** —— benchstat Tips 原文:
   "The best way to do this is to interleave before and after runs, rather than running,
   say, 10 iterations of the before benchmark, and then 10 iterations of the after
   benchmark." 在存在线性环境漂移(升温/降频)时,顺序跑把漂移整体算进回归,
   交错跑只剩一次间隔的漂移 —— 二者相差恰好 **n 倍**,本 demo 精确断言这个倍数。

无第三方依赖;固定种子保证自检可复现。
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist

ALPHA = 0.05
POWER = 0.80


def cov(xs) -> float:
    """变异系数:样本标准差 / 均值。无量纲,故可跨基准比较。"""
    n = len(xs)
    if n < 2:
        raise ValueError("CoV 至少需要 2 个样本")
    m = sum(xs) / n
    s = math.sqrt(sum((v - m) ** 2 for v in xs) / (n - 1))
    return s / m


def mde_rel_from_n(cov_value: float, n_per_group: int,
                   alpha: float = ALPHA, power: float = POWER) -> float:
    """给定 CoV 与每侧样本量,能检出的最小相对回归(见 MDE 那个 demo 的推导)。"""
    z_a = NormalDist().inv_cdf(1 - alpha / 2)
    z_b = NormalDist().inv_cdf(power)
    return (z_a + z_b) * cov_value * math.sqrt(2.0 / n_per_group)


def n_from_cov_mde(cov_value: float, mde_rel: float,
                   alpha: float = ALPHA, power: float = POWER) -> int:
    z_a = NormalDist().inv_cdf(1 - alpha / 2)
    z_b = NormalDist().inv_cdf(power)
    return math.ceil(2.0 * (z_a + z_b) ** 2 * (cov_value / mde_rel) ** 2)


def sequential_ab_delta(base: float, drift: float, n: int) -> float:
    """先跑 n 次 A 再跑 n 次 B(线性漂移 drift/次)时,均值差的**漂移分量**。

    A 在时刻 0..n-1、B 在 n..2n-1,故 E[mean_B - mean_A] = drift·n。
    """
    mean_a = base + drift * (n - 1) / 2.0
    mean_b = base + drift * (3 * n - 1) / 2.0
    return mean_b - mean_a


def interleaved_ab_delta(base: float, drift: float, n: int) -> float:
    """A/B 交替(时刻 2i 与 2i+1)时,均值差的漂移分量 = drift·1。"""
    mean_a = base + drift * (2 * (n - 1) / 2.0)
    mean_b = base + drift * (2 * (n - 1) / 2.0 + 1)
    return mean_b - mean_a


def trim_max_per_block(xs, k: int = 5):
    """Apogee 式剔除:每 k 次里丢掉最差(最大)的那次。返回(保留值, 剔除个数)。

    注意这里丢的是**最大值**,不是"离中位数最远的那个",所以它是单向的。
    """
    kept = []
    dropped = 0
    for i in range(0, len(xs) - k + 1, k):
        block = list(xs[i:i + k])
        block.remove(max(block))      # 只丢一个最大值(块内有并列时也只丢一个)
        kept.extend(block)
        dropped += 1
    return kept, dropped



def aa_noise_floor(mu: float, sigma: float, n_per_group: int,
                   trials: int, seed: int) -> float:
    """A/A 噪声地板:同一份代码重复跑,「观测到的相对差异」的典型幅度。

    理论上 mean_B - mean_A ~ N(0, σ√(2/n)),故 E[|Δ|]/μ = CoV·√(2/n)·√(2/π)。
    这个数就是「不改动任何代码时,你以为看到的回归通常有多大」。
    """
    rng = random.Random(seed)
    total = 0.0
    for _ in range(trials):
        a = [rng.gauss(mu, sigma) for _ in range(n_per_group)]
        b = [rng.gauss(mu, sigma) for _ in range(n_per_group)]
        total += abs(sum(b) / n_per_group - sum(a) / n_per_group)
    return total / trials / mu


# --------------------------------------------------------------------------- 自检

def _self_test() -> None:
    # 1) CoV 无量纲:整体缩放不改变它
    rng = random.Random(11)
    base = [100.0 + rng.gauss(0, 4.0) for _ in range(200)]
    scaled = [v * 7.3 for v in base]
    assert abs(cov(base) - cov(scaled)) < 1e-12
    print(f"[1] CoV 无量纲:缩放 7.3 倍后 {cov(base):.6f} -> {cov(scaled):.6f}")

    # 2) 交错 vs 顺序:漂移被放大的倍数恰好是 n
    for n in (5, 10, 20):
        seq = sequential_ab_delta(100.0, 0.01, n)
        itl = interleaved_ab_delta(100.0, 0.01, n)
        assert abs(seq - 0.01 * n) < 1e-9, (n, seq)
        assert abs(itl - 0.01) < 1e-9, (n, itl)
        assert abs(seq / itl - n) < 1e-9
    print("[2] 线性漂移下:顺序跑偏差 = drift×n,交错跑 = drift×1,比值恰为 n")

    # 3) 核对 Apogee 的「CoV<5% => 能检出 5% 回归」
    m10 = mde_rel_from_n(0.05, 10)
    m16 = mde_rel_from_n(0.05, 16)
    assert m10 > 0.05, m10                      # 每侧 10 次时其实检不出 5%
    assert m16 <= 0.05, m16                     # 每侧 16 次才够
    assert n_from_cov_mde(0.05, 0.05) == 16
    print(f"[3] Apogee 口径核对:CoV=5% 时 -count=10 -> MDE={m10:.2%}(>5%),"
          f"-count=16 -> MDE={m16:.2%}(≤5%,原话成立)")

    # 4) 降噪与加样本可互换,但代价曲线不同
    target = 0.0313
    n_more = n_from_cov_mde(0.05, target)
    assert abs(mde_rel_from_n(0.025, 10) - target) < 5e-4
    print(f"[4] MDE 从 6.26% 降到 3.13%:要么每侧 {n_more} 次,"
          f"要么把 CoV 从 5% 压到 2.5%(样本量不变)")

    # 5) A/A 噪声地板 = CoV·√(2/n)·√(2/π)
    mu, sigma, n = 100.0, 2.0, 20
    theory = (sigma / mu) * math.sqrt(2.0 / n) * math.sqrt(2.0 / math.pi)
    measured = aa_noise_floor(mu, sigma, n, trials=4000, seed=4242)
    assert abs(measured / theory - 1.0) < 0.08, (measured, theory)
    print(f"[5] A/A 噪声地板:实测 |Δ|/μ 均值 {measured:.4%},理论 {theory:.4%}"
          f"(这就是·什么都没改·时你以为看到的回归幅度)")

    # 6) 剔除离群点是**有偏**的:每 5 个丢最大值的理论偏差 = -E[max(Z₁..Z₅)]σ/5/0.8
    e_max5 = 1.162964           # E[max of 5 iid N(0,1)]
    theory_bias = -e_max5 / 5.0 / 0.8
    for size in (2000, 20000):
        # 发生器必须在循环外建一次:写在推导式里会每次都从同一种子重开,得到 n 个相同值
        gen = random.Random(7)
        xs = [100.0 + gen.gauss(0, 5.0) for _ in range(size)]
        kept, dropped = trim_max_per_block(xs, 5)
        assert dropped == size // 5
        bias_sigma = (sum(kept) / len(kept) - 100.0) / 5.0
        assert bias_sigma < 0, bias_sigma                    # 方向:必然偏低
        assert abs(bias_sigma - theory_bias) / abs(theory_bias) < 0.20, bias_sigma
        print(f"      n={size}: 剔除后均值偏差 {bias_sigma:+.4f}σ"
              f"(理论 {theory_bias:+.4f}σ),CoV {cov(xs):.4f} -> {cov(kept):.4f}")
    print("[6] 剔除的方向性偏差是 O(σ) 而非 O(σ/√n):样本量翻 10 倍也不消失")

    # 7) 重尾下剔除"降噪"效果更大,但偏差也更大
    rng2 = random.Random(99)
    heavy = [math.exp(rng2.gauss(math.log(100.0), 0.5)) for _ in range(5000)]
    kept_h, _ = trim_max_per_block(heavy, 5)
    assert sum(kept_h) / len(kept_h) < sum(heavy) / len(heavy)
    assert cov(kept_h) < cov(heavy)
    red_heavy = 1 - cov(kept_h) / cov(heavy)
    print(f"[7] 对数正态(CoV≈{cov(heavy):.3f})剔除后 CoV 降 {red_heavy:.1%},"
          f"均值同样被拉低 —— 降噪与偏差是同一枚硬币的两面")

    # 8) 把 60% 打到 5% 需要多少样本量才能"不降噪就达标"
    n_60 = n_from_cov_mde(0.60, 0.05)
    assert n_60 > 1000
    print(f"[8] 不降噪(CoV=60%)想检出 5% 回归需要每侧 {n_60} 次 —— "
          f"工程上不可行,这正是 Apogee 先花大力气降噪的原因")


if __name__ == "__main__":
    _self_test()
    print("\ncov_noise: 全部自检通过")
