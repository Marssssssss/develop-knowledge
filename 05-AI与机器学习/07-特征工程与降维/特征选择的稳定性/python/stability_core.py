#!/usr/bin/env python3
"""特征选择稳定性的核心构件:随机源、数据、选择器、度量、稳定性选择。

全部用标准库实现,数值与语义逐步对齐 sklearn.feature_selection;
与 Go 版共用同一套 LCG,故两边数值可逐位对拍。实验脚本见 main.py。
"""

import math

# --------------------------------------------------------------------------
# 0. 可复现随机源:同一条 LCG 在 Python 与 Go 里产出完全相同的数列
# --------------------------------------------------------------------------

MASK32 = 0xFFFFFFFF


class Rng:
    """Numerical Recipes 的线性同余发生器,state 落在 32 位无符号整数上。"""

    def __init__(self, seed=12345):
        self.state = seed & MASK32

    def u32(self):
        self.state = (1664525 * self.state + 1013904223) & MASK32
        return self.state

    def uniform(self):
        """[0, 1) 上的均匀数;用 2**32 归一,保证与 Go 的 float64 结果一致。"""
        return self.u32() / 4294967296.0

    def normal(self):
        """Box-Muller;一次消耗两个均匀数(与 Go 版保持一致,不要改成缓存版)。"""
        u1 = self.uniform() or 1e-12
        u2 = self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def randint(self, n):
        return self.u32() % n


def subsample_indices(n, size, rng):
    """无放回抽 size 个下标(部分 Fisher-Yates)。稳定性选择用的就是这种抽样。"""
    pool = list(range(n))
    for i in range(size):
        j = i + rng.randint(n - i)
        pool[i], pool[j] = pool[j], pool[i]
    return pool[:size]


def bootstrap_indices(n, rng):
    """有放回抽 n 个下标。"""
    return [rng.randint(n) for _ in range(n)]


# --------------------------------------------------------------------------
# 1. 数据与选择器
# --------------------------------------------------------------------------


def make_dataset(n=100, p=60, n_informative=4, effect=0.9, seed=7):
    """二分类:前 n_informative 个特征带类别均值差,其余是纯噪声。

    返回 (X, y),X 为 n×p 的行主序二维列表。
    """
    rng = Rng(seed)
    y = [i % 2 for i in range(n)]
    X = []
    for i in range(n):
        row = []
        for j in range(p):
            v = rng.normal()
            if j < n_informative and y[i] == 1:
                v += effect
            row.append(v)
        X.append(row)
    return X, y


def f_oneway(groups):
    """单因素方差分析的 F 值,逐步对齐 sklearn.feature_selection.f_oneway。

    sstot = Σx² − (Σx)²/n;ssbn = Σ_k (Σx_k)²/n_k − (Σx)²/n;sswn = sstot − ssbn;
    F = (ssbn/dfbn) / (sswn/dfwn),dfbn = k−1,dfwn = n−k。

    **常量列官方给的是 NaN 不是 0**:msb 与 msw 同时为零 → 0/0 = nan,源码只发一条
    UserWarning 就原样返回。(组间方差为正而组内为零时是 inf,即 msb/0。)
    """
    n = sum(len(g) for g in groups)
    ss_all = sum(x * x for g in groups for x in g)
    sum_all = sum(sum(g) for g in groups)
    sstot = ss_all - sum_all * sum_all / n
    ssbn = sum(sum(g) ** 2 / len(g) for g in groups) - sum_all * sum_all / n
    sswn = sstot - ssbn
    dfbn = len(groups) - 1
    dfwn = n - len(groups)
    msb = ssbn / dfbn
    msw = sswn / dfwn
    if msw == 0.0:
        return math.nan if msb == 0.0 else math.inf
    return msb / msw


def f_classif(X, y):
    """对每一列做 ANOVA F 检验,返回 F 值列表。"""
    p = len(X[0])
    classes = sorted(set(y))
    return [f_oneway([[row[j] for row, lab in zip(X, y) if lab == c] for c in classes])
            for j in range(p)]


NAN_REPLACEMENT = -1.7976931348623157e308      # float64 的最小有限值


def select_kbest(scores, k):
    """取分数最高的 k 个特征,返回下标集合。

    两处必须逐字对齐 sklearn:
      1. `_clean_nans` 把 NaN 换成 **float 最小有限值**(源码注释说 −inf 不可靠),
         于是常量列永远不会入选;
      2. `argsort(scores, kind="mergesort")[-k:]` 是**稳定排序取尾部**,
         因此并列时留下的是**下标更大**的那个 —— 很容易写反。
    """
    clean = [NAN_REPLACEMENT if s != s else s for s in scores]
    order = sorted(range(len(clean)), key=lambda i: (clean[i], i))
    return set(order[len(order) - k:])


def selection_sets(X, y, k, m, rng, bootstrap=False):
    """跑 m 次「重抽样 + SelectKBest」,返回 m 个特征下标集合。

    bootstrap=True 时有放回抽 n 个;否则无放回抽 ⌊n/2⌋ 个(稳定性选择的口径)。
    """
    n = len(X)
    out = []
    for _ in range(m):
        idx = bootstrap_indices(n, rng) if bootstrap else subsample_indices(n, n // 2, rng)
        out.append(select_kbest(f_classif([X[i] for i in idx], [y[i] for i in idx]), k))
    return out


# --------------------------------------------------------------------------
# 2. 稳定性度量
# --------------------------------------------------------------------------


def jaccard(a, b):
    return len(a & b) / len(a | b)


def dice(a, b):
    return 2 * len(a & b) / (len(a) + len(b))


def kuncheva(a, b, d):
    """Kuncheva(2007) 的机会校正指标(仅对 |a| = |b| = k 有定义):

        IC = (r·d − k²) / (k·(d − k))

    其中 r = |a ∩ b|。它的期望在零模型下恒为 0,故可跨 k 比较。
    """
    k = len(a)
    r = len(a & b)
    return (r * d - k * k) / (k * (d - k))


def mean_pairwise(sets, sim):
    """所有无序对的相似度平均。"""
    m = len(sets)
    if m < 2:
        raise ValueError("至少需要 2 个特征集合")
    tot = 0.0
    for i in range(m):
        for j in range(i + 1, m):
            tot += sim(sets[i], sets[j])
    return tot / (m * (m - 1) / 2)


def phi_stability(sets, d):
    """Nogueira 等的稳定性估计量(Definition 4):

        Φ̂ = 1 − [ (1/d)·Σ_f s²_f ] / [ (k̄/d)(1 − k̄/d) ],  s²_f = M/(M−1)·p̂_f(1−p̂_f)

    零模型下 E[Φ̂] = 0(机会校正),全同则 Φ̂ = 1,下界 −1/(M−1)。
    """
    m = len(sets)
    p_hat = [sum(1 for s in sets if f in s) / m for f in range(d)]
    k_bar = sum(len(s) for s in sets) / m
    var_sum = sum(m / (m - 1) * p * (1 - p) for p in p_hat) / d
    return 1.0 - var_sum / ((k_bar / d) * (1 - k_bar / d))


def random_sets(m, k, d, rng):
    """零模型:每个集合都是从 d 个特征里均匀无放回抽 k 个(与选择器无关)。"""
    return [set(subsample_indices(d, k, rng)) for _ in range(m)]


# --------------------------------------------------------------------------
# 3. 稳定性选择
# --------------------------------------------------------------------------


def selection_probabilities(sets, d):
    """每列的入选频率 Π̂_k。"""
    m = len(sets)
    return [sum(1 for s in sets if f in s) / m for f in range(d)]


def stable_features(probs, pi_thr):
    """Definition 2:Ŝ^stable = {k : max_λ Π̂^λ_k ≥ π_thr}。"""
    return {f for f, p in enumerate(probs) if p >= pi_thr}


def pfer_bound(q, pi_thr, p):
    """Theorem 1:E(V) ≤ q² / ((2·π_thr − 1)·p),q 是平均选中个数。"""
    return q * q / ((2 * pi_thr - 1) * p)
