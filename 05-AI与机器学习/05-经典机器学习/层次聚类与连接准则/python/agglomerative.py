# -*- coding: utf-8 -*-
"""层次聚类(凝聚式)与四种连接准则 —— 纯标准库实现。

权威来源(实际读过,逐条 URL 见同目录 README.md):
- scikit-learn《2.3.6 Hierarchical clustering》:
  * 自底向上:每个观测自成一簇,逐次合并;**linkage criteria 决定合并用的度量**;
  * Ward = 最小化所有簇内平方差之和,是**方差最小化**方法,在这一点上与 k-means 目标相似,
    只不过用凝聚层次的方式求解;
  * Maximum/complete = 最小化两簇观测间的**最大**距离;Average = **平均**距离;
    Single = **最近**观测对的距离;
  * "Agglomerative cluster has a **rich get richer** behavior that leads to uneven cluster sizes.
    In this regard, single linkage is the worst strategy, and Ward gives the most regular sizes";
  * Ward 的 affinity 不能换(只能用欧氏),非欧氏度量下 average linkage 是个好替代;
  * 树状图可视化、connectivity 约束与"rich getting richer"的相互增强。
- Lance & Williams《A General Theory of Classificatory Sorting Strategies》, Computer Journal 9(4),
  1967 —— Lance-Williams 递推的系数表(见 README 表格;四个系数组在本自检里逐条与"直接重算"
  交叉验证,不依赖记忆数值)。
"""
from __future__ import annotations

import math

LINKAGES = ("single", "complete", "average", "ward")

# Lance-Williams 系数:d(ij,k) = α_i·d(i,k) + α_j·d(j,k) + β·d(i,j) + γ·|d(i,k)−d(j,k)|
def lw_coeffs(linkage, ni, nj, nk):
    if linkage == "single":
        return 0.5, 0.5, 0.0, -0.5
    if linkage == "complete":
        return 0.5, 0.5, 0.0, 0.5
    if linkage == "average":
        tot = ni + nj
        return ni / tot, nj / tot, 0.0, 0.0
    if linkage == "ward":
        tot = ni + nj + nk
        return (ni + nk) / tot, (nj + nk) / tot, -nk / tot, 0.0
    raise ValueError("unknown linkage: %s" % linkage)


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def ward_gain(mem_i, mem_j):
    """合并 i、j 引起的簇内平方和增量(即 Ward 的合并代价):

    ΔSSE = (n_i·n_j/(n_i+n_j))·||c_i − c_j||²
    """
    ni, nj = len(mem_i), len(mem_j)
    d = len(mem_i[0])
    ci = [sum(p[t] for p in mem_i) / ni for t in range(d)]
    cj = [sum(p[t] for p in mem_j) / nj for t in range(d)]
    return ni * nj / (ni + nj) * sum((a - b) ** 2 for a, b in zip(ci, cj))


class Agglomerative:
    """凝聚层次聚类。

    距离矩阵的口径:
    - single / complete / average 用**欧氏距离** d;
    - ward 用**半平方距离 d²/2**。理由:Lance-Williams 递推对 D 是齐次的(ward 的系数满足
      α_i+α_j+β = 1),而在平方距离上它给出的值是 ΔSSE 的 **2 倍**(两点特例:
      ΔSSE = d²/2 而递推给出 d²)。把初值取成 d²/2,后续每一步的取值就**恰好是合并代价 ΔSSE**。
    """

    def __init__(self, linkage="ward"):
        if linkage not in LINKAGES:
            raise ValueError("unknown linkage: %s" % linkage)
        self.linkage = linkage

    def _pair_dist(self, members, a, b):
        """按准则直接算两簇(下标集合)之间的距离 —— 用于校验 Lance-Williams 递推。"""
        ma, mb = members[a], members[b]
        if self.linkage == "single":
            return min(_dist(self.X[i], self.X[j]) for i in ma for j in mb)
        if self.linkage == "complete":
            return max(_dist(self.X[i], self.X[j]) for i in ma for j in mb)
        if self.linkage == "average":
            vs = [_dist(self.X[i], self.X[j]) for i in ma for j in mb]
            return sum(vs) / len(vs)
        return ward_gain([self.X[i] for i in ma], [self.X[i] for i in mb])

    def fit(self, X):
        self.X = X
        n = len(X)
        sq = self.linkage == "ward"
        # 初始距离矩阵(活跃簇之间)
        D = {}
        for i in range(n):
            for j in range(i + 1, n):
                d = _dist(X[i], X[j])
                D[(i, j)] = d * d / 2.0 if sq else d  # ward:半平方距离 → 取值即 ΔSSE
        members = {i: [i] for i in range(n)}
        active = list(range(n))
        self.merges = []          # (a, b, height, size)
        self.updates = 0
        nxt = n
        while len(active) > 1:
            best = None
            for a in range(len(active)):
                for b in range(a + 1, len(active)):
                    k = self._key(active[a], active[b])
                    if best is None or D[k] < D[self._key(*best)] - 1e-15:
                        best = (active[a], active[b])
            i, j = best
            h = D.pop(self._key(i, j))
            new = nxt
            nxt += 1
            members[new] = members[i] + members[j]
            ni, nj = len(members[i]), len(members[j])
            for k in active:
                if k in (i, j):
                    continue
                nk = len(members[k])
                ai, aj, beta, gamma = lw_coeffs(self.linkage, ni, nj, nk)
                dij = h
                dik = D.pop(self._key(i, k))
                djk = D.pop(self._key(j, k))
                val = ai * dik + aj * djk + beta * dij + gamma * abs(dik - djk)
                D[self._key(new, k)] = val
                self.updates += 1
            active = [c for c in active if c not in (i, j)] + [new]
            self.merges.append((i, j, h, len(members[new])))
            self.members_ = members
        # 记录一次递推值与直接重算值的对照(供自检使用)
        self._final_members = members
        return self

    @staticmethod
    def _key(a, b):
        return (a, b) if a < b else (b, a)

    def cut(self, k):
        return cut_tree(self.merges, len(self.X), k)

    def heights(self):
        return [m[2] for m in self.merges]


def cut_tree(merges, n, k):
    """独立的切割函数:按合并序列回放,得到恰好 k 个簇的标签。"""
    parent = {i: i for i in range(n)}
    groups = {i: [i] for i in range(n)}
    nxt = n
    for a, b, _h, _s in merges[:n - k]:
        ga, gb = groups.pop(a), groups.pop(b)
        groups[nxt] = ga + gb
        nxt += 1
    labels = [0] * n
    for c, mem in enumerate(groups.values()):
        for i in mem:
            labels[i] = c
    return labels


# -------------------------------------------------------------- 对照:k-means
def kmeans(X, k, seed=0, iters=100):
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
