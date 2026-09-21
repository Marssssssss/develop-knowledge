#!/usr/bin/env python3
"""幂变换与分位数变换:6 组实验(实现见 prep_core.py)。

跑法: python main.py
权威依据见同目录 ../README.md「参考资料」。
"""

import math

from prep_core import (
    boxcox, yeojohnson, boxcox_llf, yeojohnson_llf, argmax_llf,
    boxcox_normmax_pearsonr, skewness, pearson, standardize, QuantileTransformer,
    percentile_linear, percentile_avg_inverted_cdf, normal_ppf, BOUNDS_THRESHOLD,
)

# 一列典型右偏数据(工资/停留时长/点击数的形状):对数正态再平移
from prep_core import linspace  # noqa: E402


def rng_normal(n, mu, sigma, seed=1):
    """确定性标准正态序列(Box-Muller + 线性同余),免得引第三方库。"""
    state = seed & 0xFFFFFFFF
    out = []
    while len(out) < n:
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        u1 = state / 4294967296.0 or 1e-12
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        u2 = state / 4294967296.0
        out.append(mu + sigma * math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2))
    return out[:n]


def lognormal_sample(n=300, seed=3):
    return [math.exp(v) + 0.05 for v in rng_normal(n, 0.9, 0.8, seed)]


def experiment_lambda_sources():
    """E1:同一个 λ,两种口径的差别(最容易踩的坑)。"""
    x = lognormal_sample()
    lam_mine = argmax_llf(x, boxcox_llf, -3.0, 3.0)
    lam_yj = argmax_llf(x, yeojohnson_llf, -5.0, 5.0)
    lam_pr = boxcox_normmax_pearsonr(x, -3.0, 3.0)
    print("[E1] %d 个右偏样本上 λ 的三种口径" % len(x))
    print("     Box-Cox  剖面对数似然极大(MLE)  λ = %+.9f" % lam_mine)
    print("     Box-Cox  概率图相关最大(pearsonr) λ = %+.9f  ← scipy 参数默认" % lam_pr)
    print("     两者相差 Δλ = %.4f,变换结果因此不同" % abs(lam_pr - lam_mine))
    print("     Yeo-Johnson 剖面对数似然          λ = %+.9f" % lam_yj)
    print("     变换后偏度:原 %.4f → BC(MLE) %.4f → BC(pearsonr) %.4f → YJ %.4f"
          % (skewness(x), skewness([boxcox(v, lam_mine) for v in x]),
             skewness([boxcox(v, lam_pr) for v in x]),
             skewness([yeojohnson(v, lam_yj) for v in x])))
    return lam_mine, lam_yj, lam_pr


def experiment_doc_example():
    """E2:sklearn 文档里的那个 3×2 小例子,λ 与输出的对拍锚点。"""
    data = [[1.0, 2.0], [3.0, 2.0], [4.0, 5.0]]
    lams = [argmax_llf([r[j] for r in data], yeojohnson_llf) for j in range(2)]
    print("[E2] sklearn 文档例 lambdas_ = [1.38668182, -3.10053331]")
    print("     我算的 λ = [%.8f, %.8f]" % (lams[0], lams[1]))
    t = [[yeojohnson(r[j], lams[j]) for j in range(2)] for r in data]
    st = [standardize([row[j] for row in t]) for j in range(2)]   # standardize=True
    print("     standardize=True 后的输出 = %s"
          % [[round(st[j][i], 3) for j in range(2)] for i in range(3)])
    print("     第 2 列常数级差异也照样出 λ=%.3f,可见逐列独立估计" % lams[1])
    return lams


def experiment_positivity():
    """E3:Box-Cox 要严格正数,Yeo-Johnson 不要。"""
    x = [3.0, -1.5, 0.0, 2.5, -4.0]
    print("[E3] 含 0 与负数的列 [3, -1.5, 0, 2.5, -4]")
    print("     Yeo-Johnson(λ=0.5) = %s" % ["%.4f" % yeojohnson(v, 0.5) for v in x])
    bad = [v for v in x if v <= 0]
    print("     Box-Cox 在这些点上直接不合法(%d/%d 个点非正)" % (len(bad), len(x)))
    print("     镜像分支自洽性:YJ(x<0, λ) 与 -YJ(-x, 2−λ) 的关系见自检断言")


def experiment_quantile_modes():
    """E4:分位数变换的两种输出分布与两条边界规则。"""
    col = sorted(lognormal_sample(60, 7))
    print("[E4] %d 个样本,n_quantiles=20" % len(col))
    for dist in ("uniform", "normal"):
        qt = QuantileTransformer(n_quantiles=20, output_distribution=dist).fit([col])
        probe = [col[0] / 2, col[0], col[len(col) // 2], col[-1], col[-1] * 10]
        got = qt._transform_col(probe, qt.quantiles_[0])
        print("     %-8s 越界/边界探针 → %s" % (dist, ["%.6f" % v for v in got]))
    qt5 = QuantileTransformer(n_quantiles=5).fit([col])
    landmark_vals = qt5._transform_col(qt5.quantiles_[0], qt5.quantiles_[0])
    print("     n_quantiles=5:5 个地标点(quantiles_)映射到 %s"
          % ["%.6f" % v for v in landmark_vals])
    mid = (col[0] + col[-1]) / 2.0
    v_mid = qt5._transform_col([mid], qt5.quantiles_[0])[0]
    print("     落在地标之间的值按线性插值(如中点 → %.6f),所以台阶只有 5 级"
          % v_mid)
    q10 = 10.0
    print("     口径差异(q=10):linear=%.6f,averaged_inverted_cdf=%.6f"
          % (percentile_linear(col, q10), percentile_avg_inverted_cdf(col, q10)))


def normal_scores(col):
    """经典正态得分(Blom 位置),用作分位数变换的参照物。"""
    order = sorted(range(len(col)), key=lambda i: col[i])
    n = len(col)
    out = [0.0] * n
    for r, i in enumerate(order):
        out[i] = normal_ppf((r + 0.625) / (n + 0.25))
    return out


def experiment_correlation():
    """E5:分位数变换是逐列单调映射,但它对线性相关的破坏是有结构的。"""
    a = rng_normal(400, 0.0, 1.0, 11)
    noise = rng_normal(400, 0.0, 0.5, 12)
    b = [0.9 * a[i] + noise[i] for i in range(400)]
    print("[E5] 两个联合正态列(a~N(0,1), b = 0.9a + 噪声)的各变换皮尔逊相关")
    print("     原始                       %.4f" % pearson(a, b))
    print("     标准化(线性,不变量)       %.4f" % pearson(standardize(a), standardize(b)))
    print("     Yeo-Johnson(逐列 MLE)      %.4f"
          % pearson([yeojohnson(v, 0.0) for v in a], [yeojohnson(v, 0.0) for v in b]))
    for nq in (200, 50, 10, 5):
        qa = QuantileTransformer(nq, "uniform").fit([a]).transform([a])[0]
        qb = QuantileTransformer(nq, "uniform").fit([b]).transform([b])[0]
        na = QuantileTransformer(nq, "normal").fit([a]).transform([a])[0]
        nb = QuantileTransformer(nq, "normal").fit([b]).transform([b])[0]
        print("     nq=%4d 分位数-uniform      %.4f      分位数-normal  %.4f"
              % (nq, pearson(qa, qb), pearson(na, nb)))
    print("     参照:精确正态得分(Blom)    %.4f" % pearson(normal_scores(a), normal_scores(b)))
    print("     ↑ normal 输出偏低的原因:地标位置取 i/(n_quantiles−1) 且两端被夹到 ±5.1993,")
    print("       不是 Blom 位置;nq 越小台阶越粗、压得越多")
    return pearson(a, b)


def experiment_ties():
    """E6:重复值(平台)上,两个方向取平均才是平台中点。"""
    col = [1.0] * 10 + [2.0] * 10 + [3.0] * 10
    qt = QuantileTransformer(n_quantiles=12, quantile_method="averaged_inverted_cdf")
    qt.fit([col])
    got = qt.transform([col])[0]
    spreads = [max(got[k * 10:(k + 1) * 10]) - min(got[k * 10:(k + 1) * 10])
               for k in range(3)]
    print("[E6] 10/10/10 三台阶数据:每个台阶内部映射成同一个值")
    print("     三个台阶的映射值 = %s" % ["%.6f" % got[k * 10] for k in range(3)])
    print("     台阶内极差 = %s(全 0 表示同值)" % ["%.1e" % s for s in spreads])
    print("     quantiles_ 前 4 个 = %s" % ["%.4f" % q for q in qt.quantiles_[0][:4]])
    print("     不同地标值的个数 = %d(平台在分位数上被压平)"
          % len(set("%.9f" % q for q in qt.quantiles_[0])))
    return got


def main():
    experiment_lambda_sources()
    experiment_doc_example()
    experiment_positivity()
    experiment_quantile_modes()
    experiment_correlation()
    experiment_ties()
    print("BOUNDS_THRESHOLD = %g" % BOUNDS_THRESHOLD)
    print("done")


if __name__ == "__main__":
    main()
