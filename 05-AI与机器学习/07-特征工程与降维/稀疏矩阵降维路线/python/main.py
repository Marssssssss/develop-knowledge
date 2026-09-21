"""demo 515 实验台:稀疏矩阵降维路线(TruncatedSVD 不中心化的代价 + IncrementalPCA)。

全部数值由本目录纯标准库实现算出,sklearn/scipy 只用于离线对拍(见 README)。
运行:`python main.py`
"""

import math

from linalg import Rng, matmul, transpose, svd_jacobi
from rsvd import TruncatedSVD
from ipca import IncrementalPCA, incremental_mean_and_var


def f4(x):
    return "%10.6f" % x


def frob(A):
    return math.sqrt(sum(v * v for row in A for v in row))


def vnorm(v):
    return math.sqrt(sum(x * x for x in v))


def cos(u, v):
    return sum(a * b for a, b in zip(u, v)) / (vnorm(u) * vnorm(v))


def principal_cos(V1, V2):
    """两个列正交基的主余弦(最大者),= V1ᵀV2 的最大奇异值。"""
    _, s, _ = svd_jacobi(matmul(transpose(V1), V2))
    return s[0]


def residual(A, B):
    return frob([[A[i][j] - B[i][j] for j in range(len(A[0]))] for i in range(len(A))])


def block_counts(rows=120, cols=25, seed=11, baseline=30.0):
    """模拟"词频/计数"矩阵:全列共享大基线,块结构只占很小的相对幅度。"""
    r = Rng(seed)
    X = [[baseline] * cols for _ in range(rows)]
    blocks = [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9], [10, 11, 12, 13, 14],
              [15, 16, 17], [18, 19, 20, 21, 22]]
    for i in range(rows):
        for j in blocks[i % 5]:
            X[i][j] += 4.0 + 2.0 * abs(r.normal())
        for j in range(cols):
            if r.uniform() < 0.12:
                X[i][j] += 3.0 * abs(r.normal())
    return X


def mean_vector(X):
    n, p = len(X), len(X[0])
    return [sum(X[i][j] for i in range(n)) / n for j in range(p)]


def center(X):
    mu = mean_vector(X)
    return [[X[i][j] - mu[j] for j in range(len(mu))] for i in range(len(X))]


def project(X, k, seed=0, n_iter=7):
    """不中心化 rank-k 重建。"""
    t = TruncatedSVD(n_components=k, random_state=seed, n_iter=n_iter).fit(X)
    return matmul(t.transform(X), t.components_), t


# --------------------------------------------------------------------- E1

def e1_uncentered_cost():
    print("E1 不中心化的代价:top-1 方向就是均值方向")
    X = block_counts()
    mu = mean_vector(X)
    _, t = project(X, 4)
    c = abs(cos(t.components_[0], mu))
    print("   shape=%dx%d  列均值=[%.2f, %.2f]" % (len(X), len(X[0]), min(mu), max(mu)))
    print("   |cos(components_[0], 均值)| = %.10f" % c)
    print("   不中心化 ratio =", [round(v, 5) for v in t.explained_variance_ratio_])
    Xc = center(X)
    _, tc = project(Xc, 4)
    print("   中心化   ratio =", [round(v, 5) for v in tc.explained_variance_ratio_])
    print("   -> 基线越大,comp0 越贴近全 1 方向;这一维不携带类间结构")


# --------------------------------------------------------------------- E2

def e2_one_extra_component(X):
    print("E2 代价的定量形式:不中心化要多花一个分量")
    mu = mean_vector(X)
    spread = frob([[X[i][j] - mu[j] for j in range(len(mu))] for i in range(len(X))])
    print("   ‖X - 均值‖_F = %s   (分母)" % f4(spread))
    print("   k   不中心化 rank-k      中心化 rank-k      不中心化 rank-(k+1)")
    for k in [1, 2, 3]:
        ru, _ = project(X, k)
        rc, _ = project(center(X), k)
        rc = [[rc[i][j] + mu[j] for j in range(len(mu))] for i in range(len(X))]
        ru1, _ = project(X, k + 1)
        print("   %d   %s      %s      %s"
              % (k, f4(residual(X, ru) / spread), f4(residual(X, rc) / spread),
                 f4(residual(X, ru1) / spread)))
    print("   -> 不中心化 rank-(k+1) 的误差与中心化 rank-k 同级:差的就是那一个分量")


# --------------------------------------------------------------------- E3

def decayed_spectrum(m=120, p=60, nr=12, seed=4, kind="fast"):
    """构造已知奇异谱的矩阵:保留全部 nr 个方向,其余为 0。"""
    R = Rng(seed)
    A = [[R.normal() for _ in range(m)] for _ in range(m)]
    B = [[R.normal() for _ in range(p)] for _ in range(p)]
    U, _, _ = svd_jacobi(A)
    V, _, _ = svd_jacobi(B)
    Uc, Vc = transpose(U), transpose(V)      # [t][i] / [t][j] 即第 t 个左/右奇异向量
    sig = ([0.5 ** i for i in range(nr)] if kind == "fast"
           else [1.0 / math.sqrt(i + 1) for i in range(nr)])
    return [[sum(sig[t] * Uc[t][i] * Vc[t][j] for t in range(nr)) for j in range(p)]
            for i in range(m)]


def _sweep(X, k, oversamples, base=None):
    if base is None:
        base = frob(X)
    _, _, Vte = svd_jacobi(X)
    Ve = transpose(Vte[:k])
    rows = []
    for norm in ["none", "LU", "QR"]:
        for it in [0, 1, 2, 4, 8]:
            t = TruncatedSVD(n_components=k, random_state=3, n_iter=it,
                             n_oversamples=oversamples,
                             power_iteration_normalizer=norm).fit(X)
            rec = matmul(t.transform(X), t.components_)
            rows.append((it, norm, f4(residual(X, rec) / base),
                         principal_cos(transpose(t.components_), Ve)))
    return rows


def e3_n_iter():
    print("E3 幂迭代与归一化:两个旋钮各自管什么")
    print("   [A] 谱衰减慢 + n_oversamples=0:子空间还没被抓住,靠 n_iter 补")
    XA = decayed_spectrum(kind="slow")
    print("       n_iter  归一化    相对残差        主余弦")
    for it, norm, e, c in _sweep(XA, 6, 0):
        if norm == "none":
            print("       %-8d %-9s %s   %.10f" % (it, norm, e, c))
    print("   [B] 谱衰减快 + n_oversamples=0:子空间一次就准,但 none 在迭代多时会自己坏掉")
    XB = decayed_spectrum(kind="fast")
    print("       n_iter  归一化    相对残差        主余弦")
    for it, norm, e, c in _sweep(XB, 6, 0):
        print("       %-8d %-9s %s   %.10f" % (it, norm, e, c))
    print("       -> none 在 n_iter=8 时残差反而变大;LU / QR 把它按住")
    print("          (对应官方文档:none 在 n_iter >= 5 时数值不稳定)")


# --------------------------------------------------------------------- E4

def e4_batch(data, p, k=3):
    print("E4 partial_fit 的近似精度:batch_size 会改变结果(不是浮点噪声)")
    ref = IncrementalPCA(n_components=k, batch_size=len(data)).fit([r[:] for r in data])
    print("   单块 S =", [round(v, 6) for v in ref.singular_values_])
    print("   batch_size   max|Δmean|    max|ΔS|      max|Δcomponent|")
    for bs in [4, 5, 7, 13, 20, 37, len(data), 4 * len(data)]:
        m = IncrementalPCA(n_components=k, batch_size=bs).fit([r[:] for r in data])
        print("   %-12d %.3e    %.3e   %.3e"
              % (bs, max(abs(a - b) for a, b in zip(m.mean_, ref.mean_)),
                 max(abs(a - b) for a, b in zip(m.singular_values_, ref.singular_values_)),
                 max(abs(abs(m.components_[i][j]) - abs(ref.components_[i][j]))
                     for i in range(k) for j in range(p))))
    print("   -> 均值精确,但每次 partial_fit 都把状态截到 k 行;块越多丢的残差方向越多")
    print("      (官方文档把 batch_size 定义为「近似精度 vs 内存」的权衡)")


# --------------------------------------------------------------------- E5

def e5_youngs_cramer():
    print("E5 增量方差更新的数值稳定性(生成时真值方差 = 1)")
    r = Rng(9)
    n, p, shift = 400, 4, 1.0e8
    X = [[shift + r.normal() for _ in range(p)] for _ in range(n)]
    onepass, twopass = [], []
    for j in range(p):
        mu = sum(X[i][j] for i in range(n)) / n
        twopass.append(sum((X[i][j] - mu) ** 2 for i in range(n)) / n)
        onepass.append(sum(X[i][j] ** 2 for i in range(n)) / n - mu * mu)
    mean, var, cnt = [0.0] * p, [0.0] * p, 0
    for i in range(0, n, 25):
        mean, var, cntv = incremental_mean_and_var(X[i:i + 25], mean, var, [cnt] * p)
        cnt = cntv[0]
    print("   列   一趟 Σx²/n−μ²        两趟 Σ(x−μ)²/n      增量 Youngs-Cramer")
    for j in range(p):
        print("   %d    %-20s %-18s %s"
              % (j, "%14.6f" % onepass[j], f4(twopass[j]), f4(var[j])))
    print("   偏移 %g 下一趟法直接崩(负方差/量级错);增量 25 行一块与两趟法逐位一致"
          % shift)


# --------------------------------------------------------------------- E6

def e6_order(data, p, k=3):
    print("E6 行序敏感度:打乱后 partial_fit 是否等价")
    ref = IncrementalPCA(n_components=k, batch_size=17).fit([r[:] for r in data])
    for seed in [1, 2, 3]:
        r = Rng(seed)
        idx = list(range(len(data)))
        for i in range(len(idx) - 1, 0, -1):              # Fisher-Yates
            j = int(r.uniform() * (i + 1))
            idx[i], idx[j] = idx[j], idx[i]
        m = IncrementalPCA(n_components=k, batch_size=17).fit([data[i][:] for i in idx])
        print("   seed=%d  max|Δmean|=%.3e  max|ΔS|=%.3e  max|Δcomp|=%.3e"
              % (seed, max(abs(a - b) for a, b in zip(m.mean_, ref.mean_)),
                 max(abs(a - b) for a, b in zip(m.singular_values_, ref.singular_values_)),
                 max(abs(abs(m.components_[i][j]) - abs(ref.components_[i][j]))
                     for i in range(k) for j in range(p))))
    print("   -> 均值/方差是与顺序无关的对称统计量(恒在 1e-15),但块的划分变了,")
    print("      截断残差方向的方式也跟着变,故分量会差到 1e-2 量级")


if __name__ == "__main__":
    r0 = Rng(21)
    data = [[2.0 + r0.normal() * (1.0 + 0.3 * j) for j in range(6)] for _ in range(60)]
    e1_uncentered_cost()
    print()
    e2_one_extra_component(block_counts())
    print()
    e3_n_iter()
    print()
    e4_batch(data, 6)
    print()
    e5_youngs_cramer()
    print()
    e6_order(data, 6)
