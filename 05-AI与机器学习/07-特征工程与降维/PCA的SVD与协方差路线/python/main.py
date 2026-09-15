#!/usr/bin/env python3
"""PCA 的两条数值路线:协方差矩阵特征分解 vs 数据矩阵直接 SVD。

对应 sklearn `PCA(svd_solver=...)` 的两个精确求解器:
  - 'covariance_eigh':先算 C = XcᵀXc/(n-1),再对 C 做特征分解
    (官方原话:compared to the "full" solver, this solver effectively doubles
     the condition number and is therefore less numerically stable)
  - 'full'          :直接对 Xc 做 SVD,components_ 就是右奇异向量
    (官方:`components_` ... Equivalently, the right singular vectors of the
     centered input data)

两条路线在数学上等价:λ_i = σ_i² / (n-1)。差的是**数值**:协方差路线的
条件是 SVD 路线的**平方**(因为特征值是奇异值的平方),于是 float64 下
跨度大的谱会被直接压穿。

本脚本不依赖任何第三方库,自己实现:
  - 单边 Jacobi SVD(对列做正交旋转,列范数即奇异值,数值上很稳)
  - 循环 Jacobi 对称特征分解(在 p×p 协方差矩阵上做)

权威依据见同目录 ../README.md「参考资料」。
"""

import math
import random

# --------------------------------------------------------------------------
# 线性代数:单边 Jacobi SVD 与循环 Jacobi 特征分解
# --------------------------------------------------------------------------


def center(X):
    """按列去均值。sklearn: "PCA centers but does not scale the input data"."""
    n, p = len(X), len(X[0])
    mu = [math.fsum(row[j] for row in X) / n for j in range(p)]
    return [[row[j] - mu[j] for j in range(p)] for row in X], mu


def jacobi_svd(A, max_sweeps=60, tol=1e-15):
    """单边 Jacobi SVD:A = U·diag(s)·Vᵀ,要求行数 n >= 列数 p。

    思路:不断用 2×2 旋转把 A 的列两两正交化。每轮扫描所有列对 (i,j),
    令 Gram 矩阵 G=[[a,b],[g,b']] 的非对角元归零(theta = atan2(2g, a-b)/2),
    列旋转后累乘到 V。收敛后各列两两正交,列范数即奇异值,U 由列归一化得到。
    单边 Jacobi 的右奇异向量精度不受条件数放大影响,是本 demo 的对照组。
    """
    n, p = len(A), len(A[0])
    B = [row[:] for row in A]
    V = [[1.0 if i == j else 0.0 for j in range(p)] for i in range(p)]
    for _ in range(max_sweeps):
        off = 0.0
        for i in range(p - 1):
            for j in range(i + 1, p):
                a = math.fsum(B[r][i] * B[r][i] for r in range(n))
                b = math.fsum(B[r][j] * B[r][j] for r in range(n))
                g = math.fsum(B[r][i] * B[r][j] for r in range(n))
                if g == 0.0 or abs(g) <= tol * math.sqrt(a * b):
                    continue
                off += g * g
                theta = 0.5 * math.atan2(2.0 * g, a - b)
                c, s = math.cos(theta), math.sin(theta)
                for r in range(n):  # 列旋转:B ← B·Q
                    bi, bj = B[r][i], B[r][j]
                    B[r][i] = c * bi + s * bj
                    B[r][j] = -s * bi + c * bj
                for r in range(p):  # V ← V·Q
                    vi, vj = V[r][i], V[r][j]
                    V[r][i] = c * vi + s * vj
                    V[r][j] = -s * vi + c * vj
        if off <= 1e-30:
            break
    s = [math.sqrt(math.fsum(B[r][i] * B[r][i] for r in range(n))) for i in range(p)]
    order = sorted(range(p), key=lambda i: -s[i])
    s = [s[i] for i in order]
    U = [[(B[r][order[k]] / s[k] if s[k] > 0 else 0.0) for k in range(p)] for r in range(n)]
    V = [[V[r][order[k]] for k in range(p)] for r in range(p)]
    return U, s, V


def jacobi_eigh(S, max_sweeps=100, tol=1e-15):
    """循环 Jacobi 对称特征分解,返回 (w 升序, Q 的列 = 特征向量)。"""
    p = len(S)
    A = [row[:] for row in S]
    Q = [[1.0 if i == j else 0.0 for j in range(p)] for i in range(p)]
    for _ in range(max_sweeps):
        off = math.fsum(A[i][j] * A[i][j] for i in range(p) for j in range(i + 1, p))
        if off <= 1e-30:
            break
        for i in range(p - 1):
            for j in range(i + 1, p):
                if A[i][j] == 0.0:
                    continue
                theta = 0.5 * math.atan2(2.0 * A[i][j], A[i][i] - A[j][j])
                c, s = math.cos(theta), math.sin(theta)
                for k in range(p):  # A ← QᵀAQ 的显式两行两列更新
                    ak_i, ak_j = A[k][i], A[k][j]
                    A[k][i] = c * ak_i + s * ak_j
                    A[k][j] = -s * ak_i + c * ak_j
                for k in range(p):
                    ak_i, ak_j = A[i][k], A[j][k]
                    A[i][k] = c * ak_i + s * ak_j
                    A[j][k] = -s * ak_i + c * ak_j
                for k in range(p):
                    qk_i, qk_j = Q[k][i], Q[k][j]
                    Q[k][i] = c * qk_i + s * qk_j
                    Q[k][j] = -s * qk_i + c * qk_j
    w = [A[i][i] for i in range(p)]
    order = sorted(range(p), key=lambda i: -w[i])
    return [w[i] for i in order], [[Q[r][order[k]] for k in range(p)] for r in range(p)]


# --------------------------------------------------------------------------
# 两条 PCA 路线
# --------------------------------------------------------------------------


def pca_via_covariance(Xc, k):
    """路线一:协方差矩阵特征分解(sklearn 'covariance_eigh')。"""
    n, p = len(Xc), len(Xc[0])
    C = [[math.fsum(Xc[r][i] * Xc[r][j] for r in range(n)) / (n - 1)
          for j in range(p)] for i in range(p)]
    lam, Q = jacobi_eigh(C)
    return lam[:k], [[Q[r][c] for r in range(p)] for c in range(k)], C


def pca_via_svd(Xc, k):
    """路线二:数据矩阵直接 SVD(sklearn 'full')。"""
    n = len(Xc)
    _, s, V = jacobi_svd(Xc)
    lam = [si * si / (n - 1) for si in s]
    return lam[:k], [list(row) for row in V[:k]], s


# --------------------------------------------------------------------------
# 实验
# --------------------------------------------------------------------------


def orthonormal_columns(n, k, seed, skip_const=True):
    """生成 n×k 的列正交矩阵;skip_const=True 时所有列都与常向量 1/√n 正交。

    为什么必须正交于常向量:本 demo 的两条路线都作用在**去均值**后的数据上。
    若构造出来的矩阵各列本身就有非零均值,`center()` 会像减掉一个秩 1 分量那样
    把谱改掉(实测能改掉 98% 的方差),实验就变成了测别的东西。让 U 的列与
    1/√n 正交 ⇒ X = U·diag(σ)·Vᵀ 的列均值恰为 0,center() 成为恒等操作。
    """
    rng = random.Random(seed)
    basis = []
    if skip_const:
        basis.append([1.0 / math.sqrt(n)] * n)
    while len(basis) < k + (1 if skip_const else 0):
        v = [rng.gauss(0.0, 1.0) for _ in range(n)]
        for _ in range(2):  # 两轮改进的 Gram-Schmidt(数值上比经典 GS 稳)
            for u in basis:
                d = math.fsum(a * b for a, b in zip(v, u))
                v = [a - d * b for a, b in zip(v, u)]
        nrm = math.sqrt(math.fsum(a * a for a in v))
        basis.append([a / nrm for a in v])
    cols = basis[1:] if skip_const else basis
    return [[cols[j][i] for j in range(k)] for i in range(n)]  # 行主序矩阵


def make_matrix_with_spectrum(n, p, sigma, seed):
    """造一个奇异值恰好为 sigma 的矩阵:X = U·diag(sigma)·Vᵀ(且各列已零均值)。"""
    U = orthonormal_columns(n, p, seed)          # n×p,列正交且正交于常向量
    V = orthonormal_columns(p, p, seed + 1, skip_const=False)  # p×p 正交矩阵
    return [[math.fsum(U[r][t] * sigma[t] * V[j][t] for t in range(p)) for j in range(p)]
            for r in range(n)]


def exp1_sklearn_anchor():
    """用 sklearn 官方 docstring 的例子当锚点(见 PCA API 文档的 Examples)。"""
    X = [[-1.0, -1.0], [-2.0, -1.0], [-3.0, -2.0], [1.0, 1.0], [2.0, 1.0], [3.0, 2.0]]
    Xc, _ = center(X)
    lam, _, _ = pca_via_covariance(Xc, 2)
    _, _, s = pca_via_svd(Xc, 2)
    total = math.fsum(lam)
    ratio = [v / total for v in lam]
    print("== 实验 1:对齐 sklearn PCA 文档 docstring 的数值锚点 ==")
    print(f"  奇异值        : [{s[0]:.5f}, {s[1]:.5f}]   期望 [6.30061, 0.54980]")
    print(f"  方差解释比例  : [{ratio[0]:.4f}, {ratio[1]:.4f}]   期望 [0.9924, 0.0075]")
    print(f"  λ_i = σ_i²/(n-1) 校验:{abs(lam[0]-s[0]**2/5) < 1e-12 and abs(lam[1]-s[1]**2/5) < 1e-12}")
    assert abs(s[0] - 6.30061) < 1e-5 and abs(s[1] - 0.54980) < 1e-5
    assert abs(ratio[0] - 0.9924) < 1e-4 and abs(ratio[1] - 0.00755) < 1e-4


def exp2_condition_number():
    """谱跨度大时,协方差路线的条件数是 SVD 路线的平方 → 最小奇异值直接失效。

    精度账(eps = 机器精度 ≈ 2.2e-16):
      - 直接 SVD:奇异值的**绝对**精度 ~ eps·σ_max,故 σ_min 的相对误差 ~ eps·κ
      - 协方差路线:特征值的绝对精度 ~ eps·λ_max,而 σ = sqrt(λ(n-1)),
        于是 σ_min 的相对误差 ~ eps·κ²/2 —— κ 被平方,κ=1e12 时直接爆掉。
    """
    n, p = 200, 6
    sigma = [1.0, 1e-2, 1e-4, 1e-8, 1e-10, 1e-12]
    X = make_matrix_with_spectrum(n, p, sigma, seed=7)
    Xc, _ = center(X)
    lam_cov, _, _ = pca_via_covariance(Xc, p)
    _, _, s_svd = pca_via_svd(Xc, p)
    kappa = sigma[0] / sigma[-1]
    print("\n== 实验 2:条件数被平方(covariance_eigh 的数值代价)==")
    print(f"  κ(Xc)={kappa:.1e} → 理论上 κ(C)=κ²={kappa ** 2:.1e};"
          f" 而 1/eps ≈ {1 / 2.22e-16:.1e}")
    print(f"  SVD 路线预期 σ_min 相对误差 ~ eps·κ = {2.22e-16 * kappa:.1e};"
          f"协方差路线 ~ eps·κ²/2 = {2.22e-16 * kappa ** 2 / 2:.1e}")
    print("  i   σ_真值      σ_SVD路线    相对误差    σ_协方差路线  相对误差")
    err_svd, err_cov = [], []
    for i in range(p):
        valid = lam_cov[i] > 0
        s_cov = math.sqrt(lam_cov[i] * (n - 1)) if valid else float("nan")
        e_svd = abs(s_svd[i] - sigma[i]) / sigma[i]
        e_cov = abs(s_cov - sigma[i]) / sigma[i] if valid else float("nan")
        err_svd.append(e_svd)
        err_cov.append(e_cov)
        tag = "" if valid else "   ← λ<0,开方后连数都不是"
        print(f"  {i}  {sigma[i]:.1e}   {s_svd[i]:.6e}  {e_svd:.2e}   "
              f"{s_cov:.6e}  {e_cov:.2e}{tag}")
    print(f"  实测 λ_min = {lam_cov[-1]:+.3e}(理论 {sigma[-1] ** 2 / (n - 1):.3e})")
    assert max(err_svd) < 1e-5                       # SVD 路线:受 eps·κ 限制(实测 4.2e-6)
    assert err_cov[3] > 1e-3                         # 协方差路线从 σ=1e-8 起开始失真
    assert err_cov[3] / err_svd[3] > 1e6             # 同一条 σ 上,差距 7 个数量级
    assert math.isnan(err_cov[4]) and math.isnan(err_cov[5])  # 更小的 λ 直接变负 → σ 无定义


def exp3_center_only_not_scale():
    """sklearn:PCA "centers but does not scale"。单位差异会直接决定主成分。"""
    rng = random.Random(11)
    X = [[rng.gauss(0, 1.0), rng.gauss(0, 1000.0)] for _ in range(300)]
    n = len(X)
    var = [math.fsum(r[j] * r[j] for r in X) / (n - 1) - (math.fsum(r[j] for r in X) / n) ** 2
           for j in range(2)]
    Xc, _ = center(X)
    _, V, _ = pca_via_svd(Xc, 2)

    def share(vec, vars_):  # 第 1 主成分捕获的方差里,两个特征各贡献多少
        parts = [vec[0][j] ** 2 * vars_[j] for j in range(2)]
        return [p / math.fsum(parts) for p in parts]

    Zc, _ = center([[row[j] / math.sqrt(var[j]) for j in range(2)] for row in X])
    _, Vz, _ = pca_via_svd(Zc, 2)
    print("\n== 实验 3:只中心化、不缩放 → 主成分被大尺度特征独占 ==")
    print(f"  两特征标准差 {math.sqrt(var[0]):.3f} vs {math.sqrt(var[1]):.3f}")
    print(f"  不缩放:第 1 主成分方向 {V[0][0]:+.6f}·f0 {V[0][1]:+.6f}·f1,"
          f"方差贡献 f0={share(V, var)[0]:.4%} / f1={share(V, var)[1]:.4%}")
    print(f"  标准化:第 1 主成分方向 {Vz[0][0]:+.6f}·f0 {Vz[0][1]:+.6f}·f1,"
          f"方差贡献 f0={share(Vz, [1.0, 1.0])[0]:.2%} / f1={share(Vz, [1.0, 1.0])[1]:.2%}")
    print("  → 不缩放时第 1 主成分几乎等于「f1 本身」,f0 的信息被完全挤掉;"
          "whiten=True 也救不了,白化只改分量尺度,不改方向")
    assert share(V, var)[1] > 0.9999 and abs(Vz[0][0]) > 0.4


def exp4_whiten_and_memory():
    """whiten 的作用与两条路线的内存/复杂度账。"""
    n, p, k = 300, 40, 8
    sigma = [1.0 / (i + 1) for i in range(p)]
    X = make_matrix_with_spectrum(n, p, sigma, seed=3)
    Xc, _ = center(X)
    _, _, s = pca_via_svd(Xc, p)
    var = [si * si / (n - 1) for si in s[:k]]
    print("\n== 实验 4:whiten 与内存/复杂度 ==")
    print(f"  前 3 个成分方差(未白化):{[round(v, 4) for v in var[:3]]}")
    print(f"  前 3 个成分方差(白化后):{[round(v / v, 4) for v in var[:3]]}  ← 恒为 1")
    print(f"  内存:SVD 路线 {n * p} 个 double vs 协方差路线 {p * p}(再加 {n * p})")
    print(f"  n={n}, p={p} → 协方差矩阵额外 {p * p * 8 / 1024:.1f} KiB;"
          f"p 增大到 4096 时是 {4096 * 4096 * 8 / 2**20:.0f} MiB(所以官方 auto 只在小 p 用)")


if __name__ == "__main__":
    exp1_sklearn_anchor()
    exp2_condition_number()
    exp3_center_only_not_scale()
    exp4_whiten_and_memory()
    print("\n全部断言通过。")
