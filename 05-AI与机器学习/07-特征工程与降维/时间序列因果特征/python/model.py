"""最小二乘(纯标准库):正规方程 + 部分主元高斯消元。

只为把"泄漏有多大"量化成 R²,不追求数值稳健 —— 设计矩阵都是标准化过的小矩阵。
"""


def solve(A, b):
    """部分主元高斯消元,就地消元后回代。A 为 n x n,b 长度 n。"""
    n = len(A)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(M[i][k]))
        if p != k:
            M[k], M[p] = M[p], M[k]
        piv = M[k][k]
        if piv == 0.0:
            continue
        for i in range(k + 1, n):
            f = M[i][k] / piv
            M[i][k] = 0.0
            for j in range(k + 1, n + 1):
                M[i][j] -= f * M[k][j]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / M[i][i] if M[i][i] != 0.0 else 0.0
    return x


def fit_ols(X, y, l2=1e-8):
    """岭回归式稳定化:l2 只加在对角上(截距列也不放过,免得完全共线时崩)。"""
    n, p = len(X), len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) + (l2 if a == b else 0.0)
          for b in range(p)] for a in range(p)]
    b = [sum(X[i][a] * y[i] for i in range(n)) for a in range(p)]
    return solve(A, b)


def predict(X, w):
    return [sum(row[j] * w[j] for j in range(len(w))) for row in X]


def r2(y, pred):
    mu = sum(y) / len(y)
    ss_tot = sum((v - mu) ** 2 for v in y)
    ss_res = sum((a - b) ** 2 for a, b in zip(y, pred))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def mse(y, pred):
    return sum((a - b) ** 2 for a, b in zip(y, pred)) / len(y)
