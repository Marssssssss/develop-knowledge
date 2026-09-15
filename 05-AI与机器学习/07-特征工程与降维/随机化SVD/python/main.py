#!/usr/bin/env python3
"""随机化 SVD(Halko / Martinsson / Tropp 2009):用随机投影把大矩阵压到小矩阵上再做 SVD。

sklearn 的 `PCA(svd_solver='randomized')` 与 `sklearn.utils.extmath.randomized_svd`
走的就是这套算法(官方指定参考:Halko et al. 2009 Algorithm 4.3)。值钱的地方在于只要前 k 个
奇异三元组时不必付完整 SVD 的代价:

    随机化 PCA :O(nmax²·n_components)   内存 ≈ 2·nmax·n_components
    精确  PCA  :O(nmax²·nmin)           内存 ≈ nmax·nmin      (nmax=max(n,p), nmin=min(n,p))

官方参数语义(本脚本逐个实验对应):
  - n_oversamples(默认 10):额外抽的随机向量数,**总量 = n_components + n_oversamples**。
    "Smaller number can improve speed but can negatively impact the quality of
     approximation of singular vectors and singular values";需要更高精度时可加大到
    `2k - n_components`(k 为有效秩),针对噪声大 / 谱衰减慢的矩阵。
  - n_iter('auto' → 4;若 n_components < 0.1·min(shape) 则 7):幂迭代次数。官方同时提醒
    "users should rather increase n_oversamples before increasing n_iter",因为随机化方法
    的初衷就是躲开昂贵的幂迭代;当 n_components ≥ 有效秩且谱不慢衰减时 n_iter=0/1 就够。
  - power_iteration_normalizer('auto' → n_iter<=2 用 'none',否则 'LU'):
    'QR' 最慢最准,'none' 最快但 n_iter 较大(如 ≥5)时数值不稳定,'LU' 居中。

**验证方式**:直接构造 A = U·diag(σ)·Vᵀ,真值 σ 解析已知,误差不依赖任何外部实现对拍。
"""

import math
import random

# -------------------------------------------------------------------------- 工具


def matmul(A, B):
    m, k, n = len(A), len(B), len(B[0])
    return [[math.fsum(A[i][t] * B[t][j] for t in range(k)) for j in range(n)] for i in range(m)]


def transpose(A):
    return [list(col) for col in zip(*A)]


def matvec_sq_norm(v):
    return math.fsum(x * x for x in v)


def orthonormal_columns(n, k, rng, rounds=2):
    """两轮改进 Gram-Schmidt → n×k 列正交矩阵(QR 的 Q)。"""
    cols = []
    for _ in range(k):
        v = [rng.gauss(0.0, 1.0) for _ in range(n)]
        for _ in range(rounds):
            for u in cols:
                d = math.fsum(a * b for a, b in zip(v, u))
                v = [a - d * b for a, b in zip(v, u)]
        nrm = math.sqrt(matvec_sq_norm(v))
        cols.append([a / nrm for a in v])
    return [[cols[j][i] for j in range(k)] for i in range(n)]


def qr_q(Y, rounds=2):
    """对 Y(n×l) 做两轮改进 Gram-Schmidt,返回正交基 Q(n×l)。

    注意:投影必须对着**已归一化**的前序列做(每算完一列就地归一化),
    否则减掉的是"投影 × 未归一列的模长",列之间根本不会正交 —— 这是本 demo
    开发期踩过的坑,症状是 ‖QᵀQ-I‖_F 量级 O(l) 而不是 O(ε)。
    """
    n, l = len(Y), len(Y[0])
    cols = [list(col) for col in zip(*Y)]
    for _ in range(rounds):
        for j in range(l):
            for t in range(j):
                d = math.fsum(a * b for a, b in zip(cols[j], cols[t]))
                cols[j] = [a - d * b for a, b in zip(cols[j], cols[t])]
            nrm = math.sqrt(matvec_sq_norm(cols[j]))
            if nrm > 0.0:
                cols[j] = [a / nrm for a in cols[j]]
    return [[cols[j][i] for j in range(l)] for i in range(n)]


def lu_l_factor(Y):
    """带部分主元的 LU:返回 L 因子(n×l,单位下三角)。

    Halko 的 'LU' 归一化就是"用 L 的列当新的基":L 的列张成与 Y 相同的空间,
    但尺度被消元过程拉回 O(1),比不做归一化稳、比完整 QR 便宜。
    """
    n, l = len(Y), len(Y[0])
    Z = [row[:] for row in Y]
    L = [[0.0] * l for _ in range(n)]
    for j in range(l):
        piv = max(range(j, n), key=lambda r: abs(Z[r][j]))
        Z[j], Z[piv] = Z[piv], Z[j]
        for i in range(j + 1, n):
            m = Z[i][j] / Z[j][j] if Z[j][j] != 0.0 else 0.0
            L[i][j] = m
            if m != 0.0:
                Z[i] = [a - m * b for a, b in zip(Z[i], Z[j])]
        L[j][j] = 1.0
    return L


def jacobi_eigh(S, max_sweeps=300):
    """循环 Jacobi 对称特征分解,返回降序特征值与特征向量(Q 的列为特征向量)。"""
    p = len(S)
    A = [row[:] for row in S]
    Q = [[1.0 if i == j else 0.0 for j in range(p)] for i in range(p)]
    for _ in range(max_sweeps):
        off = math.fsum(A[i][j] ** 2 for i in range(p) for j in range(i + 1, p))
        if off <= 1e-30:
            break
        for i in range(p - 1):
            for j in range(i + 1, p):
                if A[i][j] == 0.0:
                    continue
                theta = 0.5 * math.atan2(2.0 * A[i][j], A[i][i] - A[j][j])
                c, s = math.cos(theta), math.sin(theta)
                for k in range(p):
                    aki, akj = A[k][i], A[k][j]
                    A[k][i], A[k][j] = c * aki + s * akj, -s * aki + c * akj
                for k in range(p):
                    aik, ajk = A[i][k], A[j][k]
                    A[i][k], A[j][k] = c * aik + s * ajk, -s * aik + c * ajk
                for k in range(p):
                    qki, qkj = Q[k][i], Q[k][j]
                    Q[k][i], Q[k][j] = c * qki + s * qkj, -s * qki + c * qkj
    w = [A[i][i] for i in range(p)]
    order = sorted(range(p), key=lambda i: -w[i])
    return [w[i] for i in order], [[Q[r][order[k]] for k in range(p)] for r in range(p)]


def small_svd(B):
    """对矮矩阵 B(l×p, l 很小)求左奇异向量与奇异值:B·Bᵀ 的特征分解即可。"""
    w, Q = jacobi_eigh(matmul(B, transpose(B)))
    return Q, [math.sqrt(max(x, 0.0)) for x in w]


def frob_orthogonality_error(Q):
    """‖QᵀQ - I‖_F:随机投影后正交化的质量指标。"""
    k = len(Q[0])
    QtQ = matmul(transpose(Q), Q)
    return math.sqrt(math.fsum((QtQ[i][j] - (1.0 if i == j else 0.0)) ** 2
                               for i in range(k) for j in range(k)))


def make_matrix_with_spectrum(n, p, sigma, seed):
    """造奇异值恰为 sigma 的矩阵 A = U·diag(σ)·Vᵀ,并返回 (A, U)。

    U 必须一并返回:验证子空间误差时的参照物是 A 自己的左奇异向量,
    不是随便一个随机正交基(用随机基做参照,s inθ 恒为 1,什么也验证不了)。
    """
    rng = random.Random(seed)
    U = orthonormal_columns(n, p, rng)
    V = orthonormal_columns(p, p, rng)
    A = [[math.fsum(U[r][t] * sigma[t] * V[j][t] for t in range(p)) for j in range(p)]
         for r in range(n)]
    return A, U


# ---------------------------------------------------------------- 随机化 SVD


def randomized_svd(A, n_components, n_oversamples=10, n_iter="auto",
                   normalizer="auto", seed=0):
    """返回 (U(n×k), s(k), Q(n×l), l)。k = n_components,l = k + n_oversamples。"""
    rng = random.Random(seed)
    n, p = len(A), len(A[0])
    l = n_components + n_oversamples
    if l > min(n, p):
        raise ValueError(f"l={l} 必须 <= min(n,p)={min(n, p)}")
    if n_iter == "auto":                      # 官方:'auto' → 4;n_components 小时 → 7
        n_iter = 4 if n_components >= 0.1 * min(n, p) else 7
    if normalizer == "auto":                  # 官方:'auto' → n_iter<=2 用 none,否则 LU
        normalizer = "none" if n_iter <= 2 else "LU"
    omega = [[rng.gauss(0.0, 1.0) for _ in range(l)] for _ in range(p)]
    Y = matmul(A, omega)                      # 第一步:随机"扫"一遍 A 的值域
    for _ in range(n_iter):                   # 幂迭代:(AAᵀ)^q·AΩ 放大主方向
        if normalizer == "QR":
            Y = qr_q(Y)
        elif normalizer == "LU":
            Y = lu_l_factor(Y)
        Y = matmul(A, matmul(transpose(A), Y))
    Q = qr_q(Y)                               # 值域的近似正交基
    B = matmul(transpose(Q), A)               # 压到 l×p 的小矩阵
    Ub, s = small_svd(B)
    Uc = [[Ub[r][k] for k in range(n_components)] for r in range(l)]
    return matmul(Q, Uc), s[:n_components], Q, l


def singular_errors_against(sigma_true, s_hat, k):
    return max(abs(s_hat[i] - sigma_true[i]) / sigma_true[i] for i in range(k))


def subspace_sin_theta(Q, U_true, k):
    """最大主角正弦:sinθ_max = √(1-σ_min(QᵀU_k)²)。"""
    P = matmul(transpose(Q), [[U_true[r][c] for c in range(k)] for r in range(len(U_true))])
    eig, _ = jacobi_eigh(matmul(transpose(P), P))     # PᵀP 的 k×k 特征分解 ⇒ σ²
    smin = math.sqrt(max(eig[-1], 0.0))
    return math.sqrt(max(0.0, 1.0 - min(smin, 1.0) ** 2))


# -------------------------------------------------------------------------- 实验


def exp1_oversampling():
    n, p, k = 400, 60, 10
    sigma = [math.exp(-0.15 * i) for i in range(p)]      # 指数衰减,κ≈7e3
    A, U_true = make_matrix_with_spectrum(n, p, sigma, seed=1)
    print("== 实验 1:n_oversamples 对近似质量的影响(n_iter=2, k=10)==")
    print("  oversamples   σ 最大相对误差   子空间 sinθ     ‖QᵀQ-I‖_F")
    errs = []
    for ov in (0, 2, 5, 10, 20):
        U, s, Q, l = randomized_svd(A, k, n_oversamples=ov, n_iter=2, seed=42)
        e = singular_errors_against(sigma, s, k)
        errs.append(e)
        print(f"  {ov:>11}   {e:.3e}        {subspace_sin_theta(Q, U_true, k):.3e}"
              f"      {frob_orthogonality_error(Q):.3e}")
    assert errs[-1] < 1e-6 and errs[0] > errs[-1]
    print("  → 官方:随机向量总数 = n_components + n_oversamples;"
          "「Smaller number can improve speed but can negatively\n"
          "     impact the quality of approximation」—— oversamples=0 时误差显著更大")


def exp2_slow_decay():
    n, p, k = 400, 60, 10
    sigma = [1.0 / (i + 1) for i in range(p)]             # 慢衰减,κ=60
    A, U_true = make_matrix_with_spectrum(n, p, sigma, seed=2)
    print("\n== 实验 2:谱衰减慢时幂迭代(n_iter)才是关键(oversamples=10, k=10)==")
    print("  n_iter   σ 最大相对误差   子空间 sinθ")
    errs = []
    for it in (0, 1, 2, 4, 7):
        U, s, Q, l = randomized_svd(A, k, n_oversamples=10, n_iter=it,
                                    normalizer="QR", seed=7)
        e = singular_errors_against(sigma, s, k)
        errs.append(e)
        print(f"  {it:>6}   {e:.3e}        {subspace_sin_theta(Q, U_true, k):.3e}")
    assert errs[0] > 1e-3 and errs[-1] < errs[0]
    print("  → 官方:n_iter 用于对付很噪的问题;但应先加大 n_oversamples,"
          "因为随机化方法的初衷就是躲开昂贵的幂迭代")


def exp3_fast_decay():
    n, p, k = 400, 40, 20
    sigma = [10.0 ** (-i) for i in range(p)]              # 快衰减:数值有效秩约 16
    A, _ = make_matrix_with_spectrum(n, p, sigma, seed=3)
    print("\n== 实验 3:谱衰减快 + n_components ≥ 有效秩 → n_iter=0 就够 ==")
    print(f"  n_components={k}(≥ 有效秩)   σ 最大相对误差(前 12 个)")
    errs = []
    for it in (0, 1, 4):
        U, s, Q, l = randomized_svd(A, k, n_oversamples=10, n_iter=it,
                                    normalizer="QR", seed=11)
        e = singular_errors_against(sigma, s, 12)
        errs.append(e)
        print(f"  n_iter={it:<3}          {e:.3e}")
    assert errs[0] < 1e-6
    print("  → 官方原文:「n_iter=0 or 1 should even work fine in theory」"
          "(当 n_components ≥ 有效秩且谱不慢衰减)—— 此处 n_iter=0 已经够准")


def exp4_normalizer():
    n, p = 300, 40
    sigma = [1.0] + [1e-2] * 4 + [1e-4] * 35              # 组内平坦、组间跨度大
    A, _ = make_matrix_with_spectrum(n, p, sigma, seed=4)
    print("\n== 实验 4:幂迭代之间的归一化(n_iter=7, k=10)==")
    print("  normalizer   ‖QᵀQ-I‖_F    σ 最大相对误差")
    out = {}
    for norm in ("none", "LU", "QR"):
        U, s, Q, l = randomized_svd(A, 10, n_oversamples=10, n_iter=7,
                                    normalizer=norm, seed=5)
        out[norm] = (frob_orthogonality_error(Q), singular_errors_against(sigma, s, 10))
        print(f"  {norm:<10}   {out[norm][0]:.3e}      {out[norm][1]:.3e}")
    assert out["QR"][0] <= out["none"][0] + 1e-12
    print("  → 官方:'QR' 最慢最准;'none' 最快但在 n_iter 较大(如 >=5)时数值不稳定;"
          "'LU' 居中(auto 默认:n_iter<=2 → none,否则 LU)")


if __name__ == "__main__":
    exp1_oversampling()
    exp2_slow_decay()
    exp3_fast_decay()
    exp4_normalizer()
    print("\n全部断言通过。")
