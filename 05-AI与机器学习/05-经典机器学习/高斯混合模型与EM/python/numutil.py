# -*- coding: utf-8 -*-
"""GMM 依赖的数值工具:logsumexp / Cholesky / 马氏距离 / RNG / k-means 初始化。

从 `gmm.py` 拆出(逻辑零变化),由 `gmm.py` 用 `from numutil import *` 回引,
因此 `import gmm as G` 后仍可直接 `G.cholesky(...)`。
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- 数值工具
def logsumexp(vals):
    """log Σ exp(v):先减最大值再 exp,避免下溢/上溢。"""
    m = max(vals)
    if m == -math.inf:
        return -math.inf
    return m + math.log(sum(math.exp(v - m) for v in vals))


def cholesky(A):
    """下三角 Cholesky 分解 A = L·Lᵀ;非正定时抛 ValueError(对应 GMM 的奇异性)。"""
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                d = A[i][i] - s
                if d <= 1e-300:
                    raise ValueError("covariance is not positive definite (singular component)")
                L[i][i] = math.sqrt(d)
            else:
                L[i][j] = (A[i][j] - s) / L[j][j]
    return L


def logdet_chol(L):
    return 2.0 * sum(math.log(L[i][i]) for i in range(len(L)))


def mahal_sq_chol(L, d):
    """解 L·y = d(前代),返回 ||y||²,即 dᵀΣ⁻¹d。"""
    n = len(d)
    y = [0.0] * n
    for i in range(n):
        s = sum(L[i][k] * y[k] for k in range(i))
        y[i] = (d[i] - s) / L[i][i]
    return sum(v * v for v in y)


# ------------------------------------------------------------------- RNG
def _rng(seed):
    st = seed & 0xFFFFFFFFFFFFFFFF

    def rnd():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)

    return rnd


# ------------------------------------------------------------------ k-means
def kmeans(X, k, seed=0, iters=100):
    """Lloyd 迭代 + k-means++ 式播种(用于 GMM 的 init_params='kmeans' 默认初始化)。"""
    rnd = _rng(seed)
    n = len(X)
    centers = [list(X[int(rnd() * n)])]
    for _ in range(k - 1):
        d2 = [min(sum((a - b) ** 2 for a, b in zip(x, c)) for c in centers) for x in X]
        tot = sum(d2)
        if tot <= 0:
            centers.append(list(X[int(rnd() * n)]))
            continue
        r, acc = rnd() * tot, 0.0
        for i, v in enumerate(d2):
            acc += v
            if acc >= r:
                centers.append(list(X[i]))
                break
        else:
            centers.append(list(X[-1]))
    lab = [0] * n
    for _ in range(iters):
        for i, x in enumerate(X):
            lab[i] = min(range(k), key=lambda c: sum((a - b) ** 2 for a, b in zip(x, centers[c])))
        moved = False
        for c in range(k):
            mem = [X[i] for i in range(n) if lab[i] == c]
            if not mem:
                continue
            new = [sum(m[d] for m in mem) / len(mem) for d in range(len(X[0]))]
            if new != centers[c]:
                centers[c] = new
                moved = True
        if not moved:
            break
    return centers, lab
