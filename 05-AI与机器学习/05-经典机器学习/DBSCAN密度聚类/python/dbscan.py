# -*- coding: utf-8 -*-
"""DBSCAN 密度聚类 —— 严格按 Ester et al. 1996 原论文伪码的纯标准库实现。

权威来源(实际读过,逐条 URL 见同目录 README.md):
- Ester, Kriegel, Sander, Xu《A Density-Based Algorithm for Discovering Clusters in Large Spatial
  Databases with Noise》,KDD-96(原文 6 页全文实读):
  * Definition 1 `NEps(p) = {q ∈ D | dist(p,q) ≤ Eps}` —— **含 p 自身**(dist=0);
  * Definition 2 directly density-reachable:`p ∈ NEps(q)` 且 `|NEps(q)| ≥ MinPts`(core point
    condition);原文明确"对两个核心点对称,核心点-边界点之间不对称";
  * Definition 3/4 density-reachable / density-connected;Definition 5 cluster(Maximality +
    Connectivity);Definition 6 noise;
  * 原文"a cluster contains at least MinPts points";
  * 伪码 `if seeds.size < MinPts then changeClId(Point, NOISE)` —— seeds 是 regionQuery 的结果
    (含自身),再次印证 MinPts 计自身;
  * "point p will be assigned to the cluster discovered first" —— 同时属于两簇的点归**先发现**的簇;
  * 平均运行时间 **O(n·log n)**(R*-tree 下每点至多一次 region query);
  * §4.2 k-dist 启发式:p 到第 k 近邻的距离为 d 时,d-邻域含 **k+1** 个点;取 sorted k-dist
    图第一个"谷"处的阈值点,论文图示用 4-dist。
- scikit-learn《2.3.7 DBSCAN》与源码 `sklearn/cluster/_dbscan.py`:
  * API 文档 `min_samples`: "The number of samples (or total weight) in a neighborhood for a
    point to be considered as a core point. **This includes the point itself.**";
  * 用户指南却写 "there exist **min_samples other** samples within a distance of eps" ——
    与 API 文档/原论文口径差 1,本实现取"含自身"(README 与代码注释双处标注);
  * labels 中噪声为 **−1**;核心点下标存在 `core_sample_indices_`。
"""
from __future__ import annotations

import math

UNCLASSIFIED = -2
NOISE = -1


def region_query(X, i, eps, stats=None):
    """返回 X[i] 的 eps-邻域下标列表,**包含 i 自身**(Definition 1:dist ≤ eps 且 p 到自己为 0)。"""
    if stats is not None:
        stats[0] += 1
    out = []
    for j, x in enumerate(X):
        s = 0.0
        for a, b in zip(X[i], x):
            s += (a - b) * (a - b)
        if math.sqrt(s) <= eps:
            out.append(j)
    return out


def dbscan(X, eps, min_samples, stats=None):
    """返回 (labels, core_indices)。labels: 簇号 ≥ 0,噪声 = −1。"""
    n = len(X)
    labels = [UNCLASSIFIED] * n
    iscore = [False] * n  # 论文:每个点至多被 region query 一次,顺带记下核心点
    cid = 0
    for i in range(n):
        if labels[i] != UNCLASSIFIED:
            continue
        # ---- ExpandCluster(论文伪码) ----
        seeds = region_query(X, i, eps, stats)
        if len(seeds) < min_samples:          # no core point
            labels[i] = NOISE                 # 之后可能被核心点收编为边界点
            continue
        iscore[i] = True
        # 伪码 changeCiIds(seeds, ClId):种子整体打簇号。**但不能覆盖已有簇标签** ——
        # 论文:"point p will be assigned to the cluster discovered first",
        # 即同时属于两簇的边界点归先发现者;只有 UNCLASSIFIED / NOISE 可被改写。
        for s in seeds:
            if labels[s] in (UNCLASSIFIED, NOISE):
                labels[s] = cid
        seeds = [s for s in seeds if s != i]  # seeds.delete(Point)
        while seeds:
            cur = seeds.pop(0)
            result = region_query(X, cur, eps, stats)
            if len(result) >= min_samples:    # cur 是核心点
                iscore[cur] = True
                for r in result:
                    if labels[r] in (UNCLASSIFIED, NOISE):
                        if labels[r] == UNCLASSIFIED:
                            seeds.append(r)
                        labels[r] = cid       # 原标 NOISE 的边界点在此被改写
            # 边界点不进 seeds(论文:加进去只会多一次无新答案的 region query)
        cid += 1
    core = [i for i in range(n) if iscore[i]]
    return [NOISE if l == UNCLASSIFIED else l for l in labels], core


def is_core(X, i, eps, min_samples):
    return len(region_query(X, i, eps)) >= min_samples


def kdist(X, k):
    """每个点到第 k 近邻的距离(k-dist),用于 §4.2 的 sorted k-dist 图。"""
    out = []
    for i, x in enumerate(X):
        ds = sorted(math.dist(x, y) for j, y in enumerate(X) if j != i)
        out.append(ds[k - 1])
    return out


def sorted_kdist(X, k):
    """按 k-dist **降序**排列(论文:threshold 点是第一个"谷"的谷底)。"""
    return sorted(kdist(X, k), reverse=True)


# --------------------------------------------------------- 对照:k-means
def kmeans(X, k, seed=0, iters=100):
    """Lloyd 迭代:用于演示"k-means 只能切凸形,DBSCAN 能切任意形状"。"""
    st = seed

    def rnd():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)

    n, d = len(X), len(X[0])
    centers = [list(X[int(rnd() * n)])]
    while len(centers) < k:
        d2 = [min(sum((a - b) ** 2 for a, b in zip(x, c)) for c in centers) for x in X]
        tot = sum(d2) or 1.0
        r, acc = rnd() * tot, 0.0
        for idx, v in enumerate(d2):
            acc += v
            if acc >= r:
                centers.append(list(X[idx]))
                break
        else:
            centers.append(list(X[-1]))
    lab = [0] * n
    for _ in range(iters):
        moved = False
        for i, x in enumerate(X):
            b = min(range(k), key=lambda c: sum((a - b2) ** 2 for a, b2 in zip(x, centers[c])))
            if lab[i] != b:
                lab[i], moved = b, True
        for c in range(k):
            mem = [X[i] for i in range(n) if lab[i] == c]
            if mem:
                centers[c] = [sum(m[j] for m in mem) / len(mem) for j in range(d)]
        if not moved:
            break
    return lab


def ari(a, b):
    """调整兰德指数(比较划分是否等价,忽略簇标签置换)。"""
    n = len(a)
    mx = max(max(a), max(b)) + 1
    sa, sb, tab = [0] * mx, [0] * mx, {}
    for i in range(n):
        sa[a[i]] += 1
        sb[b[i]] += 1
        tab[(a[i], b[i])] = tab.get((a[i], b[i]), 0) + 1
    c = lambda v: v * (v - 1) / 2.0
    sij = sum(c(v) for v in tab.values())
    xa, xb = sum(c(v) for v in sa), sum(c(v) for v in sb)
    exp, mxp = xa * xb / c(n), (xa + xb) / 2.0
    return (sij - exp) / (mxp - exp) if mxp != exp else 1.0
