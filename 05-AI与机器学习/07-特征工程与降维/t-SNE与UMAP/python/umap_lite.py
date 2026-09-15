#!/usr/bin/env python3
"""UMAP-lite:按官方文档描述的机制实现(便于与 t-SNE 对照,不求工业级性能)。

官方文档 *How UMAP Works* 把算法拆成两块:
  阶段一 —— 构造数据的"模糊拓扑表示":k 近邻图,每条边的权重是
     exp(-(d_ij - ρ_i)/σ_i),其中 ρ_i 是 i 到最近邻的距离(局部连通性约束),
     σ_i 由二分搜索确定,使 Σ_j exp(-(d_ij-ρ_i)/σ_i) = log2(k);
     再做对称化 w = a + b − a·b(概率的 t-conorm)。
  阶段二 —— 优化低维表示,使两者的模糊拓扑尽可能一致。因为权重被解释成
     "该单纯形存在的概率",度量用**交叉熵**:
     第一项(高维权重大 → 低维距离要小)是吸引力,第二项是排斥力,
     整体像一个力导向图布局算法;实现上配 **负采样**(每个正样本抽若干随机负样本)
     做 SGD,把 O(n²) 的排斥项降到 O(n·n_neg)。
     低维核是 1/(1+a·d^{2b}),a、b 由 min_dist/spread 拟合得到(本文件用最小二乘拟合,
     不硬编码常数)。
"""

import math
import random


def pairwise_sq_distances(X):
    n = len(X)
    D2 = [[0.0] * n for _ in range(n)]
    for i in range(n):
        xi = X[i]
        for j in range(i + 1, n):
            s = 0.0
            xj = X[j]
            for t in range(len(xi)):
                d = xi[t] - xj[t]
                s += d * d
            D2[i][j] = D2[j][i] = s
    return D2


def knn_indices(D2, k):
    """暴力 k 近邻(不含自身,按距离升序)。"""
    out = []
    for i, row in enumerate(D2):
        order = sorted((j for j in range(len(row)) if j != i), key=lambda j: row[j])
        out.append(order[:k])
    return out


def fuzzy_weights(D2, knn, local_connectivity=1.0):
    """返回稀疏权重表 {(i,j): w},含对称化。"""
    n = len(D2)
    target = math.log2(max(len(knn[0]), 2))
    weights = {}
    for i in range(n):
        rho = math.sqrt(max(D2[i][knn[i][0]], 0.0))          # 到最近邻的距离
        dists = [math.sqrt(max(D2[i][j], 0.0)) for j in knn[i]]
        lo, hi, sigma = 0.0, None, 1.0
        for _ in range(64):                                  # 二分搜索 σ_i
            s = math.fsum(math.exp(-(d - rho) / sigma) for d in dists)
            if abs(s - target) < 1e-5:
                break
            if s > target:
                hi = sigma
                sigma = sigma * 2 if lo == 0.0 else (sigma + hi) / 2
            else:
                lo = sigma
                sigma = sigma / 2 if hi is None else (sigma + lo) / 2
        for j, d in zip(knn[i], dists):
            weights[(i, j)] = math.exp(-(d - rho) / sigma)
    sym = {}
    for (i, j), w in weights.items():
        w2 = weights.get((j, i), 0.0)
        sym[(i, j)] = w + w2 - w * w2                        # 概率 t-conorm:有边即"并"
    return sym


def fit_ab(min_dist=0.1, spread=1.0, n_grid=48, eps=1e-3):
    """最小二乘拟合低维核 1/(1+a·d^{2b}) ≈ 目标隶属度(UMAP 用曲线拟合得到 a、b)。

    目标:d <= min_dist 时为 1,之后按 exp(-(d-min_dist)/spread) 衰减。
    """
    best = (1.0, 1.0, float("inf"))
    xs = [eps + 3.0 * spread * t / n_grid for t in range(1, n_grid + 1)]
    ys = [1.0 if x <= min_dist else math.exp(-(x - min_dist) / spread) for x in xs]
    for ai in range(n_grid):
        a = 10.0 ** (-1.0 + 3.0 * ai / (n_grid - 1))         # a ∈ [0.1, 100]
        for bi in range(n_grid):
            b = 0.3 + 1.7 * bi / (n_grid - 1)                # b ∈ [0.3, 2.0]
            sse = math.fsum((1.0 / (1.0 + a * x ** (2 * b)) - y) ** 2
                            for x, y in zip(xs, ys))
            if sse < best[2]:
                best = (a, b, sse)
    return best[0], best[1]


def umap(X, n_neighbors=10, min_dist=0.1, n_epochs=300, n_negative=5, lr=1.0, seed=0):
    """返回 (Y, a, b, 边数)。优化 = 对交叉熵做带负采样的 SGD。"""
    rng = random.Random(seed)
    D2 = pairwise_sq_distances(X)
    knn = knn_indices(D2, n_neighbors)
    w = fuzzy_weights(D2, knn)
    edges = [(i, j, wt) for (i, j), wt in w.items() if wt > 1e-3]
    a, b = fit_ab(min_dist)
    n = len(X)
    Y = [[rng.uniform(-10.0, 10.0) for _ in range(2)] for _ in range(n)]
    for epoch in range(n_epochs):
        rng.shuffle(edges)
        alpha = lr * (1.0 - epoch / n_epochs)
        for i, j, wt in edges:
            _attract(Y, i, j, wt, a, b, alpha)
            for _ in range(n_negative):                      # 负采样:随机抽一个点当"排斥对"
                k = rng.randrange(n)
                if k != i:
                    _repel(Y, i, k, wt, a, b, alpha)
    return Y, a, b, len(edges)


def _attract(Y, i, j, w, a, b, alpha):
    dx = Y[i][0] - Y[j][0]
    dy = Y[i][1] - Y[j][1]
    d2 = dx * dx + dy * dy
    if d2 <= 0.0:
        return
    # 交叉熵的吸引项:∂/∂d = -2ab·d^{2b-1}/(1+a·d^{2b})
    coeff = -2.0 * a * b * d2 ** (b - 1.0) / (1.0 + a * d2 ** b) * w
    Y[i][0] += alpha * coeff * dx
    Y[i][1] += alpha * coeff * dy


def _repel(Y, i, k, w, a, b, alpha):
    dx = Y[i][0] - Y[k][0]
    dy = Y[i][1] - Y[k][1]
    d2 = dx * dx + dy * dy
    # 交叉熵的排斥项(数值稳定版:分母带 0.001 与 (1+a·d^{2b}) 两个因子)
    coeff = 2.0 * b / ((0.001 + d2) * (1.0 + a * d2 ** b)) * w
    Y[i][0] += alpha * coeff * dx
    Y[i][1] += alpha * coeff * dy
