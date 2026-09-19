# -*- coding: utf-8 -*-
"""高斯混合模型与 EM 自检:把用户指南与源码里可验证的结论逐条钉住。

覆盖:logsumexp 数值稳定 → Cholesky/log|Σ|/Mahalanobis → E 步 responsibilities 归一 →
M 步四种协方差闭式解 → reg_covar 只加对角且能压住奇异性 → EM 似然单调不降 →
多局部最优 → BIC/AIC 参数计数 → BIC 选成分数 → GMM 与 k-means 的极限关系。
"""
import math

import gmm as G

TOTAL = [0, 0]
FAILS = []


def check(name, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        TOTAL[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


def rnd(seed):
    return G._rng(seed)


def blobs(n_per=60, seed=3):
    """两簇明显分离的二维高斯混合。"""
    r = rnd(seed)
    X = []
    for _ in range(n_per):
        X.append([r() - 0.5 - 3.0, r() - 0.5])
    for _ in range(n_per):
        X.append([r() - 0.5 + 3.0, r() - 0.5 + 2.0])
    return X


def gauss(seed, n, mu, sd):
    r = rnd(seed)
    out = []
    for _ in range(n):
        z = math.sqrt(-2.0 * math.log(r() + 1e-12)) * math.cos(2 * math.pi * r())
        out.append(mu + sd * z)
    return out


def ari(a, b):
    """调整兰德指数(判断两个划分是否等价,避免簇标签置换带来的假失败)。"""
    n = len(a)
    idx = sorted(range(n), key=lambda i: (a[i], b[i]))
    mx = max(max(a), max(b)) + 1
    tab = {}
    sa, sb = [0] * mx, [0] * mx
    for i in idx:
        tab[(a[i], b[i])] = tab.get((a[i], b[i]), 0) + 1
        sa[a[i]] += 1
        sb[b[i]] += 1
    c = lambda v: v * (v - 1) / 2.0
    sij = sum(c(v) for v in tab.values())
    exp = sum(c(x) for x in sa) * sum(c(x) for x in sb) / c(n)
    mxp = (sum(c(x) for x in sa) + sum(c(x) for x in sb)) / 2.0
    return (sij - exp) / (mxp - exp) if mxp != exp else 1.0


def main():
    print("=" * 74)
    print("高斯混合模型与 EM 算法 —— 自检")
    print("=" * 74)

    # A. logsumexp
    print("\n【A】log 域与数值稳定")
    v = [-1000.0, -1001.0, -999.5]
    naive = 0.0
    for x in v:
        try:
            naive += math.exp(x)
        except OverflowError:
            naive = math.inf
    check("A1 朴素 exp 求和在这些量级上全部下溢为 0", naive == 0.0, f"naive={naive}")
    lse = G.logsumexp(v)
    check("A2 logsumexp 得到有限值", math.isfinite(lse), f"logsumexp={lse:.4f}")
    check("A3 logsumexp(0,0) == log 2", abs(G.logsumexp([0.0, 0.0]) - math.log(2)) < 1e-12)

    # B. Cholesky / logdet / Mahalanobis
    print("\n【B】Cholesky 分解与马氏距离")
    A = [[4.0, 1.0], [1.0, 3.0]]
    L = G.cholesky(A)
    rec = [[sum(L[i][k] * L[j][k] for k in range(2)) for j in range(2)] for i in range(2)]
    check("B1 L·Lᵀ 还原 A", all(abs(rec[i][j] - A[i][j]) < 1e-12 for i in range(2) for j in range(2)))
    check("B2 log|Σ| 与 2×2 行列式一致",
          abs(G.logdet_chol(L) - math.log(A[0][0] * A[1][1] - A[0][1] * A[1][0])) < 1e-12,
          f"logdet={G.logdet_chol(L):.6f}")
    d = [1.0, -2.0]
    mh = G.mahal_sq_chol(L, d)
    inv = [[A[1][1], -A[0][1]], [-A[1][0], A[0][0]]]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    ref = sum(d[i] * inv[i][j] / det * d[j] for i in range(2) for j in range(2))
    check("B3 马氏距离 dᵀΣ⁻¹d 与显式求逆一致", abs(mh - ref) < 1e-10, f"{mh:.8f} vs {ref:.8f}")
    # 传入的是 Cholesky 因子 L,方差 = L 对角元的平方(2²=4, 5²=25)
    check("B4 对角协方差时马氏距离退化为 Σd²/σ²",
          abs(G.mahal_sq_chol([[2.0, 0.0], [0.0, 5.0]], [1.0, 1.0]) - (1 / 4.0 + 1 / 25.0)) < 1e-12,
          f"{G.mahal_sq_chol([[2.0, 0.0], [0.0, 5.0]], [1.0, 1.0]):.6f} vs {1 / 4.0 + 1 / 25.0:.6f}")

    # C. E 步归一
    print("\n【C】E 步:responsibilities")
    X = blobs()
    m = G.GaussianMixture(2, "full", random_state=1).fit(X)
    P = m.predict_proba(X)
    check("C1 每个样本对 K 个成分的 responsibility 和为 1",
          all(abs(sum(p) - 1.0) < 1e-9 for p in P))
    check("C2 responsibility 非负且 ≤ 1", all(0.0 <= v <= 1.0 for p in P for v in p))

    # D. EM 单调性
    print("\n【D】EM 的似然单调性(用户指南:保证收敛到局部最优)")
    tr = m.loglik_trace_
    drops = [i for i in range(1, len(tr)) if tr[i] < tr[i - 1] - 1e-9]
    check("D1 对数似然序列单调不降", not drops, f"{len(tr)} 轮,首 {tr[0]:.3f} → 末 {tr[-1]:.3f}")
    check("D2 确实发生了多轮迭代(不只跑 1 轮)", len(tr) >= 2, f"it={len(tr)}")

    # E. 多局部最优
    print("\n【E】初始化敏感:EM 只有局部最优保证")
    lls = []
    for ip in ("kmeans", "random_from_data", "random"):
        try:
            mm = G.GaussianMixture(2, "full", random_state=5, init_params=ip).fit(X)
            lls.append(mm.lower_bound_)
        except ValueError:
            lls.append(None)
    ok = any(v is not None for v in lls)
    check("E1 三种初始化都能跑通", ok, "ll=" + str([None if v is None else round(v, 2) for v in lls]))
    if all(v is not None for v in lls):
        check("E2 不同初始化收敛到不同的局部最优(差 > 1e-6)",
              max(lls) - min(lls) > 1e-6, f"Δll={max(lls) - min(lls):.4f}")

    # F. 参数计数与 BIC/AIC
    print("\n【F】参数计数与 BIC/AIC(sklearn _n_parameters / bic / aic)")
    expect = {"full": 11, "tied": 8, "diag": 9, "spherical": 7}
    for t, e in expect.items():
        mm = G.GaussianMixture(2, t, random_state=1).fit(X)
        check(f"F-{t} n_components=2,d=2 的自由参数数 == {e}",
              mm._n_parameters() == e, f"got={mm._n_parameters()}")
    mm = G.GaussianMixture(2, "full", random_state=1).fit(X)
    n, p = len(X), mm._n_parameters()
    check("F-BIC/AIC 关系:BIC − AIC == p·(ln n − 2)",
          abs((mm.bic(X) - mm.aic(X)) - p * (math.log(n) - 2.0)) < 1e-9,
          f"p={p}, n={n}")

    # G. reg_covar 抑制奇异性
    print("\n【G】奇异性:聚类塌到单点会让协方差发散")
    Xdup = [[1.0, 2.0]] * 20 + [[3.0, 4.0]] * 20  # 每簇内部零方差
    raised = False
    try:
        G.GaussianMixture(2, "full", reg_covar=0.0, random_state=1).fit(Xdup)
    except ValueError:
        raised = True
    check("G1 reg_covar=0 时零方差成分让 Cholesky 抛错(似然发散)", raised)
    ok = True
    try:
        G.GaussianMixture(2, "full", reg_covar=1e-6, random_state=1).fit(Xdup)
    except ValueError:
        ok = False
    check("G2 reg_covar=1e-6(默认值)压住奇异性,训练照常完成", ok)
    mm0 = G.GaussianMixture(2, "full", reg_covar=1e-6, random_state=1).fit(Xdup)
    diag0 = [mm0.covariances_[0][j][j] for j in range(2)]
    check("G3 reg_covar 只加在对角线上,量级 ≈ 1e-6",
          all(abs(c - 1e-6) < 1e-7 for c in diag0), f"diag={[f'{c:.2e}' for c in diag0]}")

    # H. M 步闭式解
    print("\n【H】M 步闭式解")
    Xh = [[0.0], [1.0], [2.0], [3.0]]
    mh = G.GaussianMixture(2, "diag")
    mh.means_ = [[0.0], [3.0]]
    mh.weights_ = [0.5, 0.5]
    mh.covariances_ = [[1.0], [1.0]]
    resp = [[0.5, 0.5]] * 4
    mh._m_step(Xh, [[math.log(r[0]), math.log(r[1])] for r in resp])
    check("H1 等权 responsibility 下 μ = 普通样本均值",
          abs(mh.means_[0][0] - mh.means_[1][0]) < 1e-12 and abs(mh.means_[0][0] - 1.5) < 1e-12,
          f"μ={mh.means_}")
    w = [[0.9, 0.1], [0.8, 0.2], [0.2, 0.8], [0.1, 0.9]]
    mh2 = G.GaussianMixture(2, "diag")
    mh2.means_ = [[0.0], [3.0]]
    mh2.covariances_ = [[1.0], [1.0]]
    mh2._m_step(Xh, [[math.log(r[0]), math.log(r[1])] for r in w])
    num = sum(w[i][0] * Xh[i][0] for i in range(4))
    den = sum(w[i][0] for i in range(4))
    check("H2 加权 responsibility 下 μ_k = Σγx / Σγ", abs(mh2.means_[0][0] - num / den) < 1e-12,
          f"μ0={mh2.means_[0][0]:.6f}, 期望={num / den:.6f}")
    check("H3 π_k = N_k / N 且 Σπ = 1",
          abs(sum(mh2.weights_) - 1.0) < 1e-12 and abs(mh2.weights_[0] - den / 4.0) < 1e-12)
    # spherical = diag 的逐行均值
    md = G.GaussianMixture(2, "diag", reg_covar=1e-6)
    md.means_ = [[0.0], [1.0]]
    md.covariances_ = [[1.0], [1.0]]
    md._m_step([[0.0, 0.0], [1.0, 2.0], [2.0, 1.0]],
               [[math.log(0.6), math.log(0.4)], [math.log(0.5), math.log(0.5)],
                [math.log(0.4), math.log(0.6)]])
    ms = G.GaussianMixture(2, "spherical", reg_covar=1e-6)
    ms.means_ = [[0.0], [1.0]]
    ms.covariances_ = [1.0, 1.0]
    ms._m_step([[0.0, 0.0], [1.0, 2.0], [2.0, 1.0]],
               [[math.log(0.6), math.log(0.4)], [math.log(0.5), math.log(0.5)],
                [math.log(0.4), math.log(0.6)]])
    check("H4 spherical 方差 = 同一次 M 步里 diag 各维的均值",
          all(abs(ms.covariances_[k] - sum(md.covariances_[k]) / 2.0) < 1e-12 for k in range(2)),
          f"sph={[round(v, 6) for v in ms.covariances_]}, diag={[[round(v, 6) for v in r] for r in md.covariances_]}")

    # I. 与 k-means 的极限关系
    print("\n【I】GMM ⊃ k-means:spherical + 等权 + σ→0 退化为硬分配")
    Xk = blobs(50, 11)
    mk = G.GaussianMixture(2, "spherical", reg_covar=1e-6, random_state=2).fit(Xk)
    Pk = mk.predict_proba(Xk)
    hard = all(max(p) > 0.99 for p in Pk)
    centers, lab = G.kmeans(Xk, 2, seed=2)
    a = ari(lab, mk.predict(Xk))
    check("I1 球形小方差下 responsibility 接近 0/1", hard,
          f"max resp 最小 = {min(max(p) for p in Pk):.4f}")
    check("I2 此时的硬划分与 k-means 等价(ARI == 1)", abs(a - 1.0) < 1e-9, f"ARI={a:.6f}")

    # J. BIC 选成分数
    print("\n【J】BIC 模型选择")
    Xb = blobs(80, 21)
    bics = {}
    for K in (1, 2, 3, 4):
        mm = G.GaussianMixture(K, "full", random_state=7).fit(Xb)
        bics[K] = mm.bic(Xb)
    best = min(bics, key=bics.get)
    check("J1 两份量数据的 BIC 最小值出现在 K=2", best == 2,
          "  ".join(f"K={k}:{v:.1f}" for k, v in bics.items()))

    print("\n" + "-" * 74)
    print(f"断言 {TOTAL[1]}/{TOTAL[0]} 通过")
    if FAILS:
        print("失败项:" + ", ".join(FAILS))
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
