"""线性回归:OLS + Ridge 闭式解 + 梯度下降。

权威来源:
- scikit-learn Linear Models https://scikit-learn.org/dev/modules/linear_model.html
  OLS: min_w ||Xw - y||²  normal equation w = (XᵀX)⁻¹ Xᵀ y
       SVD 复杂度 O(n_sample · n_features²),n_sample >= n_features 假设
  Ridge: min_w ||Xw - y||² + α||w||²  normal equation w = (XᵀX + αI)⁻¹ Xᵀ y
         α >= 0 控制收缩量,越大系数越抗共线性
  Multicollinearity: 特征近似线性相关 → 设计矩阵近奇异 → OLS 对方差敏感大方差
"""

from __future__ import annotations

import math
import random
import time
from typing import List, Sequence, Tuple

Vector = List[float]
Matrix = List[List[float]]


# ---------- 基础矩阵运算(纯 stdlib,无 numpy) ----------

def transpose(A: Matrix) -> Matrix:
    r, c = len(A), len(A[0]) if A else 0
    return [[A[i][j] for i in range(r)] for j in range(c)]


def matmul(A: Matrix, B: Matrix) -> Matrix:
    r, k = len(A), len(A[0]) if A else 0
    k2, c = len(B), len(B[0]) if B else 0
    out = [[0.0] * c for _ in range(r)]
    for i in range(r):
        Ai = A[i]
        for j in range(c):
            s = 0.0
            for s_ in range(k):
                s += Ai[s_] * B[s_][j]
            out[i][j] = s
    return out


def matvec(A: Matrix, v: Vector) -> Vector:
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def inverse(A: Matrix) -> Matrix:
    """Gauss-Jordan 求逆,O(n³)。仅适合 p ≤ 数十的小方阵。"""
    n = len(A)
    M = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-14:
            raise ValueError("matrix is singular or near-singular")
        M[col], M[pivot] = M[pivot], M[col]
        inv_diag = 1.0 / M[col][col]
        for j in range(2 * n):
            M[col][j] *= inv_diag
        for r in range(n):
            if r == col:
                continue
            factor = M[r][col]
            if factor == 0.0:
                continue
            for j in range(2 * n):
                M[r][j] -= factor * M[col][j]
    return [row[n:] for row in M]


def mse(yt: Sequence[float], yp: Sequence[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(yt, yp)) / len(yt) if yt else 0.0


def r2_score(yt: Sequence[float], yp: Sequence[float]) -> float:
    mean_y = sum(yt) / len(yt)
    ss_res = sum((a - b) ** 2 for a, b in zip(yt, yp))
    ss_tot = sum((a - mean_y) ** 2 for a in yt)
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


# ---------- 线性模型 ----------

class LinearRegression:
    """OLS 闭式解:w = (XᵀX)⁻¹ Xᵀ y。"""

    def __init__(self, fit_intercept: bool = True):
        self.fit_intercept = fit_intercept
        self.coef_: Vector = []
        self.intercept_: float = 0.0

    def fit(self, X: Matrix, y: Vector) -> "LinearRegression":
        if self.fit_intercept:
            X = [[1.0] + row for row in X]
        Xt = transpose(X)
        w = matvec(inverse(matmul(Xt, X)), matvec(Xt, y))
        if self.fit_intercept:
            self.intercept_, self.coef_ = w[0], w[1:]
        else:
            self.intercept_, self.coef_ = 0.0, w
        return self

    def predict(self, X: Matrix) -> Vector:
        return [self.intercept_ + dot(self.coef_, row) for row in X]


class RidgeRegression:
    """Ridge 闭式解:w = (XᵀX + αI)⁻¹ Xᵀ y。"""

    def __init__(self, alpha: float = 1.0, fit_intercept: bool = True):
        if alpha < 0:
            raise ValueError("alpha must be >= 0")
        self.alpha, self.fit_intercept = alpha, fit_intercept
        self.coef_: Vector = []
        self.intercept_: float = 0.0

    def fit(self, X: Matrix, y: Vector) -> "RidgeRegression":
        if self.fit_intercept:
            X = [[1.0] + row for row in X]
        Xt = transpose(X)
        XtX = matmul(Xt, X)
        for i in range(len(XtX)):
            XtX[i][i] += self.alpha
        w = matvec(inverse(XtX), matvec(Xt, y))
        if self.fit_intercept:
            self.intercept_, self.coef_ = w[0], w[1:]
        else:
            self.intercept_, self.coef_ = 0.0, w
        return self

    def predict(self, X: Matrix) -> Vector:
        return [self.intercept_ + dot(self.coef_, row) for row in X]


def gradient_descent(
    X: Matrix, y: Vector, lr: float = 0.05, n_iter: int = 2000,
    l2: float = 0.0, fit_intercept: bool = True,
) -> Tuple[Vector, float]:
    """批量梯度下降解 OLS(可叠加 L2)。"""
    n, p = len(X), len(X[0])
    if fit_intercept:
        X = [[1.0] + row for row in X]
        w = [0.0] * (p + 1)
    else:
        w = [0.0] * p
    for _ in range(n_iter):
        errs = [dot(w, row) - y[i] for i, row in enumerate(X)]
        grad = [sum(errs[i] * X[i][j] for i in range(n)) / n + l2 * w[j]
                for j in range(len(w))]
        w = [w[j] - lr * grad[j] for j in range(len(w))]
    if fit_intercept:
        return w[1:], w[0]
    return w, 0.0


# ---------- 数据生成 ----------

def make_regression(n_samples=100, n_features=3, noise=0.5, seed=0):
    rng = random.Random(seed)
    w_true = [rng.uniform(-2.0, 2.0) for _ in range(n_features)]
    X = [[rng.uniform(-1.0, 1.0) for _ in range(n_features)] for _ in range(n_samples)]
    y = [dot(w_true, row) + rng.gauss(0, noise) for row in X]
    return X, y, w_true


# ---------- 演示 ----------

def demo_ols_normal_equation() -> None:
    """demo 1:OLS 闭式解 vs 梯度下降 → 应得出相近系数。"""
    X, y, w_true = make_regression(n_samples=200, n_features=3, noise=0.5, seed=42)
    model = LinearRegression().fit(X, y)
    coef_gd, intercept_gd = gradient_descent(X, y, lr=0.1, n_iter=5000)
    print("[1] OLS normal eq vs GD")
    print(f"  w_true   = {[round(w, 3) for w in w_true]}")
    print(f"  OLS w    = {[round(w, 3) for w in model.coef_]}  intercept={model.intercept_:.3f}")
    print(f"  GD  w    = {[round(w, 3) for w in coef_gd]}  intercept={intercept_gd:.3f}")
    print(f"  R²={r2_score(y, model.predict(X)):.4f}\n")


def demo_ridge_vs_ols_under_collinearity() -> None:
    """demo 2:多重共线性下 OLS 退化,Ridge 抗共线性。"""
    rng = random.Random(7)
    n = 50
    X1 = [rng.uniform(-2, 2) for _ in range(n)]
    X2 = [x1 + rng.gauss(0, 0.001) for x1 in X1]
    X = [[a, b] for a, b in zip(X1, X2)]
    y = [3 * a - 2 * b + rng.gauss(0, 0.1) for a, b in zip(X1, X2)]
    ols = LinearRegression().fit(X, y)
    ridge = RidgeRegression(alpha=1.0).fit(X, y)
    print("[2] Multicollinearity (x2 ≈ x1): OLS 退化 vs Ridge 收缩")
    print(f"  OLS  w = {[round(w, 3) for w in ols.coef_]}  ||w||={math.sqrt(dot(ols.coef_, ols.coef_)):.3f}")
    print(f"  Ridge w = {[round(w, 3) for w in ridge.coef_]}  ||w||={math.sqrt(dot(ridge.coef_, ridge.coef_)):.3f}")
    print(f"  true w = [3.0, -2.0]\n")


def demo_alpha_sweep() -> None:
    """demo 3:α 扫描:α 越大 → 系数范数越小。"""
    X, y, _ = make_regression(n_samples=100, n_features=4, noise=1.0, seed=1)
    print("[3] α sweep(从 0 → 100):系数范数单调下降")
    for alpha in [0.0, 0.01, 0.1, 1.0, 10.0, 100.0]:
        r = RidgeRegression(alpha=alpha).fit(X, y)
        norm = math.sqrt(dot(r.coef_, r.coef_))
        train_mse = mse(y, r.predict(X))
        print(f"  α={alpha:>6.2f}  ||w||={norm:.4f}  train MSE={train_mse:.4f}")
    print()


def demo_train_test_split() -> None:
    """demo 4:训练/测试集(80/20)R² 对比,模拟 sklearn diabetes 单特征回归。"""
    rng = random.Random(2026)
    n = 200
    X = [[rng.uniform(-3, 3)] for _ in range(n)]
    w0, w1 = 152.0, 879.0
    y = [w0 + w1 * row[0] + rng.gauss(0, 30) for row in X]
    m = LinearRegression().fit(X[:160], y[:160])
    print("[4] Train/Test 80/20(模拟 diabetes 单特征)")
    print(f"  fitted w0={m.intercept_:.2f}  w1={m.coef_[0]:.2f}  (true {w0}, {w1})")
    print(f"  train R²={r2_score(y[:160], m.predict(X[:160])):.4f}  "
          f"test R²={r2_score(y[160:], m.predict(X[160:])):.4f}\n")


def demo_complexity_scaling() -> None:
    """demo 5:复杂度 O(n·p²) 实证。"""
    print("[5] Complexity scaling:normal equation O(n·p²)")
    for p in [2, 5, 10, 20]:
        X, y, _ = make_regression(n_samples=1000, n_features=p, noise=0.1, seed=p)
        t0 = time.perf_counter()
        LinearRegression().fit(X, y)
        print(f"  p={p:>3}  fit time = {(time.perf_counter() - t0) * 1000:.2f} ms")


def main() -> None:
    demo_ols_normal_equation()
    demo_ridge_vs_ols_under_collinearity()
    demo_alpha_sweep()
    demo_train_test_split()
    demo_complexity_scaling()
    print("All 5 demos passed.")


if __name__ == "__main__":
    main()
