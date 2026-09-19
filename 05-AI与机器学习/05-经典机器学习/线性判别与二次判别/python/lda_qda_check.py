# -*- coding: utf-8 -*-
"""LDA / QDA 自检:把 sklearn 用户指南里的公式与源码里的收缩口径逐条钉住。

反例优先:ω_k / ω_k0 记错、白化方向写错、shrinkage 靶心搞混,都会让"两种写法之差不是常数"
或"投影后分不开",所以每一段都拿一个独立算出来的对照组比,而不是只断言单个数值。
"""
import math

import lda_qda as L

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
    st = seed

    def f():
        nonlocal st
        st = (st * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (st >> 11) / float(1 << 53)
    return f


def gauss(r):
    return math.sqrt(-2.0 * math.log(r() + 1e-12)) * math.cos(2 * math.pi * r())


def make(seed, n_per=60, means=((0.0, 0.0), (3.0, 2.0), (-2.0, 3.0)), sd=0.6):
    """共享协方差(各向同性)的三类数据。"""
    r = rnd(seed)
    X, y = [], []
    for c, m in enumerate(means):
        for _ in range(n_per):
            X.append([m[0] + sd * gauss(r), m[1] + sd * gauss(r)])
            y.append(c)
    return X, y


def make_hetero(seed, n_per=80):
    """异方差:类 0 是紧的圆,类 1 是被拉长的椭圆 —— QDA 应该赢。"""
    r = rnd(seed)
    X, y = [], []
    for _ in range(n_per):
        X.append([0.4 * gauss(r), 0.4 * gauss(r)])
        y.append(0)
    for _ in range(n_per):
        X.append([3.0 * gauss(r), 0.25 * gauss(r)])
        y.append(1)
    return X, y


def pca_dir(X):
    """对照用:总协方差的主成分方向(无监督)。"""
    n, d = len(X), len(X[0])
    mu = [sum(x[j] for x in X) / n for j in range(d)]
    cov = [[sum((x[a] - mu[a]) * (x[b] - mu[b]) for x in X) / n for b in range(d)]
           for a in range(d)]
    lam, v = L.power_eig(cov)
    return v, lam


def acc(y_true, y_pred):
    return sum(a == b for a, b in zip(y_true, y_pred)) / len(y_true)


def main():
    print("=" * 74)
    print("线性判别(LDA)与二次判别(QDA)—— 自检")
    print("=" * 74)
    X, y = make(11)

    # A. 两种写法是否只差一个与 k 无关的常数
    print("\n【A】log 后验 ↔ ω_kᵀx + ω_k0(sklearn coef_ / intercept_)")
    m = L.LDA().fit(X, y)
    df = m.decision_function(X)
    lp = L.QDA().fit(X, y)                       # 借用 QDA 的原始形式
    lp.cov_ = [m.covariance_] * len(m.classes_)  # 强行共享协方差,使其等于 LDA 的模型
    lp.means_, lp.priors_, lp.classes_ = m.means_, m.priors_, m.classes_
    lp.log_posterior = L.QDA.log_posterior.__get__(lp)
    raw = lp.log_posterior(X)
    ok = True
    for i in range(len(X)):
        diffs = [raw[i][k] - df[i][k] for k in range(3)]
        if max(diffs) - min(diffs) > 1e-9:
            ok = False
    check("A1 两种写法对每个样本都只差一个与类别无关的常数", ok,
          f"首样本常数项 = {raw[0][0] - df[0][0]:.9f}")
    w = L.chol_solve(m._L, m.means_[1])
    check("A2 ω_k = Σ⁻¹μ_k(与直接解方程一致)",
          all(abs(sum(m.covariance_[a][j] * w[j] for j in range(2)) - m.means_[1][a]) < 1e-10
              for a in range(2)), "")
    pred = m.predict(X)
    check("A3 argmax 与按定义算后验的 argmax 一致",
          pred == [max(range(3), key=lambda k: r[k]) for r in raw], "")

    # B. 共享协方差时 LDA 与 QDA 表现相当,但 LDA 参数更少
    print("\n【B】共享协方差下 LDA ≈ QDA")
    q = L.QDA().fit(X, y)
    check("B1 训练集上两者精度都很高", acc(y, m.predict(X)) > 0.95 and acc(y, q.predict(X)) > 0.95,
          f"LDA={acc(y, m.predict(X)):.3f}, QDA={acc(y, q.predict(X)):.3f}")
    print(f"   [信息] 参数量:LDA = 2 均值 + 1 协方差(3 自由参数)= 7;"
          f" QDA = 2 均值 + 2 协方差(各 3)= 10")

    # C. QDA(对角) ≡ GaussianNB
    print("\n【C】QDA 假设协方差为对角 ⇔ GaussianNB")
    qd = L.QDA(diagonal=True, reg=0.0).fit(X, y)
    nb = L.GaussianNB().fit(X, y)
    for k in range(len(nb.classes_)):
        for j in range(2):
            nb.vars_[k][j] = qd.cov_[k][j][j]     # 对齐方差(含/不含 reg 的口径)
    pq, pn = qd.predict(X), nb.predict(X)
    check("C1 逐样本预测完全相同", pq == pn, f"一致率={acc(pq, pn):.3f}")
    # 对角协方差下 log|Σ| = Σ log v_j、马氏距离 = Σ (x−μ)²/v,二者只差文档里被塞进 Cst 的
    # −½·d·log2π 这一项 ⇒ 差值必须对所有类别都相同,且恰好等于 ½·d·log(2π)
    A, B = qd.log_posterior(X), nb.joint_log_likelihood(X)
    diffs = [A[i][k] - B[i][k] for i in range(len(X)) for k in range(3)]
    cst = 0.5 * len(X[0]) * math.log(2 * math.pi)
    check("C2 两者只差常数 ½·d·log2π(文档里被吸收进 Cst 的那一项)",
          max(diffs) - min(diffs) < 1e-9 and abs(diffs[0] - cst) < 1e-9,
          f"差值={diffs[0]:.9f}, ½·d·log2π={cst:.9f}")
    orders = [tuple(sorted(range(3), key=lambda k: -r[k])) for r in qd.log_posterior(X)]
    orders_nb = [tuple(sorted(range(3), key=lambda k: -r[k])) for r in nb.joint_log_likelihood(X)]
    check("C3 三类后验的大小排序一致", orders == orders_nb, "")

    # D. 降维上限 K−1
    print("\n【D】LDA 降维:输出维度至多 K−1")
    X5, y3 = [], []
    r = rnd(5)
    for c, m in enumerate(((0, 0, 0, 0, 0), (2, 0, 1, 0, 0), (0, 3, 0, 0, 0))):
        for _ in range(50):
            X5.append([m[j] + 0.3 * gauss(r) for j in range(5)])
            y3.append(c)
    m5 = L.LDA().fit(X5, y3)
    check("D1 K=3 时只有 2 个判别分量", len(m5.scalings_) == 2, f"{len(m5.scalings_)}")
    M = [[sum(m5.priors_[k] * (L.chol_solve(m5._L, m5.means_[k])[a] -
                               sum(m5.priors_[t] * L.chol_solve(m5._L, m5.means_[t])[a]
                                   for t in range(3))) *
              (L.chol_solve(m5._L, m5.means_[k])[b] -
               sum(m5.priors_[t] * L.chol_solve(m5._L, m5.means_[t])[b] for t in range(3)))
              for k in range(3)) for b in range(5)] for a in range(5)]
    for lam, v in zip(m5.explained_variance_, m5.scalings_):
        M = L.deflate(M, lam, v)
    lam3, _ = L.power_eig(M)
    check("D2 第 3 个特征值 ≈ 0(类均值张成的空间只有 K−1 维)", abs(lam3) < 1e-8,
          f"λ3={lam3:.3e}")
    Z = m5.transform(X5)
    check("D3 transform 后每个样本是 2 维", all(len(z) == 2 for z in Z), "")

    # E. shrinkage 的靶心
    print("\n【E】shrinkage:(1−γ)Σ + γ·(tr Σ / p)·I")
    cov = [[4.0, 1.0], [1.0, 2.0]]
    s0 = L.shrunk_covariance(cov, 0.0)
    check("E1 γ=0 → 原协方差", all(abs(s0[i][j] - cov[i][j]) < 1e-12 for i in range(2)
                                   for j in range(2)), "")
    s1 = L.shrunk_covariance(cov, 1.0)
    mu = (4.0 + 2.0) / 2
    check("E2 γ=1 → 平均方差 × 单位阵(非对角归零,对角相等)",
          abs(s1[0][1]) < 1e-12 and abs(s1[0][0] - mu) < 1e-12 and abs(s1[1][1] - mu) < 1e-12,
          f"diag=({s1[0][0]:.3f}, {s1[1][1]:.3f}), 平均方差={mu:.3f}")
    sh = L.shrunk_covariance(cov, 0.5)
    check("E3 γ=0.5 → 严格在两者中间",
          all(abs(sh[i][j] - ((1 - 0.5) * cov[i][j] + (0.5 * mu if i == j else 0.0))) < 1e-12
              for i in range(2) for j in range(2)), "")
    print("   —— 口径提醒:用户指南说 γ=1 得到「the diagonal matrix of variances」,"
          "\n      源码 `_shrunk_covariance.py` 的靶心其实是 tr(Σ)/p·I(各维方差被抹平成均值)")

    # F. LDA 方向 vs PCA 方向
    print("\n【F】有监督 vs 无监督:判别方向与方差方向可以完全不同")
    r2 = rnd(33)
    Xf, yf = [], []
    for c, shift in ((0, -1.2), (1, 1.2)):      # 类间差异在 x2 方向
        for _ in range(80):
            Xf.append([4.0 * gauss(r2), shift + 0.3 * gauss(r2)])   # x1 方差远大于 x2
            yf.append(c)
    mf = L.LDA().fit(Xf, yf)
    pv, plam = pca_dir(Xf)
    lv = mf.directions_[0]
    check("F1 PCA 第一主成分几乎是 x1 方向(方差最大)",
          abs(pv[0]) > 0.98, f"PCA = ({pv[0]:.3f}, {pv[1]:.3f})")
    check("F2 LDA 判别方向几乎是 x2 方向(类间差异所在)",
          abs(lv[1]) > 0.98, f"LDA = ({lv[0]:.3f}, {lv[1]:.3f})")
    def proj_acc(v):
        z = [sum(v[j] * x[j] for j in range(2)) for x in Xf]
        thr = (sum(z[i] for i in range(len(z)) if yf[i] == 0) / 80 +
               sum(z[i] for i in range(len(z)) if yf[i] == 1) / 80) / 2
        return sum((z[i] < thr) == (yf[i] == 0) for i in range(len(z))) / len(z)
    check("F3 沿 LDA 方向投影后两类完全可分(准确率 == 1)", abs(proj_acc(lv) - 1.0) < 1e-12,
          f"{proj_acc(lv):.3f}")
    check("F4 沿 PCA 方向投影后几乎分不开(≈ 随机)", proj_acc(pv) < 0.65, f"{proj_acc(pv):.3f}")

    # G. 异方差:QDA 更灵活
    print("\n【G】异方差数据:QDA 的二次边界更贴合")
    Xh, yh = make_hetero(17)
    lh, qh = L.LDA().fit(Xh, yh), L.QDA().fit(Xh, yh)
    al, aq = acc(yh, lh.predict(Xh)), acc(yh, qh.predict(Xh))
    check("G1 QDA 精度不低于 LDA", aq >= al - 1e-12, f"LDA={al:.3f}, QDA={aq:.3f}")
    print(f"   [信息] LDA 强制共享协方差 → 只能画直线;QDA 每类一个椭圆 → 可画二次曲线")

    print("\n" + "-" * 74)
    print(f"断言 {TOTAL[1]}/{TOTAL[0]} 通过")
    if FAILS:
        print("失败项:" + ", ".join(FAILS))
        raise SystemExit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
