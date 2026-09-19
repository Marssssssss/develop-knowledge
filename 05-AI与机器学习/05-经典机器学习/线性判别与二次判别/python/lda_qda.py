# -*- coding: utf-8 -*-
"""线性判别分析(LDA)与二次判别分析(QDA)—— 纯标准库实现。

权威来源(实际读过,逐条 URL 见同目录 README.md):
- scikit-learn《1.2. Linear and Quadratic Discriminant Analysis》:
  * 二者都由**类条件高斯** P(X|y=k) + 贝叶斯法则导出,LDA 假设各类的 Σ 相同(⇒ 线性边界),
    QDA 不做该假设(⇒ 二次边界);
  * log-posterior 的两种写法:
    QDA `−½log|Σ_k| −½(x−μ_k)ᵀΣ_k⁻¹(x−μ_k) + log P(y=k)`;
    LDA 化简为 `ω_kᵀx + ω_k0`,其中 **ω_k = Σ⁻¹μ_k**、**ω_k0 = −½μ_kᵀΣ⁻¹μ_k + log P(y=k)**,
    正好对应 `coef_` 与 `intercept_`;
  * LDA 的项 (x−μ_k)ᵀΣ⁻¹(x−μ_k) 是**马氏距离**;
  * 降维:K 个均值至多张成 **K−1** 维仿射子空间,故 `transform` 的输出维度 < K;
    进一步压缩到 L 维 = 对"白化后的类均值"做一次 PCA;
  * shrinkage:γ=0 用经验协方差,γ=1 用"方差组成的对角阵",'auto' 走 Ledoit-Wolf 引理;
    **shrinkage 只能配 lsqr / eigen solver**;
  * QDA 若假设协方差为对角 ⇒ 等价于 `GaussianNB`。
- scikit-learn 源码 `sklearn/covariance/_shrunk_covariance.py`:
  `shrunk_cov = (1−γ)·emp_cov + γ·(tr(Σ)/p)·I` —— 注意靶心是**平均方差 × 单位阵**,
  不是"各维方差组成的对角阵"(用户指南那一句措辞偏松,自检 E 段按源码口径钉住)。
"""
from __future__ import annotations

import math


# --------------------------------------------------------------- 线性代数
def cholesky(A):
    n = len(A)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                d = A[i][i] - s
                if d <= 1e-300:
                    raise ValueError("not positive definite")
                L[i][i] = math.sqrt(d)
            else:
                L[i][j] = (A[i][j] - s) / L[j][j]
    return L


def chol_solve(L, b):
    """解 (L·Lᵀ)x = b:前代 + 回代。"""
    n = len(b)
    y = [0.0] * n
    for i in range(n):
        y[i] = (b[i] - sum(L[i][k] * y[k] for k in range(i))) / L[i][i]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (y[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


def logdet_chol(L):
    return 2.0 * sum(math.log(L[i][i]) for i in range(len(L)))


def matvec(M, v):
    return [sum(M[i][j] * v[j] for j in range(len(v))) for i in range(len(M))]


def shrunk_covariance(cov, gamma):
    """sklearn `shrunk_covariance` 的口径:(1−γ)Σ + γ·(tr(Σ)/p)·I。"""
    p = len(cov)
    mu = sum(cov[i][i] for i in range(p)) / p
    return [[(1 - gamma) * cov[i][j] + (gamma * mu if i == j else 0.0)
             for j in range(p)] for i in range(p)]


def power_eig(M, iters=500, seed=1):
    """幂迭代求对称矩阵的主特征对(用于白化后类均值的 PCA)。"""
    n = len(M)
    st = seed
    v = []
    for _ in range(n):
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        v.append((st >> 11) / float(1 << 53) - 0.5)
    nv = math.sqrt(sum(x * x for x in v))
    v = [x / nv for x in v]
    for _ in range(iters):
        w = matvec(M, v)
        nw = math.sqrt(sum(x * x for x in w))
        if nw < 1e-300:
            return 0.0, v
        v = [x / nw for x in w]
    w = matvec(M, v)
    return sum(v[i] * w[i] for i in range(n)), v


def deflate(M, eigval, eigvec):
    """收缩:M ← M − λ·vvᵀ,便于继续求次大特征对。"""
    n = len(M)
    return [[M[i][j] - eigval * eigvec[i] * eigvec[j] for j in range(n)] for i in range(n)]


# ------------------------------------------------------------------- 模型
class LDA:
    """共享协方差的判别分析:线性决策面。"""

    def __init__(self, shrinkage=0.0, n_components=None):
        self.shrinkage = shrinkage
        self.n_components = n_components

    def fit(self, X, y):
        self.classes_ = sorted(set(y))
        n, d = len(X), len(X[0])
        idx = {c: [i for i in range(n) if y[i] == c] for c in self.classes_}
        self.priors_ = [len(idx[c]) / n for c in self.classes_]
        self.means_ = [[sum(X[i][j] for i in idx[c]) / len(idx[c]) for j in range(d)]
                       for c in self.classes_]
        # 池化协方差:按类频率(即默认 priors)加权
        cov = [[0.0] * d for _ in range(d)]
        for k, c in enumerate(self.classes_):
            mem = idx[c]
            mu = self.means_[k]
            for a in range(d):
                for b in range(d):
                    cov[a][b] += self.priors_[k] * sum(
                        (X[i][a] - mu[a]) * (X[i][b] - mu[b]) for i in mem) / len(mem)
        if self.shrinkage:
            cov = shrunk_covariance(cov, self.shrinkage)
        self.covariance_ = cov
        self._L = cholesky(cov)
        self._fit_transform(X, y, idx, d)
        return self

    def _fit_transform(self, X, y, idx, d):
        """transform 的方向 = 对"白化后的类均值"做 PCA(L ≤ K−1)。"""
        K = len(self.classes_)
        L = self._L
        # 白化:x* = L⁻¹x;类均值同样变换
        mus = [chol_solve(L, mu) for mu in self.means_]
        gm = [sum(self.priors_[k] * mus[k][j] for k in range(K)) for j in range(d)]
        cen = [[mus[k][j] - gm[j] for j in range(d)] for k in range(K)]
        # 协方差(带 prior 权重,非归一化常数不影响方向)
        S = [[sum(self.priors_[k] * cen[k][a] * cen[k][b] for k in range(K))
              for b in range(d)] for a in range(d)]
        comps, vals, M = [], [], S
        for _ in range(min(self.n_components or (K - 1), K - 1, d)):
            lam, v = power_eig(M)
            if lam <= 1e-12:
                break
            comps.append(v)
            vals.append(lam)
            M = deflate(M, lam, v)
        self.scalings_ = comps                       # 白化空间里的方向
        self.explained_variance_ = vals
        self._gm = gm
        # 原始空间的方向:w = (Lᵀ)⁻¹ v  (x* = L⁻¹x ⇒ vᵀx* = vᵀL⁻¹x = ((L⁻¹)ᵀv)ᵀx)
        dirs = [chol_solve_transpose(L, v) for v in comps]
        self.directions_ = []
        for w in dirs:                                  # 单位化,便于比较方向
            nw = math.sqrt(sum(t * t for t in w))
            self.directions_.append([t / nw for t in w])

    def decision_function(self, X):
        """ω_kᵀx + ω_k0(sklearn:coef_ 与 intercept_)。"""
        out = []
        for x in X:
            row = []
            for k in range(len(self.classes_)):
                Sk = chol_solve(self._L, self.means_[k])         # Σ⁻¹μ_k
                quad = sum(self.means_[k][j] * Sk[j] for j in range(len(x)))
                row.append(sum(Sk[j] * x[j] for j in range(len(x)))
                           - 0.5 * quad + math.log(self.priors_[k]))
            out.append(row)
        return out

    def predict(self, X):
        return [max(range(len(self.classes_)),
                    key=lambda k: r[k]) for r in self.decision_function(X)]

    def transform(self, X, n_components=None):
        m = n_components or len(self.scalings_)
        return [[sum(v[j] * (x[j]) for j in range(len(x))) for v in self.directions_[:m]]
                for x in X]


def chol_solve_transpose(L, b):
    """解 Lᵀx = b(回代)。"""
    n = len(b)
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = (b[i] - sum(L[k][i] * x[k] for k in range(i + 1, n))) / L[i][i]
    return x


class QDA:
    """每类各一个协方差:二次决策面。"""

    def __init__(self, diagonal=False, reg=1e-6):
        self.diagonal = diagonal
        self.reg = reg

    def fit(self, X, y):
        self.classes_ = sorted(set(y))
        n, d = len(X), len(X[0])
        idx = {c: [i for i in range(n) if y[i] == c] for c in self.classes_}
        self.priors_ = [len(idx[c]) / n for c in self.classes_]
        self.means_ = [[sum(X[i][j] for i in idx[c]) / len(idx[c]) for j in range(d)]
                       for c in self.classes_]
        self.cov_ = []
        for k, c in enumerate(self.classes_):
            mem = idx[c]
            mu = self.means_[k]
            cm = [[sum((X[i][a] - mu[a]) * (X[i][b] - mu[b]) for i in mem) / len(mem)
                   for b in range(d)] for a in range(d)]
            if self.diagonal:                        # 只保留对角 ⇒ 条件独立 ⇒ GaussianNB
                cm = [[cm[a][b] if a == b else 0.0 for b in range(d)] for a in range(d)]
            for a in range(d):
                cm[a][a] += self.reg
            self.cov_.append(cm)
        return self

    def log_posterior(self, X):
        """论文/文档里的原始形式:−½log|Σ_k| −½ 马氏距离 + log π_k(未归一化)。"""
        d = len(X[0])
        out = []
        for x in X:
            row = []
            for k in range(len(self.classes_)):
                L = cholesky(self.cov_[k])
                diff = [x[j] - self.means_[k][j] for j in range(d)]
                y = chol_solve(L, diff)
                mh = sum(diff[j] * y[j] for j in range(d))
                row.append(-0.5 * logdet_chol(L) - 0.5 * mh + math.log(self.priors_[k]))
            out.append(row)
        return out

    def predict(self, X):
        return [max(range(len(self.classes_)), key=lambda k: r[k])
                for r in self.log_posterior(X)]


class GaussianNB:
    """朴素贝叶斯(条件独立)—— 用于验证"QDA + 对角协方差 ≡ GaussianNB"。"""

    def fit(self, X, y):
        self.classes_ = sorted(set(y))
        n, d = len(X), len(X[0])
        idx = {c: [i for i in range(n) if y[i] == c] for c in self.classes_}
        self.priors_ = [len(idx[c]) / n for c in self.classes_]
        self.means_ = [[sum(X[i][j] for i in idx[c]) / len(idx[c]) for j in range(d)]
                       for c in self.classes_]
        self.vars_ = [[sum((X[i][j] - self.means_[k][j]) ** 2 for i in idx[c]) / len(idx[c])
                       + 1e-6 for j in range(d)] for k, c in enumerate(self.classes_)]
        return self

    def joint_log_likelihood(self, X):
        d = len(X[0])
        out = []
        for x in X:
            row = []
            for k in range(len(self.classes_)):
                s = math.log(self.priors_[k])
                for j in range(d):
                    v = self.vars_[k][j]
                    s += -0.5 * math.log(2 * math.pi * v) - (x[j] - self.means_[k][j]) ** 2 / (2 * v)
                row.append(s)
            out.append(row)
        return out

    def predict(self, X):
        return [max(range(len(self.classes_)), key=lambda k: r[k])
                for r in self.joint_log_likelihood(X)]
