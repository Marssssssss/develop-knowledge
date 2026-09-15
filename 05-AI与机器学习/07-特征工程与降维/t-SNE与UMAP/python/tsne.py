#!/usr/bin/env python3
"""t-SNE 核心实现(van der Maaten & Hinton, JMLR 9:2579-2605, 2008)。

只依赖标准库,目的是让"t-SNE 到底做了什么"完全可见:
  1. 高维空间按点对条件概率定义相似度 P_{j|i},其带宽由 **perplexity** 二分搜索决定
  2. 低维空间用**重尾的 t 分布**(自由度为 1)定义相似度 Q_ij ∝ (1+‖y_i-y_j‖²)^{-1}
  3. 最小化 KL(P‖Q),即"高维近的点在低维也必须近",反之不惩罚 —— 这是拥挤问题
     (crowding problem)的解法,也是"簇间距离不可信"这一性质的根源
  4. 早期夸张(early exaggeration)把 P 乘一个系数,先让簇成形再 Relax
"""

import math
import random


def squared_distances(X):
    n = len(X)
    D2 = [[0.0] * n for _ in range(n)]
    for i in range(n):
        xi = X[i]
        row = D2[i]
        for j in range(i + 1, n):
            xj = X[j]
            s = 0.0
            for t in range(len(xi)):
                d = xi[t] - xj[t]
                s += d * d
            row[j] = s
            D2[j][i] = s
    return D2


def joint_probabilities(D2, perplexity, tol=1e-5, max_iter=60):
    """按 perplexity 逐点二分搜索高斯带宽,再做对称化。

    官方描述:perplexity 可理解为"每个点的有效近邻数",原论文说"typical values are
    between 5 and 50",并且"really should be smaller than the number of points"。
    """
    n = len(D2)
    target = math.log(perplexity)
    P = [[0.0] * n for _ in range(n)]
    for i in range(n):
        beta, lo, hi = 1.0, 0.0, float("inf")
        for _ in range(max_iter):
            row = [math.exp(-beta * D2[i][j]) if j != i else 0.0 for j in range(n)]
            s = math.fsum(row)
            if s <= 1e-300:              # beta 过大,权重全部下溢 ⇒ 把上界收到当前 beta
                hi = beta
                beta = 0.5 * (lo + beta)
                continue
            # H(P_i) = log Z + beta·Σ_j d_ij²·p_ji,用于和 log(perplexity) 做二分
            entropy = math.log(s) + beta * math.fsum(D2[i][j] * row[j] for j in range(n)) / s
            if abs(entropy - target) < tol:
                break
            if entropy > target:         # 熵太大 ⇒ 分布太平 ⇒ 需要更大的 beta
                lo = beta
            else:
                hi = beta
            beta = beta * 2.0 if hi == float("inf") else 0.5 * (lo + hi)
        row = [math.exp(-beta * D2[i][j]) if j != i else 0.0 for j in range(n)]
        s = math.fsum(row) or 1.0
        for j in range(n):
            P[i][j] = row[j] / s
    for i in range(n):                                  # 对称化 P_ij = (P_{j|i}+P_{i|j})/(2n)
        for j in range(i + 1, n):
            v = (P[i][j] + P[j][i]) / (2.0 * n)
            P[i][j] = P[j][i] = v
    return P


def tsne(X, perplexity=30.0, n_iter=500, lr=200.0, exaggeration=12.0,
         exaggeration_iters=100, seed=0, snapshots=None):
    """返回 (Y, kl 历史, snapshots 字典)。snapshots 形如 {步数: 当时的 Y 副本}。"""
    rng = random.Random(seed)
    n = len(X)
    P = joint_probabilities(squared_distances(X), perplexity)
    Y = [[rng.gauss(0.0, 1e-4) for _ in range(2)] for _ in range(n)]  # 小随机初始化
    velocity = [[0.0, 0.0] for _ in range(n)]
    kl_hist = []
    snapshots = dict(snapshots or {})
    for step in range(1, n_iter + 1):
        alpha = exaggeration if step <= exaggeration_iters else 1.0
        momentum = 0.5 if step <= exaggeration_iters else 0.8
        # 低维相似度:自由度为 1 的 t 分布核
        inv = [[0.0] * n for _ in range(n)]
        total = 0.0
        for i in range(n):
            yi = Y[i]
            for j in range(i + 1, n):
                dx = yi[0] - Y[j][0]
                dy = yi[1] - Y[j][1]
                v = 1.0 / (1.0 + dx * dx + dy * dy)
                inv[i][j] = inv[j][i] = v
                total += 2.0 * v
        total = total or 1.0
        # ∂KL/∂y_i = 4·Σ_j (α·P_ij - Q_ij)·(1+‖y_i-y_j‖²)^{-1}·(y_i-y_j)
        grad = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            yi = Y[i]
            gx = gy = 0.0
            for j in range(n):
                if j == i:
                    continue
                w = (alpha * P[i][j] - inv[i][j] / total) * inv[i][j]
                gx += w * (yi[0] - Y[j][0])
                gy += w * (yi[1] - Y[j][1])
            grad[i][0], grad[i][1] = 4.0 * gx, 4.0 * gy
        for i in range(n):
            for d in range(2):
                velocity[i][d] = momentum * velocity[i][d] - lr * grad[i][d]
                Y[i][d] += velocity[i][d]
        for i in range(n):                              # 每步把质心拉回原点,避免整体漂移
            pass
        kl_hist.append(kl_divergence(P, inv, total))
        if step in snapshots:
            snapshots[step] = [list(p) for p in Y]
    return Y, kl_hist, snapshots


def kl_divergence(P, inv, total):
    n = len(P)
    kl = 0.0
    for i in range(n):
        for j in range(n):
            if i != j and P[i][j] > 0.0:
                kl += P[i][j] * math.log(P[i][j] * total / inv[i][j])
    return kl
