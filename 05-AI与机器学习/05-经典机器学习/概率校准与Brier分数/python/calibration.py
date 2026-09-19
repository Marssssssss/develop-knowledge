# -*- coding: utf-8 -*-
"""概率校准(Platt sigmoid / isotonic / temperature scaling)与 Brier 分数分解 —— 纯标准库。

权威来源(实际读过,逐条 URL 见同目录 README.md):
- scikit-learn《1.16. Probability calibration》:
  * 良好校准的定义:"among the samples to which it gave predict_proba value close to 0.8,
    approximately 80% actually belong to the positive class";
  * **Brier 与 log_loss 同时刻画 reliability(校准)、resolution(判别力)与 uncertainty(数据固有随机性)**,
    "This follows from the well-known Brier score decomposition of Murphy [1973]",因此
    "A lower Brier loss does not necessarily mean a better calibrated model";
  * 校准曲线 = reliability diagram(Wilks 1995):y 轴是**该箱内真实正例比例**,x 轴是**该箱平均预测概率**;
  * sigmoid = Platt 逻辑模型 `p = 1/(1+exp(A·f+B))`,假设校准误差**对称**,在高度不平衡时会有问题;
  * isotonic = 保序回归,只要求单调,能修正任意单调畸变,但**更易过拟合**(样本 > ~1000 才好);
    isotonic 会引入**并列**,可能改变 AUC;sigmoid 严格单调,**保持排序**;
  * temperature scaling:`softmax(z/T)`,T 由 log_loss 学出,**不改变 argmax ⇒ 不改变 accuracy**;
  * 校准器必须拟合在**与训练分类器无关**的数据上(否则偏置到 0/1)。
- scikit-learn 源码 `sklearn/calibration.py`(`_sigmoid_calibration`):
  * Platt 平滑后的目标值 `T[y>0] = (N_pos+1)/(N_pos+2)`、`T[y≤0] = 1/(N_neg+2)`;
  * 初值 `AB0 = [0, log((N_neg+1)/(N_pos+1))]`;
  * `max_prediction >= max_abs_prediction_threshold`(**默认 30**)时整体缩放 F(缩放不影响无惩罚逻辑回归的结果);
    源码注释:该阈值由 `logit(np.finfo(np.float64).eps) ≈ −36` 近似而来;
  * 用 L-BFGS 最小化 HalfBinomialLoss。
"""
from __future__ import annotations

import math

MAX_ABS_PREDICTION_THRESHOLD = 30.0  # sklearn 源码 `_sigmoid_calibration` 的默认值


# --------------------------------------------------------------- 评估指标
def brier(y, p):
    """Brier 分数:mean((p − y)²),越小越好(同时受校准与判别力影响)。"""
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y)


def brier_decomposition(y, p, n_bins=10):
    """Murphy 分解:BS = REL − RES + UNC。

    REL(可靠性/校准):(1/N)·Σ_b n_b (ō_b − p̄_b)²
    RES(分辨力)      :(1/N)·Σ_b n_b (ō_b − ō)²
    UNC(不确定性)    :ō(1 − ō),只由数据本身的正例率决定
    """
    n = len(y)
    o_bar = sum(y) / n
    bins = {}
    for pi, yi in zip(p, y):
        b = min(int(pi * n_bins), n_bins - 1)
        bins.setdefault(b, []).append((pi, yi))
    rel = res = 0.0
    for members in bins.values():
        nb = len(members)
        p_bar = sum(m[0] for m in members) / nb
        o_b = sum(m[1] for m in members) / nb
        rel += nb * (o_b - p_bar) ** 2
        res += nb * (o_b - o_bar) ** 2
    return rel / n, res / n, o_bar * (1 - o_bar)


def calibration_curve(y, p, n_bins=10):
    """校准曲线:返回 [(该箱平均预测概率, 该箱真实正例比例, 样本数)]。"""
    bins = {}
    for pi, yi in zip(p, y):
        b = min(int(pi * n_bins), n_bins - 1)
        bins.setdefault(b, []).append((pi, yi))
    out = []
    for b in sorted(bins):
        m = bins[b]
        out.append((sum(v[0] for v in m) / len(m), sum(v[1] for v in m) / len(m), len(m)))
    return out


def auc(y, s):
    """处理并列的 ROC-AUC(秩和公式,并列取平均秩)。"""
    order = sorted(range(len(s)), key=lambda i: s[i])
    ranks = [0.0] * len(s)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and s[order[j + 1]] == s[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    npos = sum(y)
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return float("nan")
    return (sum(r for r, yi in zip(ranks, y) if yi == 1) - npos * (npos + 1) / 2) / (npos * nneg)


def accuracy(y, hard):
    return sum(a == b for a, b in zip(y, hard)) / len(y)


# ------------------------------------------------------------ Platt sigmoid
def platt_targets(y):
    """sklearn `_sigmoid_calibration` 的目标平滑:

    T[y>0]  = (N_pos + 1) / (N_pos + 2)
    T[y<=0] = 1 / (N_neg + 2)
    """
    npos = float(sum(1 for v in y if v > 0))
    nneg = float(len(y)) - npos
    return [(npos + 1.0) / (npos + 2.0) if v > 0 else 1.0 / (nneg + 2.0) for v in y]


def platt_init(y):
    """AB0 = [0, log((N_neg+1)/(N_pos+1))]。"""
    npos = float(sum(1 for v in y if v > 0))
    nneg = float(len(y)) - npos
    return 0.0, math.log((nneg + 1.0) / (npos + 1.0))


def sigmoid_scale(f):
    """>= 阈值时按最大值整体缩放(线性模型对特征缩放不变)。"""
    m = max(abs(v) for v in f)
    return (m, 1.0) if m < MAX_ABS_PREDICTION_THRESHOLD else (m, m)


class SigmoidCalibration:
    """Platt scaling:p = 1/(1+exp(A·f+B)),在平滑目标上最小化 logistic loss。"""

    def __init__(self, lr=0.1, iters=2000):
        self.lr, self.iters = lr, iters

    def fit(self, f, y):
        T = platt_targets(y)
        A, B = platt_init(y)
        _m, scale = sigmoid_scale(f)
        F = [v / scale for v in f]
        for _ in range(self.iters):
            gA = gB = 0.0
            for fi, ti in zip(F, T):
                z = A * fi + B
                pr = 1.0 / (1.0 + math.exp(-z)) if z > -700 else 0.0
                d = pr - ti
                gA += d * fi
                gB += d
            A -= self.lr * gA / len(F)
            B -= self.lr * gB / len(F)
        self.A_, self.B_ = A / scale, B        # 缩放还原(sklearn:AB_[0]/scale_constant)
        self.scale_ = scale
        return self

    def predict(self, f):
        return [1.0 / (1.0 + math.exp(-(self.A_ * v + self.B_))) for v in f]


# ------------------------------------------------------------ isotonic(PAVA)
class IsotonicRegression:
    """保序回归(PAVA):最小化 Σ(y−f̂)² 且 f̂ 对 x 单调不降,输出是**阶跃函数**。"""

    def fit(self, x, y):
        pairs = sorted(zip(x, y), key=lambda t: t[0])
        self.xs = [p[0] for p in pairs]
        self.ys = [p[1] for p in pairs]
        blocks = [(v, 1) for v in self.ys]     # (块内均值, 块大小)
        bx = list(self.xs)
        i = 0
        while i + 1 < len(blocks):
            if blocks[i][0] <= blocks[i + 1][0] + 1e-15:
                i += 1
                continue
            # 违反单调 → 合并相邻两块为加权均值
            v1, n1 = blocks[i]
            v2, n2 = blocks[i + 1]
            merged = ((v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2)
            blocks[i:i + 2] = [merged]
            bx[i:i + 2] = [bx[i]]
            while i > 0 and blocks[i - 1][0] > blocks[i][0] + 1e-15:
                v1, n1 = blocks[i - 1]
                v2, n2 = blocks[i]
                blocks[i - 1:i + 1] = [((v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2)]
                bx[i - 1:i + 1] = [bx[i - 1]]
                i -= 1
        self.blocks_ = blocks
        self.bx_ = bx
        return self

    def predict(self, x):
        out = []
        for v in x:
            if v <= self.bx_[0]:
                out.append(self.blocks_[0][0])
                continue
            if v >= self.bx_[-1]:
                out.append(self.blocks_[-1][0])
                continue
            k = 0
            while k + 1 < len(self.bx_) and self.bx_[k + 1] <= v:
                k += 1
            out.append(self.blocks_[k][0])
        return out


# --------------------------------------------------------- temperature scaling
def temperature_scale(logits, T):
    """softmax(z/T);仅实现二分类(z 为正类的 logit 差)。"""
    return [1.0 / (1.0 + math.exp(-(z / T))) for z in logits]


def fit_temperature(logits, y, lo=0.05, hi=20.0, iters=200):
    """三分法搜 T,最小化 log_loss(交叉熵)。"""
    def loss(T):
        eps = 1e-12
        s = 0.0
        for z, yi in zip(logits, y):
            p = min(max(1.0 / (1.0 + math.exp(-(z / T))), eps), 1 - eps)
            s -= yi * math.log(p) + (1 - yi) * math.log(1 - p)
        return s / len(y)
    for _ in range(iters):
        m1 = lo + (hi - lo) / 3
        m2 = hi - (hi - lo) / 3
        if loss(m1) < loss(m2):
            hi = m2
        else:
            lo = m1
    T = (lo + hi) / 2
    return T, loss(T)
