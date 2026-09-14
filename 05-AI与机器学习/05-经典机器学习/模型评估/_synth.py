"""确定性合成数据与数值小工具(供 model_eval.py / roc_auc.py 共用)。

用自定义 LCG 而非 `random` 模块:参数与 Go/C 版完全一致,三个语言实现结果可逐位复现。
"""

import math


def lcg(seed):
    """64 位线性同余发生器(乘子/增量与 Go、C 版相同),返回 [0,1) 均匀数生成器。"""
    st = seed

    def rnd():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)
    return rnd


def gauss(r):
    """Box-Muller 变换:两个 [0,1) 均匀数 → 一个标准正态数。"""
    return math.sqrt(-2.0 * math.log(max(r(), 1e-12))) * math.cos(2.0 * math.pi * r())


def sigmoid(z):
    return 1.0 / (1.0 + math.exp(-z))


def mean_sd(v):
    """样本均值与**样本**标准差(n−1 做分母)—— 交叉验证报告的惯例。"""
    mu = sum(v) / len(v)
    return mu, math.sqrt(sum((x - mu) ** 2 for x in v) / (len(v) - 1))


def binary_data(n, seed):
    """y ~ Bernoulli(σ(2.2·x0 − 1.6·x1 + 0.3)),得分用真实权重重算的 σ。

    标签本身带噪 ⇒ 再好的模型 AUC 也 < 1(存在 Bayes 最优上限)。
    """
    r, rows = lcg(seed), []
    for _ in range(n):
        x0, x1 = gauss(r) * 1.3, gauss(r) * 1.3
        p = sigmoid(2.2 * x0 - 1.6 * x1 + 0.3)
        rows.append(([x0, x1], 1 if r() < p else 0, p))
    return [v[0] for v in rows], [v[1] for v in rows], [v[2] for v in rows]


def imbalanced_data(n, seed, pos_rate=0.05):
    """极不平衡 + **按 x0 排序**(模拟按时间/ID 天然的排序)。

    sklearn 的 KFold 默认 shuffle=False,直接切连续块 —— 数据一旦有序,
    各折就不是同分布的。这正是官方文档专门警告的场景。
    """
    r, rows = lcg(seed), []
    for _ in range(n):
        x0, x1 = gauss(r) * 1.4, gauss(r) * 1.4
        z = 2.6 * x0 - 2.0 * x1 + 1.8
        rows.append((x0, x1, 1 if (r() < sigmoid(z) and r() < pos_rate * 2) else 0))
    rows.sort(key=lambda t: t[0])
    return [[a, b] for a, b, _ in rows], [c for _, _, c in rows]


def multi_data(n, seed):
    """3 个类心,得分 = softmax(−‖x−μ_j‖²/2),标签 = argmax,再按 12% 随机翻标签加噪。"""
    r = lcg(seed)
    mus = [(0.0, 0.0), (3.0, 1.2), (1.2, 3.2)]
    X, y, S = [], [], []
    for _ in range(n):
        c = int(r() * 3)
        x = [mus[c][0] + gauss(r) * 0.9, mus[c][1] + gauss(r) * 0.9]
        sc = [math.exp(-(((x[0] - m[0]) ** 2 + (x[1] - m[1]) ** 2) / 2.0)) for m in mus]
        t = sum(sc)
        sc = [v / t for v in sc]
        lab = max(range(3), key=lambda j: sc[j])
        X.append(x)
        y.append((lab + 1) % 3 if r() < 0.12 else lab)
        S.append(sc)
    return X, y, S
