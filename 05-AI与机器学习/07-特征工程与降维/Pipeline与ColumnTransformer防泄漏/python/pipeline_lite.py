#!/usr/bin/env python3
"""极简 Pipeline / ColumnTransformer:把 sklearn 的契约实现到"够暴露泄漏"的程度。

sklearn 官方文档给 Pipeline 的三个用途里,第三个就是 **Safety**:
"Pipelines help avoid leaking statistics from your test data into the trained model in
 cross-validation, by ensuring that the same samples are used to train the transformers and
 predictors."(§8.1.1)

这里的实现遵守同一条契约:
  - 所有变换器都是 `fit(X, y)` → 只在训练数据上学习统计量;`transform(X)` 用学到的统计量
  - `Pipeline.fit_transform` 逐级调用下一级的 `fit_transform`,只有**最后一级**用 `fit`
  - `ColumnTransformer` 按列路由,支持 `remainder='drop' | 'passthrough' | 变换器`
  - `cross_val_score` 在每一折内部重新 `fit` 整个 pipeline(这是"安全"的全部秘密)
"""

import math
import random


class StandardScaler:
    """按列标准化:`(x - μ) / σ`,μ/σ 只在 fit 数据上算。"""

    def fit(self, X, y=None):
        n = len(X)
        self.mu = [math.fsum(row[j] for row in X) / n for j in range(len(X[0]))]
        self.sd = [math.sqrt(math.fsum((row[j] - self.mu[j]) ** 2 for row in X) / (n - 1))
                   or 1.0 for j in range(len(X[0]))]
        return self

    def transform(self, X):
        return [[(row[j] - self.mu[j]) / self.sd[j] for j in range(len(row))] for row in X]

    def fit_transform(self, X, y=None):
        return self.fit(X, y).transform(X)


class SelectKBest:
    """ANOVA F 值特征选择:score_j 只在 fit 数据上统计(counts/means 全部来自 fit 集)。"""

    def __init__(self, k=25):
        self.k = k

    def fit(self, X, y):
        n, p = len(X), len(X[0])
        yb = math.fsum(y) / n
        scores = []
        for j in range(p):
            a = [X[i][j] for i in range(n) if y[i] == 1]
            b = [X[i][j] for i in range(n) if y[i] == 0]
            if not a or not b:
                scores.append(0.0)
                continue
            scores.append(len(a) * (math.fsum(a) / len(a) - yb) ** 2
                          + len(b) * (math.fsum(b) / len(b) - yb) ** 2)
        self.idx = sorted(range(p), key=lambda j: -scores[j])[:self.k]
        return self

    def transform(self, X):
        return [[row[j] for j in self.idx] for row in X]

    def fit_transform(self, X, y=None):
        return self.fit(X, y).transform(X)


class OneHot:
    """多列 one-hot:对输入的**每一列**分别统计类别表,再首尾拼接。

    类别表只在 fit 数据上收集 ⇒ 未见图层类别在 transform 时退化为全 0
    (等价于 sklearn 的 `handle_unknown='ignore'`),且输出宽度永远等于 fit 时的宽度。
    """

    def __init__(self):
        self.cols = []

    def fit(self, X, y=None):
        self.cols = [sorted({row[j] for row in X}, key=repr) for j in range(len(X[0]))]
        return self

    def transform(self, X):
        out = []
        for row in X:
            r = []
            for j, cats in enumerate(self.cols):
                r.extend(1.0 if row[j] == c else 0.0 for c in cats)
            out.append(r)
        return out

    def fit_transform(self, X, y=None):
        return self.fit(X, y).transform(X)


class LogReg:
    """L2 逻辑回归(全批量梯度下降)。最后一个"估计器",不是变换器。"""

    def __init__(self, epochs=300, lr=0.3, l2=0.0):
        self.epochs, self.lr, self.l2 = epochs, lr, l2

    def fit(self, X, y):
        n, p = len(X), len(X[0]) + 1
        w = [0.0] * p
        for _ in range(self.epochs):
            g = [0.0] * p
            for i in range(n):
                z = w[0] + math.fsum(w[j + 1] * X[i][j] for j in range(p - 1))
                err = _sigmoid(z) - y[i]
                g[0] += err
                for j in range(p - 1):
                    g[j + 1] += err * X[i][j]
            for j in range(p):
                w[j] -= self.lr * (g[j] / n + self.l2 * w[j])
        self.w = w
        return self

    def predict(self, X):
        return [1 if self.decision_function(row) > 0 else 0 for row in X]

    def decision_function(self, row):
        return self.w[0] + math.fsum(self.w[j + 1] * row[j] for j in range(len(row)))


def _sigmoid(z):
    if z < -35.0:
        return 0.0
    if z > 35.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


class Pipeline:
    """除最后一级外都必须是变换器;最后一级用 fit(而不是 fit_transform)。"""

    def __init__(self, steps):
        self.steps = steps

    def fit(self, X, y):
        Xt = X
        for name, est in self.steps[:-1]:
            Xt = est.fit_transform(Xt, y)
        self.steps[-1][1].fit(Xt, y)
        self._n_features = len(Xt[0]) if Xt else 0
        return self

    def transform(self, X):
        Xt = X
        for _, est in self.steps[:-1]:
            Xt = est.transform(Xt)
        return Xt

    def predict(self, X):
        return self.steps[-1][1].predict(self.transform(X))


class ColumnTransformer:
    """按列路由:每个 (name, transformer, columns) 各自 fit/transform,再横向拼接。

    `remainder`:`'drop'`(默认)/ `'passthrough'` / 一个变换器 —— 与官方语义一致。
    """

    def __init__(self, transformers, remainder="drop"):
        self.transformers, self.remainder = transformers, remainder

    def fit(self, X, y):
        self.fitted = []
        used = set()
        for name, tr, cols in self.transformers:
            tr.fit([[row[j] for j in cols] for row in X], y)
            self.fitted.append((name, tr, list(cols)))
            used.update(cols)
        self.rest = [j for j in range(len(X[0])) if j not in used]
        if self.remainder != "drop" and self.rest and self.remainder != "passthrough":
            self.remainder.fit([[row[j] for j in self.rest] for row in X], y)
        return self

    def transform(self, X):
        parts = [tr.transform([[row[j] for j in cols] for row in X])
                 for _, tr, cols in self.fitted]
        if self.remainder != "drop" and self.rest:
            sub = [[row[j] for j in self.rest] for row in X]
            parts.append(sub if self.remainder == "passthrough" else self.remainder.transform(sub))
        if not parts:
            return []
        out = []
        for i in range(len(X)):
            row = []
            for part in parts:      # 横向拼接:各路由在同一行上的输出首尾相连
                row.extend(part[i])
            out.append(row)
        return out

    def fit_transform(self, X, y=None):
        return self.fit(X, y).transform(X)


def accuracy(estimator, X, y):
    pred = estimator.predict(X)
    return sum(1 for p, t in zip(pred, y) if p == t) / len(y)


def cross_val_score(make_estimator, X, y, n_folds=5, seed=0):
    """每一折都在**该折的训练部分**重新 fit 整个 estimator —— pipeline 安全的来源。"""
    n = len(y)
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    scores = []
    for k in range(n_folds):
        lo, hi = k * n // n_folds, (k + 1) * n // n_folds
        val = set(idx[lo:hi])
        tr = [i for i in range(n) if i not in val]
        va = [i for i in idx[lo:hi]]
        est = make_estimator()
        est.fit([X[i] for i in tr], [y[i] for i in tr])
        scores.append(accuracy(est, [X[i] for i in va], [y[i] for i in va]))
    return scores
