"""Amdahl / Gustafson / USL 三条扩展律的最小可验证实现。

公式与论断口径全部来自实读资料：
  * HPC 101《Limits of Parallelism》（readthedocs）
        Amdahl    : S = 1 / [ (1 − P) + P/N ]          强扩展，上限 1/(1−P)
        Gustafson : S = N − (1 − P)(N − 1)            弱扩展，无上限
        显式警告：同一个 P 在两式里含义不同 —— 这是最常见的混淆点
        数值例：P=0.9, N=100 -> S = 100 − 0.1×99 = 90.1
        强扩展 = 固定问题加处理器；弱扩展 = 问题随处理器同比例放大
  * Cornell CVW《Amdahl's Law》
        T_N = F_s + F_p/N，取 F_s + F_p = 1 得 S = 1/(F_s + F_p/N)，lim S = 1/F_s
        Gustafson 的 scaled speedup 三种等价写法：
            N·F_p + F_s = N + (1−N)F_s = (N−1)F_p + 1
  * UVA RC《Parallel Performance Analysis》
        "要把程序扩展到 ~100 进程，并行比例必须接近 99%"
        弱扩展效率 ε = S/p，p → ∞ 时 ε → 1 − f
  * Gunther《How to Quantify Scalability》
        USL: C(N) = N/[1 + α(N−1) + βN(N−1)]，且 β=0、γ=1 时退化为 Amdahl 定律
"""

from __future__ import annotations

import math


def amdahl(P: float, N: float) -> float:
    """Amdahl 加速比：S = 1 / [(1 − P) + P/N]，P 为可并行比例。"""
    if not 0.0 <= P <= 1.0:
        raise ValueError("P 必须在 [0,1]")
    if N < 1:
        raise ValueError("N 必须 >= 1")
    return 1.0 / ((1.0 - P) + P / N)


def amdahl_serial(serial_fraction: float, N: float) -> float:
    """按**串行比例**书写的 Amdahl：S = 1 / [f + (1 − f)/N]。"""
    if not 0.0 <= serial_fraction <= 1.0:
        raise ValueError("串行比例必须在 [0,1]")
    if N < 1:
        raise ValueError("N 必须 >= 1")
    f = serial_fraction
    return 1.0 / (f + (1.0 - f) / N)


def gustafson(P: float, N: float) -> float:
    """Gustafson 加速比：S = N − (1 − P)(N − 1)，P 为并行机上测得的可并行比例。"""
    if not 0.0 <= P <= 1.0:
        raise ValueError("P 必须在 [0,1]")
    if N < 1:
        raise ValueError("N 必须 >= 1")
    return N - (1.0 - P) * (N - 1.0)


def gustafson_alt(P: float, N: float) -> float:
    """同一式的展开写法：S = 1 + P(N − 1)。

    注意：**不要**写成 `P + N(1−P)` —— 那个式子里的 P 是**串行比例**，
    与本函数的 P（可并行比例）差一个 `1−P` 的替换。这正是 hpc101 显式警告的
    「同一个符号在两式里含义不同」的位置。
    """
    return 1.0 + P * (N - 1.0)


def gustafson_serial(serial_fraction: float, N: float) -> float:
    """按**串行比例**书写的 Gustafson：S = f + N(1 − f)。"""
    f = serial_fraction
    return f + N * (1.0 - f)


def gustafson_cornell_forms(F_s: float, N: float):
    """Cornell 给出的三种等价写法：返回 (N·F_p + F_s, N + (1−N)F_s, (N−1)F_p + 1)。"""
    F_p = 1.0 - F_s
    return N * F_p + F_s, N + (1.0 - N) * F_s, (N - 1.0) * F_p + 1.0


def amdahl_limit(P: float) -> float:
    """Amdahl 天花板 = 1/(1 − P)（P 为可并行比例）。"""
    if P >= 1.0:
        return math.inf
    return 1.0 / (1.0 - P)


def efficiency(S: float, N: float) -> float:
    """效率 ε = S/N。"""
    return S / N


def weak_efficiency_limit(serial_fraction: float) -> float:
    """弱扩展下 p → ∞ 的效率极限 = 1 − f。"""
    return 1.0 - serial_fraction


def usl_capacity(N: float, alpha: float, beta: float = 0.0, gamma: float = 1.0):
    """USL 相对容量：C(N) = γN / [1 + α(N−1) + βN(N−1)]。"""
    d = 1.0 + alpha * (N - 1.0) + beta * N * (N - 1.0)
    if d <= 0:
        raise ValueError("分母非正")
    return gamma * N / d


def amdahl_as_usl(serial_fraction: float, N: float):
    """β=0、γ=1、α=f 的 USL —— 应当与 Amdahl 逐点相等。"""
    return usl_capacity(N, alpha=serial_fraction, beta=0.0, gamma=1.0)


def parallel_fraction_for(target_speedup: float, N: float):
    """要达到 target_speedup，N 个处理器上所需的可并行比例 P（Amdahl 反解）。"""
    if N < 1:
        raise ValueError("N 必须 >= 1")
    if target_speedup <= 0:
        raise ValueError("目标加速比必须为正")
    # S = 1/((1-P) + P/N)  =>  1/S = 1 - P(1 - 1/N)  =>  P = (1 - 1/S)/(1 - 1/N)
    return (1.0 - 1.0 / target_speedup) / (1.0 - 1.0 / N)


def serial_time_share(P: float, N: float) -> float:
    """并行执行时串行部分占 wallclock 的比例 = (1−P)/[(1−P) + P/N]。"""
    s = 1.0 - P
    return s / (s + P / N)
