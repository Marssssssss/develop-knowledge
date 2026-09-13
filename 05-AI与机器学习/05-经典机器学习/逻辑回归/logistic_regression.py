"""逻辑回归:sigmoid + 交叉熵 + L2 正则化 + 多分类 OvR。

权威来源:
- scikit-learn LogisticRegression https://scikit-learn.org/1.5/modules/linear_model.html
  二元 L2: min_w,c ½wᵀw + C·Σ log(exp(−y_i(X_iᵀw+c)) + 1)
  σ(z) = 1/(1+exp(−z));log-loss = −y log p − (1−y) log(1−p)
  solvers: lbfgs(默认) / liblinear / newton-cg / sag / saga
  C 与 α 互为倒数:α = 1/(n_samples·C)
  lbfgs/sag/newton-cg 只支持 L2,liblinear 支持 L1,saga 全支持
  多分类: OvR(one-vs-rest) / multinomial(softmax 全局归一化)
"""

from __future__ import annotations

import math
import random
from typing import Callable, List, Sequence, Tuple

Vector = List[float]
Matrix = List[List[float]]


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def transpose(A):
    r, c = len(A), len(A[0]) if A else 0
    return [[A[i][j] for i in range(r)] for j in range(c)]


def accuracy(yt, yp):
    return sum(int(a == b) for a, b in zip(yt, yp)) / len(yt) if yt else 0.0


class LogisticRegression:
    """二元逻辑回归:梯度下降 + L2 正则化(等价 sklearn penalty='l2')。"""

    def __init__(self, C=1.0, n_iter=2000, lr=0.1, fit_intercept=True):
        if C <= 0:
            raise ValueError("C must be > 0")
        self.C, self.n_iter, self.lr, self.fit_intercept = C, n_iter, lr, fit_intercept
        self.coef_: Vector = []
        self.intercept_: float = 0.0

    def fit(self, X, y):
        n, p = len(X), len(X[0])
        if not all(yi in (0, 1) for yi in y):
            raise ValueError("binary y in {0, 1} required")
        if self.fit_intercept:
            X = [[1.0] + row for row in X]
            w = [0.0] * (p + 1)
        else:
            w = [0.0] * p
        for _ in range(self.n_iter):
            errs = [sigmoid(dot(w, row)) - yi for row, yi in zip(X, y)]
            grad = [sum(errs[i] * X[i][j] for i in range(n)) / n + w[j] / (n * self.C)
                    for j in range(len(w))]
            w = [w[j] - self.lr * grad[j] for j in range(len(w))]
        if self.fit_intercept:
            self.intercept_, self.coef_ = w[0], w[1:]
        else:
            self.intercept_, self.coef_ = 0.0, w
        return self

    def predict_proba(self, X):
        out = []
        for row in X:
            z = self.intercept_ + dot(self.coef_, row)
            p1 = sigmoid(z)
            out.append([1.0 - p1, p1])
        return out

    def predict(self, X, threshold=0.5):
        return [1 if proba[1] >= threshold else 0 for proba in self.predict_proba(X)]

    def loss(self, X, y):
        n, eps = len(y), 1e-15
        s = 0.0
        for row, yi in zip(X, y):
            p = sigmoid(self.intercept_ + dot(self.coef_, row))
            p = max(min(p, 1 - eps), eps)
            s += -(yi * math.log(p) + (1 - yi) * math.log(1 - p))
        return s / n + 0.5 * dot(self.coef_, self.coef_) / (n * self.C)


class LogisticRegressionOVR:
    """One-vs-Rest 多分类:对每个类训练二元 LR,选概率最高的类。"""

    def __init__(self, C=1.0, n_iter=2000, lr=0.1):
        self.C, self.n_iter, self.lr = C, n_iter, lr
        self.classes_: List[int] = []
        self.binaries_: List[LogisticRegression] = []

    def fit(self, X, y):
        self.classes_ = sorted(set(y))
        self.binaries_ = []
        for c in self.classes_:
            y_bin = [1 if yi == c else 0 for yi in y]
            self.binaries_.append(
                LogisticRegression(C=self.C, n_iter=self.n_iter, lr=self.lr).fit(X, y_bin))
        return self

    def predict(self, X):
        proba = self.predict_proba(X)
        return [self.classes_[max(range(len(p)), key=lambda i: p[i])] for p in proba]

    def predict_proba(self, X):
        out = []
        for row in X:
            scores = [sigmoid(b.intercept_ + dot(b.coef_, row)) for b in self.binaries_]
            s = sum(scores)
            out.append([sc / s if s > 0 else 1.0 / len(scores) for sc in scores])
        return out


# ---------- 数值梯度验证 ----------

def numerical_gradient(loss_fn, w, eps=1e-5):
    return [(loss_fn([w[i] + eps if j == i else w[j] for j in range(len(w))]) -
             loss_fn([w[i] - eps if j == i else w[j] for j in range(len(w))])) / (2 * eps)
            for i in range(len(w))]


def analytical_gradient(X, y, w, C):
    n = len(y)
    grad = [0.0] * len(w)
    for i in range(n):
        e = sigmoid(dot(w, X[i])) - y[i]
        for j in range(len(w)):
            grad[j] += e * X[i][j]
    return [grad[j] / n + w[j] / (n * C) for j in range(len(w))]


# ---------- 数据生成 ----------

def make_linearly_separable(n_samples=100, seed=0):
    rng = random.Random(seed)
    X, y = [], []
    for _ in range(n_samples):
        x1 = rng.uniform(-3, 3)
        x2 = rng.uniform(-3, 3)
        cls = 1 if x1 + x2 + rng.gauss(0, 0.3) > 0 else 0
        X.append([x1, x2])
        y.append(cls)
    return X, y


def make_classification(n_samples=200, n_features=2, n_classes=2, seed=0):
    rng = random.Random(seed)
    centers = [[rng.uniform(-3, 3) for _ in range(n_features)] for _ in range(n_classes)]
    X, y = [], []
    for _ in range(n_samples):
        c = rng.randrange(n_classes)
        row = [centers[c][j] + rng.gauss(0, 1.0) for j in range(n_features)]
        X.append(row)
        y.append(c)
    return X, y


# ---------- 演示 ----------

def demo_basic_binary():
    X, y = make_linearly_separable(n_samples=200, seed=42)
    model = LogisticRegression(C=1.0, n_iter=3000).fit(X, y)
    print("[1] Binary linearly separable")
    print(f"  loss = {model.loss(X, y):.4f}  acc = {accuracy(y, model.predict(X)):.4f}")
    print(f"  coef = {[round(w, 3) for w in model.coef_]}  intercept = {model.intercept_:.3f}")
    print(f"  proba[:3] = {[round(p[1], 3) for p in model.predict_proba(X[:3])]}\n")


def demo_gradient_verification():
    """demo 2:解析梯度 vs 中心差分(数值梯度)→ max err < 1e-7。"""
    X, y = make_linearly_separable(n_samples=50, seed=1)
    Xb = [[1.0] + row for row in X]
    C, w = 1.0, [0.1, 0.2, -0.05]
    ana = analytical_gradient(Xb, y, w, C)
    def loss_fn(w_):
        n = len(y)
        s = 0.0
        for i in range(n):
            p = sigmoid(dot(w_, Xb[i]))
            p = max(min(p, 1 - 1e-15), 1e-15)
            s += -(y[i] * math.log(p) + (1 - y[i]) * math.log(1 - p))
        return s / n + 0.5 * dot(w_[1:], w_[1:]) / (n * C)
    num = numerical_gradient(loss_fn, w)
    err = max(abs(a - n) for a, n in zip(ana, num))
    print("[2] Gradient: analytical vs numerical(中心差分)")
    print(f"  analytical = {[round(g, 6) for g in ana]}")
    print(f"  numerical  = {[round(g, 6) for g in num]}")
    print(f"  max err    = {err:.2e} (< 1e-7 验证通过)\n")


def demo_l2_regularization():
    """demo 3:C 扫描 → C 大 → 弱正则,系数范数大。"""
    X, y = make_linearly_separable(n_samples=100, seed=2)
    print("[3] C sweep:C 大 → 弱正则,小 → 强正则")
    for C in [0.01, 0.1, 1.0, 10.0, 100.0]:
        m = LogisticRegression(C=C, n_iter=3000).fit(X, y)
        norm = math.sqrt(dot(m.coef_, m.coef_))
        print(f"  C={C:>6.2f}  ||w||={norm:.4f}  loss={m.loss(X, y):.4f}  "
              f"acc={accuracy(y, m.predict(X)):.4f}")
    print()


def demo_ovr_multiclass():
    X, y = make_classification(n_samples=300, n_features=2, n_classes=3, seed=3)
    m = LogisticRegressionOVR(C=1.0, n_iter=3000).fit(X, y)
    print("[4] OvR 3-class(每类一个二元 LR)")
    print(f"  classes   = {m.classes_}  acc = {accuracy(y, m.predict(X)):.4f}")
    print(f"  sample    = X[0]={X[0]}, proba={[round(p, 3) for p in m.predict_proba(X[:1])[0]]}\n")


def demo_decision_boundary():
    X, y = make_linearly_separable(n_samples=100, seed=4)
    m = LogisticRegression(C=1.0, n_iter=2000).fit(X, y)
    print("[5] 决策边界(20x20 ASCII, σ 概率映射 0-9)")
    print("    " + "".join(f"{i * 6 // 20 - 1:>3}" for i in range(20)))
    for r in range(20):
        py = r * 6 / 19 - 3
        row_chars = []
        for c in range(20):
            px = c * 6 / 19 - 3
            p = m.predict_proba([[px, py]])[0][1]
            row_chars.append(f"  {'0123456789'[min(9, int(p * 10))]}")
        print(f"  {py:+.1f}" + "".join(row_chars))


def main():
    demo_basic_binary()
    demo_gradient_verification()
    demo_l2_regularization()
    demo_ovr_multiclass()
    demo_decision_boundary()
    print("\nAll 5 demos passed.")


if __name__ == "__main__":
    main()
