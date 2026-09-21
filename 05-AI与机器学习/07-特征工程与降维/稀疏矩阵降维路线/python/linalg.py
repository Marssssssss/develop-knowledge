"""稠密线性代数内核(纯标准库)+ 确定性随机源。

只有四个原语:矩阵乘法、Householder QR、单边 Jacobi SVD、svd_flip 符号修正。
对齐对象:scipy.linalg.svd(单边 Jacobi 与它同为高相对精度算法)与
sklearn.utils.extmath.svd_flip。

矩阵约定:统一用 list[list[float]],行主序。
"""

import math

# -------------------------------------------------------------------------- 随机源


class Rng:
    """LCG(数值取自 Numerical Recipes),只用于生成随机投影矩阵 Ω。"""

    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF

    def u32(self):
        self.s = (1664525 * self.s + 1013904223) & 0xFFFFFFFF
        return self.s

    def uniform(self):
        return self.u32() / 4294967296.0

    def normal(self):
        u1 = max(self.uniform(), 1e-300)
        u2 = self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def randn(rows, cols, rng):
    return [[rng.normal() for _ in range(cols)] for _ in range(rows)]


# -------------------------------------------------------------------------- 基础运算


def matmul(A, B):
    n, k, m = len(A), len(B), len(B[0])
    out = [[0.0] * m for _ in range(n)]
    for i in range(n):
        Ai, Oi = A[i], out[i]
        for t in range(k):
            a = Ai[t]
            if a == 0.0:
                continue
            Bt = B[t]
            for j in range(m):
                Oi[j] += a * Bt[j]
    return out


def transpose(A):
    return [list(col) for col in zip(*A)]


def matvec(A, v):
    return [sum(a * b for a, b in zip(row, v)) for row in A]


def eye(n):
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def col_norm(A, j):
    return math.sqrt(sum(A[i][j] * A[i][j] for i in range(len(A))))


# -------------------------------------------------------------------------- QR


def qr(A):
    """Householder QR(A = QR),R 为方阵(n x n),Q 为 m x n 的正交列。"""
    m, n = len(A), len(A[0])
    R = [row[:] for row in A]
    Q = eye(m)
    for k in range(n):
        norm = math.sqrt(sum(R[i][k] ** 2 for i in range(k, m)))
        if norm == 0.0:
            continue
        alpha = -norm if R[k][k] >= 0 else norm
        v = [0.0] * m
        for i in range(k, m):
            v[i] = R[i][k]
        v[k] -= alpha
        vnorm2 = sum(x * x for x in v)
        if vnorm2 == 0.0:
            continue
        for j in range(k, n):                      # R = H R
            s = sum(v[i] * R[i][j] for i in range(k, m)) * 2.0 / vnorm2
            for i in range(k, m):
                R[i][j] -= s * v[i]
        for j in range(m):                         # Q = Q H
            s = sum(Q[j][i] * v[i] for i in range(k, m)) * 2.0 / vnorm2
            for i in range(k, m):
                Q[j][i] -= s * v[i]
    return [row[:n] for row in Q], [row[:n] for row in R[:n]]


# -------------------------------------------------------------------------- SVD


def svd_jacobi(X, tol=1e-14, max_sweeps=60):
    """单边 Jacobi SVD,返回 (U, S, Vt),S 降序。兼容 m < n(m > n 时不做转置)。"""
    m, n = len(X), len(X[0])
    transposed = m < n
    A = transpose(X) if transposed else [row[:] for row in X]
    m, n = len(A), len(A[0])
    V = eye(n)
    for _ in range(max_sweeps):
        off = 0.0
        for p in range(n - 1):
            for q in range(p + 1, n):
                alpha = sum(A[i][p] * A[i][p] for i in range(m))
                beta = sum(A[i][q] * A[i][q] for i in range(m))
                gamma = sum(A[i][p] * A[i][q] for i in range(m))
                if abs(gamma) <= tol * math.sqrt(alpha * beta) or gamma == 0.0:
                    continue
                off = max(off, abs(gamma) / math.sqrt(alpha * beta))
                zeta = (beta - alpha) / (2.0 * gamma)
                t = math.copysign(1.0, zeta) / (abs(zeta) + math.sqrt(1.0 + zeta * zeta))
                c = 1.0 / math.sqrt(1.0 + t * t)
                s = c * t
                for i in range(m):
                    ap, aq = A[i][p], A[i][q]
                    A[i][p] = c * ap - s * aq
                    A[i][q] = s * ap + c * aq
                for i in range(n):
                    vp, vq = V[i][p], V[i][q]
                    V[i][p] = c * vp - s * vq
                    V[i][q] = s * vp + c * vq
        if off <= tol:
            break
    sigma = [col_norm(A, j) for j in range(n)]
    order = sorted(range(n), key=lambda j: -sigma[j])
    sigma = [sigma[j] for j in order]
    U = [[0.0] * n for _ in range(m)]
    for k, j in enumerate(order):
        if sigma[k] <= 0.0:
            continue
        for i in range(m):
            U[i][k] = A[i][j] / sigma[k]
    Vt = [[V[i][j] for j in order] for i in range(n)]
    Vt = transpose(Vt)
    if transposed:                                 # A = Xᵀ = U S Vᵀ ⇒ X = V S Uᵀ
        return transpose(Vt), sigma, transpose(U)
    return U, sigma, Vt


# -------------------------------------------------------------------------- 符号修正


def _sign(x):
    """np.sign 语义:0 就是 0,不是 +1(官方靠它把全零行/列抹平)。"""
    if x > 0.0:
        return 1.0
    if x < 0.0:
        return -1.0
    return 0.0


def svd_flip(u, v, u_based_decision=True):
    """把 u / v 的符号定死,避免同一份数据两次 SVD 得到相反符号。

    u_based_decision=True (PCA):看 u 每一列绝对值最大的元素;
    u_based_decision=False(TruncatedSVD / IncrementalPCA):看 v 每一行。
    """
    if u_based_decision:
        signs = [_sign(u[max(range(len(u)), key=lambda i: abs(u[i][j]))][j])
                 for j in range(len(u[0]))]
        u = [[u[i][j] * signs[j] for j in range(len(u[0]))] for i in range(len(u))]
        if v is not None:
            v = [[v[i][j] * signs[i] for j in range(len(v[0]))] for i in range(len(v))]
        return u, v
    signs = [_sign(v[i][max(range(len(v[i])), key=lambda j: abs(v[i][j]))])
             for i in range(len(v))]
    v = [[v[i][j] * signs[i] for j in range(len(v[0]))] for i in range(len(v))]
    if u is not None:
        u = [[u[i][j] * signs[j] for j in range(len(u[0]))] for i in range(len(u))]
    return u, v
