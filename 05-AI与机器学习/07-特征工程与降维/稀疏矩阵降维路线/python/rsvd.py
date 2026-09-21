"""随机化截断 SVD 与 TruncatedSVD(纯标准库)。

对齐对象:sklearn 1.9.1 的
  - sklearn.utils.extmath.randomized_range_finder / randomized_svd
  - sklearn.decomposition.TruncatedSVD(algorithm="randomized")
两者源码均已逐行读过(见 README 参考资料)。

要点(全部来自源码而非推测):
  * randomized_range_finder 的 Ω 是 `normal(size=(n_features, size))`,即 (列数, size);
    幂迭代每轮做 `A@Q` 与 `A.T@Q` 各一次,每次先过 normalizer;
    收尾无条件做一次 `qr(A@Q)`。
  * power_iteration_normalizer="auto" → `n_iter <= 2` 时退化为 "none",否则 "LU"。
  * n_iter="auto" → `n_components < 0.1 * min(shape)` 取 7,否则 4。
  * randomized_svd 在 transpose=False 时用 u_based_decision=True 定符号,
    transpose=True 时改用 v_based(u_based_decision=False)。
  * TruncatedSVD 调 randomized_svd 时传 flip_sign=False,再自己调一次
    `svd_flip(U, VT, u_based_decision=False)`。
  * TruncatedSVD(randomized)的 X_transformed 是 **X @ components_.T**,
    不是 `U * Sigma`;explained_variance_ 因此是**投影后各列的 np.var**(ddof=0),
    分母 full_var 是 **X 各列 np.var 之和**(即总方差,含均值贡献的缺失)。
"""

from linalg import Rng, matmul, transpose, qr, randn, svd_jacobi, svd_flip
# -------------------------------------------------------------------------- LU


def lu_permute_l(A):
    """部分主元 LU,返回 (P L, U) 使 `A = (P L) @ U`。

    对应 scipy.linalg.lu(permute_l=True)。A 为 m x n,k = min(m, n)。
    """
    m, n = len(A), len(A[0])
    k = min(m, n)
    LU = [row[:] for row in A]
    perm = list(range(m))                     # perm[i] = 现在第 i 行原本是第几行
    for j in range(k):
        p = max(range(j, m), key=lambda i: abs(LU[i][j]))
        if p != j:
            LU[j], LU[p] = LU[p], LU[j]
            perm[j], perm[p] = perm[p], perm[j]
        piv = LU[j][j]
        if piv == 0.0:
            continue
        for i in range(j + 1, m):
            f = LU[i][j] / piv
            LU[i][j] = f
            if f != 0.0:
                row_i, row_j = LU[i], LU[j]
                for c in range(j + 1, n):
                    row_i[c] -= f * row_j[c]
    L = [[0.0] * k for _ in range(m)]
    for i in range(m):
        lim = min(i, k - 1)
        for j in range(lim + 1):
            L[i][j] = LU[i][j] if i > j else (1.0 if i == j else 0.0)
    U = [[LU[i][j] if i <= j else 0.0 for j in range(n)] for i in range(k)]
    PL = [[0.0] * k for _ in range(m)]
    for i in range(m):
        PL[perm[i]] = L[i]                    # A = (Pᵀ L) @ U,故 PL 的行要按 perm 反置
    return PL, U


# -------------------------------------------------------------------------- 随机化子空间


def randomized_range_finder(A, size, n_iter, normalizer="auto", rng=None):
    """Halko et al. 2009 Algorithm 4.3:求 A 值域的正交基 Q(shape = m x size)。"""
    m, n = len(A), len(A[0])
    rng = rng if rng is not None else Rng(0)
    Q = randn(n, size, rng)                   # 注意是 (列数, size),不是 (行数, size)
    if normalizer == "auto":
        normalizer = "none" if n_iter <= 2 else "LU"
    if normalizer == "QR":
        def nf(x):
            return qr(x)[0]
    elif normalizer == "LU":
        def nf(x):
            return lu_permute_l(x)[0]
    elif normalizer == "none":
        def nf(x):
            return x
    else:
        raise ValueError("power_iteration_normalizer 只能是 auto/QR/LU/none")
    At = transpose(A)
    for _ in range(n_iter):
        Q = nf(matmul(A, Q))
        Q = nf(matmul(At, Q))
    return qr(matmul(A, Q))[0]


def randomized_svd(M, n_components, n_oversamples=10, n_iter="auto",
                   normalizer="auto", transpose_="auto", flip_sign=True, rng=None):
    """随机化截断 SVD,返回 (U, s, Vt),形状 (m,k) / (k,) / (k,n)。"""
    n_random = n_components + n_oversamples
    m, n = len(M), len(M[0])
    if n_iter == "auto":
        n_iter = 7 if n_components < 0.1 * min(m, n) else 4
    if transpose_ == "auto":
        transpose_ = m < n
    A = transpose(M) if transpose_ else [row[:] for row in M]
    Q = randomized_range_finder(A, n_random, n_iter, normalizer, rng)
    B = matmul(transpose(Q), A)               # (size x m) @ (m x n) = size x n
    Uhat, s, Vt = svd_jacobi(B)
    U = matmul(Q, Uhat)
    if flip_sign:
        if transpose_:
            U, Vt = svd_flip(U, Vt, u_based_decision=False)
        else:
            U, Vt = svd_flip(U, Vt)
    if transpose_:
        return (transpose(Vt[:n_components]), s[:n_components],
                transpose([row[:n_components] for row in U]))
    return ([row[:n_components] for row in U], s[:n_components], Vt[:n_components])


# -------------------------------------------------------------------------- TruncatedSVD


def _var(col):
    """np.var 默认 ddof=0。"""
    mu = sum(col) / len(col)
    return sum((x - mu) ** 2 for x in col) / len(col)


class TruncatedSVD:
    """不中心化的截断 SVD:可直接吃稀疏矩阵,代价是首个方向被均值吃掉。"""

    def __init__(self, n_components=2, *, algorithm="randomized", n_iter=5,
                 n_oversamples=10, power_iteration_normalizer="auto",
                 random_state=None):
        self.n_components = n_components
        self.algorithm = algorithm
        self.n_iter = n_iter
        self.n_oversamples = n_oversamples
        self.power_iteration_normalizer = power_iteration_normalizer
        self.random_state = random_state

    def fit(self, X, y=None):
        self.fit_transform(X)
        return self

    def fit_transform(self, X, y=None):
        m, n = len(X), len(X[0])
        if self.algorithm != "randomized":
            raise ValueError("本实现只覆盖 algorithm='randomized'")
        if self.n_components > n:
            raise ValueError(
                "n_components(%d) must be <= n_features(%d)." % (self.n_components, n))
        rng = Rng(0 if self.random_state is None else self.random_state)
        U, Sigma, VT = randomized_svd(
            X, self.n_components, n_oversamples=self.n_oversamples,
            n_iter=self.n_iter, normalizer=self.power_iteration_normalizer,
            flip_sign=False, rng=rng)
        U, VT = svd_flip(U, VT, u_based_decision=False)
        self.components_ = VT
        X_transformed = matmul(X, transpose(self.components_))
        self.explained_variance_ = [_var(col) for col in transpose(X_transformed)]
        full_var = sum(_var(col) for col in transpose(X))
        self.explained_variance_ratio_ = [e / full_var for e in self.explained_variance_]
        self.singular_values_ = Sigma
        return X_transformed

    def transform(self, X):
        return matmul(X, transpose(self.components_))

    def inverse_transform(self, X):
        return matmul(X, self.components_)
