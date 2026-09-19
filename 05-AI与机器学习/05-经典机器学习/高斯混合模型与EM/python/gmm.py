# -*- coding: utf-8 -*-
"""高斯混合模型(GMM)与 EM 算法 —— 纯标准库实现。

权威来源(实际读过,逐条 URL 见同目录 README.md):
- scikit-learn《2.1. Gaussian mixture models》:GMM 是"把 k-means 推广到带上协方差结构";
  EM 先假定随机成分、算每个点由各成分生成的概率,再调参最大化似然,**保证单调收敛到局部最优**;
  不足观测点会让协方差估计发散(奇异性),须"人为正则化协方差";
  四种协方差约束 spherical / diagonal / tied / full;BIC 可选成分数(只在渐近意义下恢复真值)。
- sklearn 源码 `sklearn/mixture/_gaussian_mixture.py`:
  `reg_covar` 默认 **1e-6** 且**只加在对角线上**;
  full 的 M 步 `cov[k] = (resp[:,k]*diff.T) @ diff / nk[k]`;
  diag 的 M 步用 `avg_X2 = (resp.T @ X*X)/nk; avg_means2 = means**2; return avg_X2 - avg_means2 + reg_covar`;
  spherical 取 diag 的逐行均值;
  `bic = -2*score(X)*n + n_params*log(n)`,`aic = -2*score(X)*n + 2*n_params`,
  `n_params = cov_params + mean_params + n_components - 1`。
"""
from __future__ import annotations

import math

REG_COVAR = 1e-6  # sklearn GaussianMixture 的 reg_covar 默认值


from numutil import *   # noqa: F401,F403  数值工具/RNG/k-means,保持 `import gmm` 的原命名空间
from numutil import _rng  # noqa: F401  下划线开头不会被 `import *` 带过来,单独回引

# --------------------------------------------------------------------- GMM
class GaussianMixture:
    """只依赖 stdlib 的 EM 版 GMM,支持 full / tied / diag / spherical 四种协方差。"""

    def __init__(self, n_components=2, covariance_type="full", reg_covar=REG_COVAR,
                 tol=1e-4, max_iter=100, init_params="kmeans", random_state=0):
        if covariance_type not in ("full", "tied", "diag", "spherical"):
            raise ValueError("unknown covariance_type: %s" % covariance_type)
        self.K = n_components
        self.covariance_type = covariance_type
        self.reg_covar = reg_covar
        self.tol = tol
        self.max_iter = max_iter
        self.init_params = init_params
        self.seed = random_state
        self.lower_bound_ = None
        self.loglik_trace_ = []

    # ---------- 初始化 ----------
    def _init(self, X):
        n, d = len(X), len(X[0])
        if self.init_params == "kmeans":
            centers, lab = kmeans(X, self.K, seed=self.seed)
        elif self.init_params == "random_from_data":
            rnd = _rng(self.seed)
            centers = [list(X[int(rnd() * n)]) for _ in range(self.K)]
            lab = None
        else:  # 'random':绕全局均值做小扰动(用户指南:centers 是全体均值附近的小扰动)
            rnd = _rng(self.seed)
            gm = [sum(x[j] for x in X) / n for j in range(d)]
            spread = [max(x[j] for x in X) - min(x[j] for x in X) for j in range(d)]
            centers = [[gm[j] + (rnd() - 0.5) * 0.01 * max(spread[j], 1e-9) for j in range(d)]
                       for _ in range(self.K)]
            lab = None
        self.weights_ = [1.0 / self.K] * self.K
        self.means_ = [list(c) for c in centers]
        if lab is None:
            lab = [min(range(self.K),
                       key=lambda c: sum((a - b) ** 2 for a, b in zip(x, centers[c]))) for x in X]
        self.covariances_ = self._cov_from_labels(X, lab)

    def _cov_from_labels(self, X, lab):
        n, d = len(X), len(X[0])
        t = self.covariance_type
        covs = []
        for k in range(self.K):
            mem = [X[i] for i in range(n) if lab[i] == k]
            if not mem:
                mem = [list(X[0])]
            mu = [sum(m[j] for m in mem) / len(mem) for j in range(d)]
            diff = [[x[j] - mu[j] for j in range(d)] for x in mem]
            c = [[sum(di[i] * di[j] for di in diff) / len(diff) for j in range(d)] for i in range(d)]
            for j in range(d):
                c[j][j] += self.reg_covar
            covs.append(c)
        if t == "full":
            return covs
        if t == "tied":  # 共享:按样本数加权平均后 + reg_covar 对角
            w = [sum(1 for l in lab if l == k) / n for k in range(self.K)]
            sh = [[sum(w[k] * covs[k][i][j] for k in range(self.K)) for j in range(d)] for i in range(d)]
            return sh
        if t == "diag":
            return [[covs[k][j][j] for j in range(d)] for k in range(self.K)]
        return [sum(covs[k][j][j] for j in range(d)) / d for k in range(self.K)]

    # ---------- 密度 ----------
    def _prepare(self):
        """为当前协方差预算 Cholesky 与 log|Σ|,避免每轮重复分解。"""
        t, d = self.covariance_type, len(self.means_[0])
        if t in ("full", "tied"):
            mats = self.covariances_ if t == "full" else [self.covariances_] * self.K
            self._chol = [cholesky(m) for m in mats]
            self._logdet = [logdet_chol(L) for L in self._chol]
        elif t == "diag":
            self._chol = self._logdet = None
            self._logdet_d = [sum(math.log(max(v, 1e-300)) for v in c) for c in self.covariances_]
        else:
            self._chol = self._logdet = None
            self._logdet_d = [d * math.log(max(v, 1e-300)) for v in self.covariances_]

    def _log_gauss(self, k, x):
        d = len(x)
        t = self.covariance_type
        diff = [x[j] - self.means_[k][j] for j in range(d)]
        if t in ("full", "tied"):
            mh = mahal_sq_chol(self._chol[k], diff)
            ld = self._logdet[k]
        else:
            var = self.covariances_[k]
            if t == "diag":
                mh = sum(diff[j] * diff[j] / var[j] for j in range(d))
            else:
                mh = sum(v * v for v in diff) / var
            ld = self._logdet_d[k]
        return -0.5 * (d * math.log(2 * math.pi) + ld + mh)

    def _estimate_log_prob(self, X):
        return [[math.log(max(self.weights_[k], 1e-300)) + self._log_gauss(k, x)
                 for k in range(self.K)] for x in X]

    def _e_step(self, X):
        """E 步:log-responsibility = log(π_k·N_k(x)) − logsumexp,天然数值稳定。"""
        lp = self._estimate_log_prob(X)
        log_prob_norm = [logsumexp(r) for r in lp]
        log_resp = [[r[k] - log_prob_norm[i] for k in range(self.K)] for i, r in enumerate(lp)]
        return log_resp, sum(log_prob_norm)

    def _m_step(self, X, log_resp):
        n, d = len(X), len(X[0])
        t = self.covariance_type
        resp = [[math.exp(lr[k]) for k in range(self.K)] for lr in log_resp]
        nk = [sum(r[k] for r in resp) for k in range(self.K)]
        self.weights_ = [max(nk[k], 1e-300) / n for k in range(self.K)]
        self.means_ = [[sum(resp[i][k] * X[i][j] for i in range(n)) / max(nk[k], 1e-300)
                        for j in range(d)] for k in range(self.K)]
        if t == "full":
            self.covariances_ = []
            for k in range(self.K):
                c = [[sum(resp[i][k] * (X[i][a] - self.means_[k][a]) * (X[i][b] - self.means_[k][b])
                          for i in range(n)) / max(nk[k], 1e-300) for b in range(d)] for a in range(d)]
                for j in range(d):
                    c[j][j] += self.reg_covar  # reg_covar 只加对角
                self.covariances_.append(c)
        elif t == "tied":
            c = [[sum(resp[i][k] * (X[i][a] - self.means_[k][a]) * (X[i][b] - self.means_[k][b])
                      for i in range(n) for k in range(self.K)) / n for b in range(d)] for a in range(d)]
            for j in range(d):
                c[j][j] += self.reg_covar
            self.covariances_ = c
        elif t == "diag":
            self.covariances_ = [[sum(resp[i][k] * X[i][j] * X[i][j] for i in range(n)) / max(nk[k], 1e-300)
                                  - self.means_[k][j] ** 2 + self.reg_covar for j in range(d)]
                                 for k in range(self.K)]
        else:  # spherical = diag 的逐行均值
            dgs = [[sum(resp[i][k] * X[i][j] * X[i][j] for i in range(n)) / max(nk[k], 1e-300)
                    - self.means_[k][j] ** 2 + self.reg_covar for j in range(d)] for k in range(self.K)]
            self.covariances_ = [sum(v for v in row) / d for row in dgs]
        self._nk_ = nk

    # ---------- 训练 ----------
    def fit(self, X):
        self._init(X)
        self._prepare()
        self.loglik_trace_ = []
        prev = None
        for _ in range(self.max_iter):
            log_resp, ll = self._e_step(X)
            self.loglik_trace_.append(ll)
            self._m_step(X, log_resp)
            self._prepare()
            self.lower_bound_ = ll
            if prev is not None and abs(ll - prev) < self.tol * abs(prev if prev else 1.0):
                break
            prev = ll
        return self

    # ---------- 推断 ----------
    def predict_proba(self, X):
        self._prepare()
        lp = self._estimate_log_prob(X)
        return [[math.exp(l[k] - logsumexp(l)) for k in range(self.K)] for l in lp]

    def predict(self, X):
        return [max(range(self.K), key=lambda k: r[k]) for r in self.predict_proba(X)]

    def score(self, X):
        """平均对数似然(sklearn 的 score 语义),BIC/AIC 里乘样本数还原成总对数似然。"""
        self._prepare()
        return sum(logsumexp(l) for l in self._estimate_log_prob(X)) / len(X)

    # ---------- 模型选择 ----------
    def _n_parameters(self):
        d = len(self.means_[0])
        K = self.K
        t = self.covariance_type
        if t == "full":
            cov_p = K * d * (d + 1) / 2.0
        elif t == "diag":
            cov_p = K * d
        elif t == "tied":
            cov_p = d * (d + 1) / 2.0
        else:
            cov_p = K
        return int(cov_p + d * K + K - 1)

    def bic(self, X):
        return -2 * self.score(X) * len(X) + self._n_parameters() * math.log(len(X))

    def aic(self, X):
        return -2 * self.score(X) * len(X) + 2 * self._n_parameters()
