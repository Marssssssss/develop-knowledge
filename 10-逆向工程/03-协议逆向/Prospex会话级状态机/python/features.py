"""Prospex —— 特征、距离与消息聚类（论文 §2.2）。

三组特征各占 1/3、组内等权；PAM 聚类；泛化 Dunn 指数选 k。
"""

from __future__ import annotations

import itertools

# ---------------------------------------------------------------- 特征与距离

# 三组特征（论文 §2.2）：message / execution / file-system。
# 论文规定：每组总权重 1/3，组内特征等权。
FEATURE_GROUPS = (
    ("direction", "keywords"),   # message 组
    ("funcs", "syscalls"),       # execution 组
    ("fileops",),                # file-system 组
)


def feature_weights():
    """每组 1/3，组内等权。"""
    w = {}
    for group in FEATURE_GROUPS:
        for name in group:
            w[name] = 1.0 / (3 * len(group))
    return w


WEIGHTS = feature_weights()


def jaccard(a, b):
    """Jaccard 指数。两边都空时定义为 1（完全相似），否则 0/0 无定义。"""
    if not a and not b:
        return 1.0
    u = a | b
    if not u:
        return 1.0
    return len(a & b) / len(u)


def _eq(a, b):
    return 1.0 if a == b else 0.0


def similarity(a, b):
    """s_i(a,b) 逐特征相似度。direction 只有相等/不等两种，其余走 Jaccard。"""
    return {
        "direction": _eq(a.direction, b.direction),
        "keywords": jaccard(a.keywords, b.keywords),
        "funcs": jaccard(a.funcs, b.funcs),
        "syscalls": jaccard(a.syscalls, b.syscalls),
        "fileops": jaccard(a.fileops, b.fileops),
    }


def distance(a, b):
    """d(a,b) = 1 - sum_i w_i * s_i(a,b)（论文 §2.2.2 式）。"""
    s = similarity(a, b)
    return 1.0 - sum(WEIGHTS[k] * v for k, v in s.items())


class Message:
    """一条被监控到的协议消息。"""

    def __init__(self, mid, direction, keywords=(), funcs=(), syscalls=(),
                 fileops=()):
        self.mid = mid
        self.direction = direction
        self.keywords = frozenset(keywords)
        self.funcs = frozenset(funcs)
        self.syscalls = frozenset(syscalls)
        self.fileops = frozenset(fileops)

    def __repr__(self):
        return "Message(%s)" % self.mid


# ---------------------------------------------------------------- PAM 聚类

def _cost(medoids, points, dist):
    return sum(min(dist(p, m) for m in medoids) for p in points)


def _assign(medoids, points, dist):
    clusters = [[] for _ in medoids]
    for p in points:
        best = min(range(len(medoids)), key=lambda i: dist(p, medoids[i]))
        clusters[best].append(p)
    return clusters


def pam(points, k, dist, iters=40, seed=0):
    """Partitioning Around Medoids：先贪心选初始代表点，再 swap 到局部最优。"""
    n = len(points)
    if k >= n:
        return [[p] for p in points], [p for p in points]
    # BUILD：首个取离全局质心最近的点，之后取「能最大降低代价」的点
    chosen = [points[0]]
    while len(chosen) < k:
        cand, gain = None, None
        for p in points:
            if p in chosen:
                continue
            g = _cost(chosen, points, dist) - _cost(chosen + [p], points, dist)
            if gain is None or g > gain:
                cand, gain = p, g
        chosen.append(cand)
    # SWAP
    cur = _cost(chosen, points, dist)
    for _ in range(iters):
        improved = False
        for i, m in enumerate(chosen):
            for p in points:
                if p in chosen:
                    continue
                trial = list(chosen)
                trial[i] = p
                c = _cost(trial, points, dist)
                if c < cur - 1e-12:
                    chosen, cur, improved = trial, c, True
                    break
            if improved:
                break
        if not improved:
            break
    return _assign(chosen, points, dist), chosen


# ---------------------------------------------------------------- Dunn 指数

def _rng_diameter(cluster, dist):
    """基于 Relative Neighborhood Graph 的直径（论文 §2.2.2 引 [29]）。

    RNG 中 (a,b) 成边当且仅当不存在 c 使 max(d(a,c), d(b,c)) < d(a,b)。
    直径取 RNG 上的最大边权；单点/无边时为 0。
    """
    if len(cluster) < 2:
        return 0.0
    best = 0.0
    for a, b in itertools.combinations(cluster, 2):
        dab = dist(a, b)
        if all(max(dist(a, c), dist(b, c)) >= dab for c in cluster
               if c is not a and c is not b):
            best = max(best, dab)
    return best


def dunn_index(clusters, dist):
    """D(k) = min_{i≠j} δ(Ci,Cj) / max_i Δ(Ci)，δ 取 single-linkage。"""
    nonempty = [c for c in clusters if c]
    if len(nonempty) < 2:
        return 0.0
    sep = min(dist(a, b) for ci, cj in itertools.combinations(nonempty, 2)
              for a in ci for b in cj)
    dia = max(_rng_diameter(c, dist) for c in nonempty)
    if dia == 0.0:
        return float("inf") if sep > 0 else 0.0
    return sep / dia


def choose_k(points, dist, kmax):
    """枚举 k = 2..kmax，取 Dunn 指数最大的那个。"""
    best_k, best_v = 1, -1.0
    for k in range(2, min(kmax, len(points)) + 1):
        clusters, _ = pam(points, k, dist)
        v = dunn_index(clusters, dist)
        if v > best_v:
            best_k, best_v = k, v
    return best_k
