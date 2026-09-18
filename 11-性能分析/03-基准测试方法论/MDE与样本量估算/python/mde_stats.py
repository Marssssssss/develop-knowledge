#!/usr/bin/env python3
"""样本量估算所需的分布函数(无第三方依赖)。

- 正态分位数直接用标准库 ``statistics.NormalDist().inv_cdf``。
- t 分布用不完全 Beta 函数表示:``P(|T| > t) = I_x(ν/2, 1/2)``,其中 ``x = ν/(ν + t²)``,
  ``I_x`` 用连分式展开(与 Numerical Recipes 的 betacf/betai 同构)。
- t 分位数由 CDF 单调性做二分求解——NIST e-Handbook §7.2.2.2 明确说样本量方程
  "must be solved iteratively",这里把它做成真的迭代而不是查表。
"""

from __future__ import annotations

import math
from statistics import NormalDist

_FPMIN = 1e-300
_EPS = 3e-16
_MAXIT = 300


def norm_ppf(p: float) -> float:
    """标准正态分位数。"""
    return NormalDist().inv_cdf(p)


def norm_sf(z: float) -> float:
    """``P(Z > z)``。"""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _betacf(a: float, b: float, x: float) -> float:
    """连分式展开(Lentz 算法),供 betai 使用。"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < _FPMIN:
        d = _FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, _MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < _FPMIN:
            d = _FPMIN
        c = 1.0 + aa / c
        if abs(c) < _FPMIN:
            c = _FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 ``I_x(a, b)``。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log1p(-x) + a * math.log(x)
    ) * _betacf(b, a, 1.0 - x) / b


def t_sf_two_sided(t: float, df: float) -> float:
    """``P(|T_df| > |t|)``。df <= 0 时无定义(返回 NaN)。"""
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    return betai(df / 2.0, 0.5, x)


def t_cdf(t: float, df: float) -> float:
    """t 分布的累积分布函数。"""
    if df <= 0:
        return float("nan")
    if t >= 0:
        return 1.0 - 0.5 * t_sf_two_sided(t, df)
    return 0.5 * t_sf_two_sided(t, df)


def t_ppf(p: float, df: float) -> float:
    """t 分位数:由 CDF 单调性二分求解(NIST 说的迭代在这里真的做了)。"""
    if df <= 0:
        raise ValueError("t 分布要求自由度 > 0(n=1 时 df=0,无定义)")
    if not 0.0 < p < 1.0:
        raise ValueError("p 必须落在 (0, 1)")
    lo, hi = -1.0e4, 1.0e4
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if t_cdf(mid, df) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def welch_t(xs, ys):
    """Welch t 统计量与其双侧 p 值;自由度用 Welch-Satterthwaite 近似。

    NIST e-Handbook §1.3.5.3 给出的正是这个自由度公式:
    ``ν = (s1²/N1 + s2²/N2)² / [ (s1²/N1)²/(N1-1) + (s2²/N2)²/(N2-1) ]``
    """
    n1, n2 = len(xs), len(ys)
    m1 = sum(xs) / n1
    m2 = sum(ys) / n2
    v1 = sum((v - m1) ** 2 for v in xs) / (n1 - 1)
    v2 = sum((v - m2) ** 2 for v in ys) / (n2 - 1)
    se2 = v1 / n1 + v2 / n2
    if se2 <= 0:
        return 0.0, 1.0, 0.0
    t = (m2 - m1) / math.sqrt(se2)
    num = se2 ** 2
    den = (v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1)
    df = num / den if den > 0 else float("nan")
    p = t_sf_two_sided(t, df) if df == df else 1.0
    return t, p, df


def mannwhitney_u_p(xs, ys):
    """Mann-Whitney U 的双侧 p 值:正态近似 + 并列秩校正 + 连续性校正。

    benchstat 默认 ``assume=nothing``,走的就是非参数的中位数 + U 检验,
    所以判断是否"显著"时用的不是 Welch t。
    """
    n1, n2 = len(xs), len(ys)
    combined = sorted([(v, 0) for v in xs] + [(v, 1) for v in ys])
    ranks = [0.0] * len(combined)
    tie_correction = 0.0
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        avg = (i + j + 2) / 2.0          # 秩从 1 开始
        for k in range(i, j + 1):
            ranks[k] = avg
        t = j - i + 1
        if t > 1:
            tie_correction += t ** 3 - t
        i = j + 1
    r1 = sum(ranks[k] for k in range(len(combined)) if combined[k][1] == 0)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    mu = n1 * n2 / 2.0
    n = n1 + n2
    sigma2 = (n1 * n2 / 12.0) * ((n + 1) - tie_correction / (n * (n - 1)))
    if sigma2 <= 0:
        return 1.0
    z = (abs(u1 - mu) - 0.5) / math.sqrt(sigma2)   # 连续性校正
    return min(1.0, 2.0 * norm_sf(z))
