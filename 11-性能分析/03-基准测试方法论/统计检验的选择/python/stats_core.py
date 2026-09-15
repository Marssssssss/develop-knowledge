#!/usr/bin/env python3
"""统计检验的原语(被 test_selection.py 复用)。

包含: t 分布(不完全 Beta) -> t 检验 / Welch 检验 / Mann-Whitney U /
中位数与均值的置信区间 / NIST 定义的位置估计量。

锚点(自检里逐个断言):
  * df=10 的双尾 0.05 临界值 = 2.228139(教科书值)
  * NIST/SEMATECH 手册 7.3.1 的 Welch 算例: t=2.2694, ν≈15.5
  * 大自由度下 t 分布退化到正态(t=1.96 -> p≈0.05)
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence, Tuple

ALPHA = 0.05


def _betacf(a: float, b: float, x: float, itmax: int = 200, eps: float = 3e-16) -> float:
    """连分式(Lentz 算法), 供正则化不完全 Beta 函数使用。"""
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < 1e-30:
        d = 1e-30
    d = 1.0 / d
    h = d
    for m in range(1, itmax + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 1e-30:
            d = 1e-30
        c = 1.0 + aa / c
        if abs(c) < 1e-30:
            c = 1e-30
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def betainc_reg(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a,b)。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    front = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_two_sided_p(t: float, df: float) -> float:
    """双尾 p 值: P(|T| > |t|) = I_{df/(df+t^2)}(df/2, 1/2)。"""
    return betainc_reg(df / 2.0, 0.5, df / (df + t * t))


def t_critical(df: float, alpha: float = ALPHA) -> float:
    """双尾临界值(二分法反解)。"""
    lo, hi = 0.0, 1000.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if t_two_sided_p(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def welch_t(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float, float]:
    """Welch 不等方差 t 检验 -> (t, df, p); df 用 Welch-Satterthwaite 近似。"""
    n1, n2 = len(x), len(y)
    v1, v2 = statistics.variance(x) / n1, statistics.variance(y) / n2
    t = (statistics.fmean(x) - statistics.fmean(y)) / math.sqrt(v1 + v2)
    df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
    return t, df, t_two_sided_p(t, df)


def student_t(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float, float]:
    """等方差(pooled)Student t 检验 -> (t, df, p)。"""
    n1, n2 = len(x), len(y)
    sp2 = ((n1 - 1) * statistics.variance(x) + (n2 - 1) * statistics.variance(y)) / (n1 + n2 - 2)
    t = (statistics.fmean(x) - statistics.fmean(y)) / math.sqrt(sp2 * (1 / n1 + 1 / n2))
    return t, n1 + n2 - 2, t_two_sided_p(t, n1 + n2 - 2)


def mann_whitney_p(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float]:
    """Mann-Whitney U(双侧, 正态近似 + 并列秩校正 + 连续性校正)-> (U1, p)。

    H0 是"两组同分布 / 无随机优势", 而不是"两组均值相等"。
    """
    n1, n2 = len(x), len(y)
    merged = sorted([(v, 1) for v in x] + [(v, 2) for v in y])
    r1, ties = 0.0, []
    i = 0
    while i < len(merged):
        j = i
        while j + 1 < len(merged) and merged[j + 1][0] == merged[i][0]:
            j += 1
        avg = (i + 1 + j + 1) / 2
        for k in range(i, j + 1):
            if merged[k][1] == 1:
                r1 += avg
        ties.append(j - i + 1)
        i = j + 1
    u1 = r1 - n1 * (n1 + 1) / 2
    n = n1 + n2
    tie_corr = sum(v ** 3 - v for v in ties)
    sigma2 = n1 * n2 * ((n + 1) - tie_corr / (n * (n - 1))) / 12
    if sigma2 <= 0:
        return u1, 1.0
    numer = u1 - n1 * n2 / 2
    numer -= math.copysign(0.5, numer)
    z = numer / math.sqrt(sigma2)
    phi = 0.5 * (1 + math.erf(z / math.sqrt(2)))
    return u1, min(1.0, 2 * min(phi, 1 - phi))


def trimmed_mean(x: Sequence[float], prop: float = 0.05) -> float:
    """截尾均值: 两端各截掉 prop 比例后取均值(NIST 定义)。"""
    xs = sorted(x)
    k = int(len(xs) * prop)
    return statistics.fmean(xs[k:len(xs) - k])


def winsorized_mean(x: Sequence[float], prop: float = 0.05) -> float:
    """Winsorized 均值: 两端越界值改写成边界值后取均值(NIST 定义)。"""
    xs = sorted(x)
    k = int(len(xs) * prop)
    lo, hi = xs[k], xs[len(xs) - 1 - k]
    return statistics.fmean([min(max(v, lo), hi) for v in xs])


def median_ci_order_statistic(x: Sequence[float], alpha: float = ALPHA) -> Tuple[float, float]:
    """分布无关的中位数置信区间: 取最大 k 使 P(Bin(n,0.5) <= k-1) <= alpha/2。"""
    n = len(x)
    xs = sorted(x)
    k = 1
    for cand in range(1, n // 2 + 1):
        if sum(math.comb(n, i) for i in range(cand)) / 2 ** n <= alpha / 2:
            k = cand
        else:
            break
    return xs[k - 1], xs[n - k]


def mean_ci_t(x: Sequence[float], alpha: float = ALPHA) -> Tuple[float, float]:
    """均值的 t 置信区间(前提: 近似正态 / 小样本无重尾)。"""
    n = len(x)
    half = t_critical(n - 1, alpha) * statistics.stdev(x) / math.sqrt(n)
    m = statistics.fmean(x)
    return m - half, m + half
