#!/usr/bin/env python3
"""benchstat 核心统计的最小复现。

复现 golang.org/x/perf/cmd/benchstat 的默认行为:
  * assume=nothing -> 摘要统计量用中位数 + 置信区间, A/B 对比用 Mann-Whitney U 检验(非参数)
  * 显著性阈值 alpha = 0.05(即使两组数据毫无差异, 也期望约 5% 的对比被判成"有差异")
  * 表格最后一行是各列的 geomean(几何平均), 其比例变化 = 各 benchmark 比例变化的几何平均

实现要点(全部对应 README「原理详解」的步骤编号):
  1. 中位数置信区间: 分布无关的次序统计量法 + bootstrap 百分位法(两者交叉验证)
  2. U 统计量: 排序后取平均秩 -> U1 = R1 - n1(n1+1)/2, U2 = n1*n2 - U1
  3. 小样本用精确 U 分布(等价于"从 1..N 取 n1 个元素的子集和"分布),
     大样本用正态近似: sigma_U = sqrt(n1*n2*((N+1) - t/(N*(N-1)))/12), t = Σ(tj^3 - tj)
     并对分子做 0.5 的连续性校正
  4. two-sided 特例: U1 == U2 时 p = 1(离散分布下不能直接 2*CDF, 会把 Usmall 处的
     非无穷小概率质量算两次)

无第三方依赖。锚点数值来自 golang/perf 的 internal/stats/utest.go 与
pkg.go.dev/golang.org/x/perf/cmd/benchstat 的文档(见 README 参考资料)。
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Dict, List, Sequence, Tuple

ALPHA = 0.05

# ---------------------------------------------------------------- 中位数 + CI


def _binom_tail_le(n: int, k: int) -> float:
    """P(Bin(n, 0.5) <= k), 用整数组合数精确求和后再除 2^n。"""
    total = 2 ** n
    acc = sum(math.comb(n, i) for i in range(0, max(k, -1) + 1))
    return acc / total


def median_ci_order_statistic(x: Sequence[float], alpha: float = ALPHA) -> Tuple[float, float, float]:
    """分布无关的中位数置信区间(次序统计量法)。

    取最大的 k 使 P(Bin(n, 0.5) <= k-1) <= alpha/2, 则 [x(k), x(n-k+1)] 覆盖中位数
    的概率 >= 1-alpha。数据有并列时必须向外扩张, 否则离散化会让实际覆盖率掉到名义值以下。
    返回 (下界, 上界, 实际覆盖率下界)。
    """
    n = len(x)
    if n == 0:
        raise ValueError("empty sample")
    xs = sorted(x)
    k = 1
    for cand in range(1, n // 2 + 1):
        if _binom_tail_le(n, cand - 1) <= alpha / 2:
            k = cand
        else:
            break
    lo, hi = k - 1, n - k
    # 并列扩张: 与边界等值的点全部纳入
    while lo > 0 and xs[lo - 1] == xs[lo]:
        lo -= 1
    while hi < n - 1 and xs[hi + 1] == xs[hi]:
        hi += 1
    coverage = 1.0 - 2.0 * _binom_tail_le(n, k - 1)
    return xs[lo], xs[hi], min(coverage, 1.0)


def median_ci_bootstrap(x: Sequence[float], alpha: float = ALPHA, resamples: int = 2000,
                        seed: int = 20260915) -> Tuple[float, float]:
    """bootstrap 百分位法中位数 CI(用固定种子保证可复现)。"""
    rng = random.Random(seed)
    n = len(x)
    meds = []
    for _ in range(resamples):
        sample = [x[rng.randrange(n)] for _ in range(n)]
        meds.append(statistics.median(sample))
    meds.sort()
    lo = meds[int(alpha / 2 * resamples)]
    hi = meds[min(resamples - 1, int((1 - alpha / 2) * resamples))]
    return lo, hi


# ------------------------------------------------------------ Mann-Whitney U


def _average_ranks(x1: Sequence[float], x2: Sequence[float]) -> Tuple[float, List[int]]:
    """合并排序后赋平均秩, 返回 (样本 1 的秩和, 各并列组的规模列表)。"""
    merged = sorted([(v, 1) for v in x1] + [(v, 2) for v in x2])
    r1 = 0.0
    tie_sizes: List[int] = []
    i = 0
    while i < len(merged):
        j = i
        while j + 1 < len(merged) and merged[j + 1][0] == merged[i][0]:
            j += 1
        # 位置 i..j(0 基) 共享平均秩 (i+1 + j+1)/2
        avg_rank = (i + 1 + j + 1) / 2.0
        for t in range(i, j + 1):
            if merged[t][1] == 1:
                r1 += avg_rank
        tie_sizes.append(j - i + 1)
        i = j + 1
    return r1, tie_sizes


def _u_distribution_exact(n1: int, n2: int) -> Dict[int, int]:
    """精确 U 分布: 统计"从 1..N 中取 n1 个元素"的子集和分布。

    令 T1 为样本 1 的秩和, 则 U1 = T1 - n1(n1+1)/2, 故子集和的计数平移后即 U 的计数。
    总组合数 C(N, n1)。仅在无并列时可用(有并列时秩不再是整数)。
    """
    N = n1 + n2
    max_sum = n1 * N
    # dp[k][s] = 取 k 个互异元素(取自 1..N)其和为 s 的方案数
    dp = [[0] * (max_sum + 1) for _ in range(n1 + 1)]
    dp[0][0] = 1
    for v in range(1, N + 1):
        for k in range(min(v, n1), 0, -1):
            row, prev = dp[k], dp[k - 1]
            for s in range(max_sum, v - 1, -1):
                if prev[s - v]:
                    row[s] += prev[s - v]
    offset = n1 * (n1 + 1) // 2
    counts = {s - offset: c for s, c in enumerate(dp[n1]) if c}
    return counts


def mann_whitney_u(x1: Sequence[float], x2: Sequence[float],
                   alpha: float = ALPHA) -> Tuple[float, float, str]:
    """Mann-Whitney U 检验(双侧)。返回 (U1, p, 所用方法)。"""
    n1, n2 = len(x1), len(x2)
    if n1 == 0 or n2 == 0:
        raise ValueError("empty sample")
    r1, tie_sizes = _average_ranks(x1, x2)
    u1 = r1 - n1 * (n1 + 1) / 2.0
    u2 = n1 * n2 - u1
    u_small = min(u1, u2)
    has_ties = any(t > 1 for t in tie_sizes)
    if all(t == 1 for t in tie_sizes) and n1 <= 50 and n2 <= 50 and n1 * n2 <= 2500:
        counts = _u_distribution_exact(n1, n2)
        total = math.comb(n1 + n2, n1)
        assert sum(counts.values()) == total, "精确分布计数应与组合数一致"
        if u1 == u2:
            # 分布关于 u_small 对称且离散: 直接翻倍会重复计入 u_small 处的概率质量
            p = 1.0
        else:
            p = 2.0 * sum(c for u, c in counts.items() if u <= u_small) / total
        method = "exact"
    else:
        t = sum(ts ** 3 - ts for ts in tie_sizes)  # 并列秩校正
        N = n1 + n2
        mu = n1 * n2 / 2.0
        sigma2 = n1 * n2 * ((N + 1) - t / (N * (N - 1))) / 12.0
        if sigma2 <= 0:
            raise ValueError("all values equal: test is meaningless")
        numer = u1 - mu
        numer -= math.copysign(0.5, numer)  # 连续性校正
        z = numer / math.sqrt(sigma2)
        p = 2.0 * min(_normal_cdf(z), 1.0 - _normal_cdf(z))
        method = "normal+tie+continuity"
    return u1, min(1.0, p), method


def _normal_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ------------------------------------------------------------- geomean 汇总


def geomean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("empty")
    return math.exp(sum(math.log(v) for v in values) / len(values))


def geomean_ratio(after: Sequence[float], before: Sequence[float]) -> float:
    """geomean 的比例变化: exp(mean(ln(after_i / before_i)))。

    语义: 若 n 个 benchmark 中只有一个变成 2 倍, 则 geomean 比值 = 2^(1/n)。
    """
    if len(after) != len(before) or not after:
        raise ValueError("size mismatch")
    return math.exp(sum(math.log(a / b) for a, b in zip(after, before)) / len(after))


def benchstat_table(rows: Sequence[Tuple[str, Sequence[float], Sequence[float]]],
                    alpha: float = ALPHA) -> str:
    """生成 benchstat 风格表格; p >= alpha 时差异列显示 '~'。"""
    out = ["benchmark                     base(sec/op)          new(sec/op)      vs base",
           "-" * 84]
    base_med, new_med = [], []
    for name, before, after in rows:
        b, a = statistics.median(before), statistics.median(after)
        base_med.append(b)
        new_med.append(a)
        _, p, method = mann_whitney_u(before, after, alpha)
        if p >= alpha:
            delta = f"~ (p={p:.3f} n={len(before)})"
        else:
            delta = f"{(a - b) / b * 100:+.2f}% (p={p:.3f} n={len(before)}, {method})"
        out.append(f"{name:<28} {b:>12.6g}         {a:>12.6g}     {delta}")
    out.append("-" * 84)
    out.append(f"{'geomean':<28} {geomean(base_med):>12.6g}         "
               f"{geomean(new_med):>12.6g}     {(geomean_ratio(new_med, base_med) - 1) * 100:+.2f}%")
    return "\n".join(out)


# ------------------------------------------------------------------- 自检


def _synth(kind: str, n: int, rng: random.Random) -> List[float]:
    if kind == "normal":
        return [rng.gauss(100.0, 8.0) for _ in range(n)]
    if kind == "lognormal":
        return [math.exp(rng.gauss(4.6, 0.35)) for _ in range(n)]
    if kind == "exponential":
        return [rng.expovariate(1 / 100.0) for _ in range(n)]
    raise ValueError(kind)


def _self_test() -> None:
    rng = random.Random(42)

    # 1) 精确分布的总计数必须等于组合数
    counts = _u_distribution_exact(4, 4)
    assert sum(counts.values()) == math.comb(8, 4) == 70, counts
    assert min(counts) == 0 and max(counts) == 16, (min(counts), max(counts))

    # 2) U 统计量的边界与镜像关系
    a = [1.0, 2.0, 3.0, 4.0]
    b = [10.0, 20.0, 30.0, 40.0]
    u1, p, method = mann_whitney_u(a, b)
    assert (u1, p) == (0.0, 2 / 70), (u1, p)
    u1r, _, _ = mann_whitney_u(b, a)
    assert u1r == 16.0, u1r  # U1 + U2 = n1*n2

    # 3) 精确路径与正态近似路径的一致性(同一组数据强制两条路径)
    x = [rng.gauss(0, 1) for _ in range(12)]
    y = [rng.gauss(0.9, 1) for _ in range(12)]
    p_exact = mann_whitney_u(x, y)[1]
    counts = _u_distribution_exact(12, 12)
    total = math.comb(24, 12)
    r1, ties = _average_ranks(x, y)
    u1 = r1 - 12 * 13 / 2
    p_norm_sigma = math.sqrt(12 * 12 * ((24 + 1) - 0) / 12)
    numer = u1 - 72 - math.copysign(0.5, u1 - 72)
    p_norm = 2 * min(_normal_cdf(numer / p_norm_sigma), 1 - _normal_cdf(numer / p_norm_sigma))
    assert abs(p_exact - p_norm) < 0.02, (p_exact, p_norm)

    # 4) 中位数 CI: 次序统计量法覆盖率 >= 名义值, bootstrap 与之量级相当
    cov_hits = bs_narrower = 0
    trials = 120
    for _ in range(trials):
        s = _synth("exponential", 21, rng)
        lo, hi, cov = median_ci_order_statistic(s)
        blo, bhi = median_ci_bootstrap(s, resamples=400, seed=7)
        true_med = math.log(2) * 100.0  # Exp(1/100) 的中位数
        cov_hits += 1 if lo <= true_med <= hi else 0
        bs_narrower += 1 if (bhi - blo) <= (hi - lo) else 0
    coverage = cov_hits / trials
    assert coverage >= 0.85, coverage  # 次序统计量法保守, 覆盖率不应低于名义 0.95 太多
    print(f"[CI] 次序统计量法覆盖率 {coverage:.3f}(保守), "
          f"bootstrap 更窄的比例 {bs_narrower / trials:.3f}")

    # 5) alpha 标定: 同分布两组互比, 假阳性率应接近 0.05
    trials, hits = 400, 0
    for _ in range(trials):
        s1 = _synth("lognormal", 8, rng)
        s2 = _synth("lognormal", 8, rng)
        hits += 1 if mann_whitney_u(s1, s2)[1] < ALPHA else 0
    fp = hits / trials
    assert 0.01 <= fp <= 0.10, fp
    print(f"[alpha] 400 组同分布对比的假阳性率 {fp:.3f}(名义 0.05)")

    # 6) 真差异必须被检出
    s1 = _synth("normal", 20, rng)
    s2 = [v * 1.25 for v in s1]
    _, p_diff, _ = mann_whitney_u(s1, s2)
    assert p_diff < 1e-4, p_diff

    # 7) geomean 比例语义: 8 个 benchmark 中一个 2 倍 -> geomean = 2^(1/8)
    before = [1.0] * 8
    after = [2.0] + [1.0] * 7
    assert abs(geomean_ratio(after, before) - 2 ** (1 / 8)) < 1e-12

    # 8) 表格输出: 无差异显示 '~'
    base = _synth("normal", 10, rng)
    same = _synth("normal", 10, rng)
    table = benchstat_table([("Bench-8", base, base), ("Noise-8", base, same)])
    assert "~ (p=" in table
    print(table)


if __name__ == "__main__":
    _self_test()
    print("\nbenchstat_lite: 全部自检通过")
