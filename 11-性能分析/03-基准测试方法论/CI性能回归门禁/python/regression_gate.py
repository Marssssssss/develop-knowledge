#!/usr/bin/env python3
"""CI 性能回归门禁: 从"两次构建数字比大小"升级为"趋势 + 双判据 + 多重比较校正"。

三个来源的机制被逐一复现:

1. **Skia/Jetpack 的分步拟合(step fitting)** —— 不再比较 build N 与 N-1, 而是在整条
   结果序列里找"阶跃"。判定要求同时满足:
     相对变化 |delta| >= THRESHOLD(官方用 25) 且 z = delta/stderr 超过 2.0
   其中 stderr = sqrt(var(before)/n_before + var(after)/n_after), 窗口宽度 WIDTH = 5;
   "方差越小, 越有信心检出细微回归"。单点尖峰不算回归 —— 需要有持续的趋势。
2. **幅度 + 显著性双判据**(双判据同时满足才判 FAIL): 幅度阈值(默认 10%) + Welch t 检验
   p < 0.05; 自由度用 Welch-Satterthwaite 近似, df > 30 退化为正态临界值 z=1.96,
   小样本用 t 临界值表; 缺失标准差时按 5% 变异系数(CoV)兜底。
3. **降噪优先于检测**(Apogee 的经验): 先证明同一份代码重复跑的 CoV 足够低, 再谈回归检测;
   否则测量噪声会盖过代码改动的影响。本 demo 用"历史噪声地板"把幅度阈值抬到 3σ。

另附多重比较校正(Bonferroni / Benjamini-Hochberg)与"把改进也当变更告警"的处理。

无第三方依赖; 用固定种子保证自检可复现。
"""

from __future__ import annotations

import math
import random
import statistics
from typing import List, Sequence, Tuple

ALPHA = 0.05
MAG_THRESHOLD = 0.10   # 双判据的幅度阈值(10%)
STEP_WIDTH = 5         # step fit 的窗口宽度
STEP_THRESHOLD = 0.25  # step fit 的相对变化阈值(官方用 25)
STEP_Z = 2.0           # step fit 的 z 门槛

# 双尾 0.05 的 t 临界值表(df 1..30); df > 30 用正态 1.96(Picasso 同款简化)
_T_TABLE = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
            2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
            2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t_critical(df: float) -> float:
    """df > 30 -> 1.96(正态); 否则查表并对小数自由度线性插值。"""
    if df > 30:
        return 1.96
    if df <= 1:
        return _T_TABLE[0]
    lo = int(math.floor(df))
    frac = df - lo
    a, b = _T_TABLE[lo - 1], _T_TABLE[min(lo, len(_T_TABLE) - 1)]
    return a + (b - a) * frac


# ------------------------------------------------------- 1. 分步拟合(step fit)


def step_fit(series: Sequence[float], width: int = STEP_WIDTH,
             threshold: float = STEP_THRESHOLD, z_min: float = STEP_Z) -> List[dict]:
    """在结果序列里找阶跃: 窗口前 mean/var 与窗口后 mean/var 比较。

    - 相对变化 delta = (mean_after - mean_before) / mean_before 与 THRESHOLD 比
    - 显著性 z = (mean_after - mean_before) / stderr 与 2.0 比
      (注意: 网上流传的伪码把"相对 delta"直接除以"绝对 stderr", 量纲不一致;
       本 demo 用绝对差值除以 stderr, 这是统计上正确的标准化)
    - stderr = sqrt(var_before/n_before + var_after/n_after)

    返回候选列表 [{index, delta, z}]。相邻候选会合并(只保留第一个), 避免一次阶跃报多次。
    """
    out: List[dict] = []
    n = len(series)
    for i in range(width, n - width + 1):
        before = list(series[i - width:i])
        after = list(series[i:i + width])
        mb, ma = statistics.fmean(before), statistics.fmean(after)
        if mb == 0:
            continue
        delta = (ma - mb) / mb
        vb, va = statistics.variance(before), statistics.variance(after)
        stderr = math.sqrt(vb / len(before) + va / len(after))
        if stderr == 0:
            z = math.inf if ma != mb else 0.0
        else:
            z = (ma - mb) / stderr
        if abs(delta) >= threshold and abs(z) > z_min:
            out.append({"index": i, "delta": delta, "z": z})
    merged: List[dict] = []
    for cand in out:
        if merged and cand["index"] - merged[-1]["index"] < width:
            continue
        merged.append(cand)
    return merged


def naive_delta(series: Sequence[float]) -> List[Tuple[int, float]]:
    """反例: "与前一次构建比大小"。返回 [(index, delta)] —— 噪声下假阳性极多。"""
    return [(i, (series[i] - series[i - 1]) / series[i - 1])
            for i in range(1, len(series)) if series[i - 1] != 0]


# ------------------------------------------------- 2. 双判据门禁(幅度+显著性)


def welch_verdict(baseline: Sequence[float], current: Sequence[float],
                  mag_threshold: float = MAG_THRESHOLD, alpha: float = ALPHA,
                  assume_cv: float = 0.05) -> dict:
    """幅度阈值 + Welch t 显著性的双判据; 缺标准差时用 CoV 兜底估计。"""
    nb, nc = len(baseline), len(current)
    mb, mc = statistics.fmean(baseline), statistics.fmean(current)
    delta = (mc - mb) / mb if mb else float("nan")
    sb = statistics.stdev(baseline) if nb > 1 else 0.0
    sc = statistics.stdev(current) if nc > 1 else 0.0
    if sb == 0:                      # 历史基线可能只存了均值 -> 按 5% CoV 假设
        sb = abs(mb) * assume_cv
    if sc == 0:
        sc = abs(mc) * assume_cv
    v1, v2 = sb ** 2 / nb, sc ** 2 / nc
    if v1 + v2 == 0:
        return {"delta": 0.0, "p_rejected": False, "verdict": "identical",
                "t": 0.0, "df": 0.0}
    t = (mc - mb) / math.sqrt(v1 + v2)
    if nb > 1 and nc > 1:
        df = (v1 + v2) ** 2 / (v1 ** 2 / (nb - 1) + v2 ** 2 / (nc - 1))
    else:
        df = 1.0
    significant = abs(t) > t_critical(df)
    if not significant:
        verdict = "no_significant_change"
    elif abs(delta) < mag_threshold:
        verdict = "acceptable_change"
    elif delta < 0:
        verdict = "improvement"
    else:
        verdict = "REGRESSION"
    return {"delta": delta, "t": t, "df": df, "p_rejected": significant, "verdict": verdict}


def noise_floor(history: Sequence[float], k: float = 3.0,
                floor: float = MAG_THRESHOLD) -> float:
    """自适应幅度阈值: max(固定下限, k 倍历史变异系数) —— 噪声大的指标自动放宽。"""
    if len(history) < 2:
        return floor
    cv = statistics.stdev(history) / abs(statistics.fmean(history))
    return max(floor, k * cv)


def cov_gate(series: Sequence[float], limit: float = 0.02) -> Tuple[bool, float]:
    """可检测性前置条件: 同一份代码重复跑的 CoV 必须低于 limit, 否则先降噪。"""
    cv = statistics.stdev(series) / abs(statistics.fmean(series))
    return cv <= limit, cv


# ------------------------------------------------------------ 多重比较校正


def bonferroni(pvalues: Sequence[float], alpha: float = ALPHA) -> List[bool]:
    m = len(pvalues)
    return [p < alpha / m for p in pvalues]


def benjamini_hochberg(pvalues: Sequence[float], alpha: float = ALPHA) -> List[bool]:
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    out = [False] * m
    kmax = -1
    for rank, idx in enumerate(order, start=1):
        if pvalues[idx] <= alpha * rank / m:
            kmax = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= kmax:
            out[idx] = True
    return out


# ------------------------------------------------------------------- 自检


def _series_with_step(rng: random.Random, n: int, base: float, noise: float,
                      step_at: int, step: float) -> List[float]:
    return [base * (1 + step if i >= step_at else 1.0) + rng.gauss(0, noise)
            for i in range(n)]


def _self_test() -> None:
    rng = random.Random(20260915)

    # 1) t 临界值表: df=10 -> 2.228, df=30 -> 2.042, df=100 -> 1.96
    assert abs(t_critical(10) - 2.228) < 1e-3
    assert abs(t_critical(30) - 2.042) < 1e-3
    assert t_critical(100) == 1.96
    print(f"[临界值] df=10 -> {t_critical(10):.3f}, df=30 -> {t_critical(30):.3f}, "
          f"df=100 -> {t_critical(100):.2f}")

    # 2) "与上一次构建比大小"在纯噪声上的假阳性率
    noise_only = _series_with_step(rng, 60, 100.0, 6.0, 0, 0.0)
    naive_hits = sum(1 for _, d in naive_delta(noise_only) if abs(d) >= MAG_THRESHOLD)
    step_hits = step_fit(noise_only)
    assert naive_hits > 0, "纯噪声下朴素差分应当出现假阳性"
    assert len(step_hits) == 0, step_hits
    print(f"[对照] 纯噪声 60 个构建: 朴素差分 {len(naive_delta(noise_only))} 次比较中 "
          f"{naive_hits} 次 >=10%; step fit 报出 {len(step_hits)} 个阶跃")

    # 3) 真实阶跃必须被 step fit 定位到正确的构建号
    with_step = _series_with_step(rng, 60, 100.0, 6.0, 35, 0.30)
    found = step_fit(with_step)
    assert len(found) == 1 and abs(found[0]["index"] - 35) <= 1, found
    print(f"[检出] 第 35 个构建注入 +30% 阶跃 -> 定位 index={found[0]['index']}, "
          f"delta={found[0]['delta']:+.1%}, z={found[0]['z']:.1f}")

    # 4) 单点尖峰不是回归: 只抬高一个构建, step fit 不应报(除非窗口均值变化够大)
    spike = _series_with_step(rng, 60, 100.0, 6.0, 0, 0.0)
    spike[30] = 100.0 * 1.8
    spikes = step_fit(spike)
    assert len(spikes) == 0, spikes
    print(f"[抗尖峰] 单个构建抬高 80%: step fit 报出 {len(spikes)} 个阶跃(正确忽略)")

    # 5) 双判据: 幅度够大但方差极大 -> 不判回归; 幅度大且极稳 -> 判回归
    best = [100.0 + rng.gauss(0, 0.05) for _ in range(30)]
    noisy_worse = [120.0 + rng.gauss(0, 60.0) for _ in range(30)]
    stable_worse = [120.0 + rng.gauss(0, 0.05) for _ in range(30)]
    v_noisy = welch_verdict(best, noisy_worse)
    v_stable = welch_verdict(best, stable_worse)
    assert v_noisy["verdict"] != "REGRESSION", v_noisy
    assert v_stable["verdict"] == "REGRESSION", v_stable
    print(f"[双判据] +20% 但 σ=60 -> {v_noisy['verdict']}(t={v_noisy['t']:.2f}, "
          f"临界值 {t_critical(v_noisy['df']):.2f}); +20% 且 σ=0.05 -> "
          f"{v_stable['verdict']}(t={v_stable['t']:.1f})")

    # 6) 幅度不足: 显著但不达 10% -> acceptable_change
    small = [100.4 + rng.gauss(0, 0.05) for _ in range(30)]
    assert welch_verdict(best, small)["verdict"] == "acceptable_change"
    # 变快也是变更(改进), 一样要出现在看板上(幅度同样要够 10%)
    v_fast = welch_verdict(best, [80.0 + rng.gauss(0, 0.05) for _ in range(30)])
    assert v_fast["verdict"] == "improvement", v_fast
    print(f"[分类] +0.4% -> acceptable_change; {v_fast['delta']:+.1%} -> improvement"
          f"(改进也要告警)")

    # 7) CoV 前置门禁: 同一份代码重复跑
    stable_reps = [100.0 + rng.gauss(0, 0.5) for _ in range(20)]
    jumpy_reps = [100.0 + rng.gauss(0, 6.0) for _ in range(20)]
    ok_s, cv_s = cov_gate(stable_reps)
    ok_j, cv_j = cov_gate(jumpy_reps)
    assert ok_s and not ok_j
    print(f"[CoV] 稳定环境 CoV={cv_s:.3%}(通过), 抖动环境 CoV={cv_j:.3%}(先降噪)")

    # 8) 噪声地板: 抖动大的指标自适应放宽阈值
    assert noise_floor(stable_reps) == MAG_THRESHOLD
    assert noise_floor(jumpy_reps) > MAG_THRESHOLD
    print(f"[噪声地板] 稳定指标阈值 {noise_floor(stable_reps):.1%}, "
          f"抖动指标阈值 {noise_floor(jumpy_reps):.1%}")

    # 9) 多重比较: 200 个 benchmark 全无差异, 未校正/校正后的假阳性数
    m = 200
    pvals = []
    for _ in range(m):
        a = [100.0 + rng.gauss(0, 1.0) for _ in range(20)]
        b = [100.0 + rng.gauss(0, 1.0) for _ in range(20)]
        s1, s2 = statistics.stdev(a), statistics.stdev(b)
        t = (statistics.fmean(b) - statistics.fmean(a)) / math.sqrt(s1 ** 2 / 20 + s2 ** 2 / 20)
        # 用正态 p 值近似(仅用于本节的多重比较演示)
        pvals.append(2 * min(0.5 * (1 + math.erf(t / math.sqrt(2))),
                             1 - 0.5 * (1 + math.erf(t / math.sqrt(2)))))
    raw = sum(1 for p in pvals if p < ALPHA)
    bonf = sum(bonferroni(pvals))
    bh = sum(benjamini_hochberg(pvals))
    assert 2 <= raw <= 30, raw          # 期望约 5% * 200 = 10
    assert bonf <= bh <= raw
    print(f"[多重比较] {m} 个无差异 benchmark: 未校正 {raw} 个假阳性, "
          f"Bonferroni {bonf} 个, BH {bh} 个")


if __name__ == "__main__":
    _self_test()
    print("\nregression_gate: 全部自检通过")
