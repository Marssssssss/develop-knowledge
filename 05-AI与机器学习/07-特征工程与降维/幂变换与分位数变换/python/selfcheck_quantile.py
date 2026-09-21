#!/usr/bin/env python3
"""幂变换与分位数变换自检:判据来自 scipy 源码/文档与 sklearn 1.9.1 实测。

标了「对拍」的期望值是开发期用 scipy 1.18.1 + scikit-learn 1.9.1 逐值比对后写死的。
"""

import math

from prep_core import (
    boxcox, yeojohnson, boxcox_llf, yeojohnson_llf, argmax_llf,
    boxcox_normmax_pearsonr, filliben_medians, normal_ppf,
    percentile_linear, percentile_avg_inverted_cdf, linspace, np_interp,
    QuantileTransformer, skewness, pearson, standardize, BOUNDS_THRESHOLD,
)
from main import rng_normal, normal_scores, lognormal_sample

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def close(a, b, tol, label):
    if math.isinf(a) or math.isinf(b):
        ok(a == b, "%s: 期望 %r,实得 %r" % (label, b, a))
        return
    ok(abs(a - b) <= tol, "%s: 期望 %.12g ± %.3g,实得 %.12g" % (label, b, tol, a))


# ------------------------------------------------- 1. 两个变换的公式与分支
close(boxcox(4.0, 1.0), 3.0, 1e-12, "Box-Cox λ=1 → x−1")
close(boxcox(4.0, 0.0), math.log(4.0), 1e-12, "Box-Cox λ=0 → log x")
close(boxcox(4.0, 0.5), 2.0, 1e-12, "Box-Cox λ=0.5 → (√x − 1)/0.5")
close(yeojohnson(4.0, 0.0), math.log(5.0), 1e-12, "YJ x≥0,λ=0 → log(x+1)")
close(yeojohnson(-3.0, 2.0), -math.log(4.0), 1e-12, "YJ x<0,λ=2 → −log(1−x)")
close(yeojohnson(-3.0, 0.5), -14.0 / 3.0, 1e-12,
      "YJ x<0,λ=0.5 → −((4^1.5 − 1)/1.5) = −14/3")
ok(abs(yeojohnson(-2.5, 0.3) + yeojohnson(2.5, 1.7)) < 1e-12,
   "负半轴是正半轴的镜像:YJ(−x, λ) = −YJ(x, 2−λ)")
try:
    boxcox(0.0, 0.0)
except ValueError:
    ok(True, "Box-Cox 在 0 上抛 ValueError(log 定义域)")
else:
    raise AssertionError("Box-Cox(0, 0) 应当抛 ValueError")
try:
    boxcox(-1.0, 0.5)
except ValueError:
    ok(True, "Box-Cox 在负数上抛 ValueError —— 故只接受严格正数")
else:
    raise AssertionError("Box-Cox(−1, 0.5) 应当抛 ValueError")
ok(not math.isnan(yeojohnson(-1.0, 0.5)), "Yeo-Johnson 吃负数")

# ------------------------------------------------- 2. 剖面对数似然与 λ
x = lognormal_sample(300, 3)
lam_mle = argmax_llf(x, boxcox_llf, -3.0, 3.0)
lam_pr = boxcox_normmax_pearsonr(x, -3.0, 3.0)
close(lam_mle, -0.053531488556, 1e-6, "对拍 scipy:boxcox() 的 MLE λ")
close(lam_pr, -0.055428393977, 1e-6, "对拍 scipy:boxcox_normmax() 默认 pearsonr λ")
ok(abs(lam_mle - lam_pr) > 1e-4,
   "两个入口的 λ 不同(Δ=%.1e):似然极大 ≠ 概率图相关最大" % abs(lam_mle - lam_pr))
close(argmax_llf(x, yeojohnson_llf, -5.0, 5.0), -0.469904379, 1e-6,
      "对拍 scipy:yeojohnson() 的 λ")
close(boxcox_llf(0.5, x), boxcox_llf(0.5, x), 0.0, "llf 是纯函数")
ok(boxcox_llf(lam_mle, x) > boxcox_llf(lam_mle + 0.2, x), "λ 处确实是极大值(右)")
ok(boxcox_llf(lam_mle, x) > boxcox_llf(lam_mle - 0.2, x), "λ 处确实是极大值(左)")
ok(skewness(x) > 3.0 and abs(skewness([boxcox(v, lam_mle) for v in x])) < 0.01,
   "右偏 3.35 经 Box-Cox 后偏度降到 1e-2 以内")
ok(abs(skewness([yeojohnson(v, argmax_llf(x, yeojohnson_llf)) for v in x])) < 0.05,
   "Yeo-Johnson 同样把偏度压到 5e-2 以内")

# 文档例子:sklearn PowerTransformer 的 lambdas_ 与输出
data = [[1.0, 2.0], [3.0, 2.0], [4.0, 5.0]]
lams = [argmax_llf([r[j] for r in data], yeojohnson_llf) for j in range(2)]
close(lams[0], 1.38668182, 1e-5, "对拍 sklearn:文档例第 1 列 λ")
close(lams[1], -3.10053331, 1e-5, "对拍 sklearn:文档例第 2 列 λ")
t = [[yeojohnson(r[j], lams[j]) for j in range(2)] for r in data]
st = [standardize([row[j] for row in t]) for j in range(2)]
close(st[0][0], -1.316, 5e-4, "对拍 sklearn:standardize=True 的第 1 个输出")

# ------------------------------------------------- 3. Filliben 与百分位口径
v = filliben_medians(4)
close(v[0] + v[-1], 1.0, 1e-15, "Filliben:v[0] = 1 − v[-1]")
close(v[-1], 0.5 ** 0.25, 1e-15, "v[-1] = 0.5^(1/n)")
close(v[1], (2 - 0.3175) / (4 + 0.365), 1e-15, "中间位置 = (i−0.3175)/(n+0.365)")
ok(all(v[i] < v[i + 1] for i in range(3)), "Filliben 中位数严格递增")
close(normal_ppf(0.0), -math.inf, 0.0, "ppf(0) = −inf")
close(normal_ppf(1.0), math.inf, 0.0, "ppf(1) = +inf")
close(normal_ppf(0.5), 0.0, 1e-15, "ppf(0.5) = 0")

s4 = [1.0, 2.0, 3.0, 4.0]
close(percentile_linear(s4, 50.0), 2.5, 1e-12, "手算 linear q=50:h=1.5 → 2.5")
close(percentile_avg_inverted_cdf(s4, 50.0), 2.5, 1e-12, "手算 avg_inv q=50:pos=2 整除 → 2.5")
close(percentile_linear(s4, 25.0), 1.75, 1e-12, "手算 linear q=25 → 1.75")
close(percentile_avg_inverted_cdf(s4, 25.0), 1.5, 1e-12, "手算 avg_inv q=25 → 1.5")
col60 = sorted(lognormal_sample(60, 7))
ok(abs(percentile_linear(col60, 10.0) - percentile_avg_inverted_cdf(col60, 10.0)) > 1e-3,
   "两个口径在 q=10 上不同(%.6f vs %.6f)"
   % (percentile_linear(col60, 10.0), percentile_avg_inverted_cdf(col60, 10.0)))
close(percentile_linear(col60, 50.0), percentile_avg_inverted_cdf(col60, 50.0), 1e-12,
      "两者在中位数上恰好重合")
close(np_interp(5.0, [0.0, 1.0, 2.0], [10.0, 20.0, 30.0]), 30.0, 1e-12, "np.interp 右越界夹取")
close(np_interp(-5.0, [0.0, 1.0], [10.0, 20.0]), 10.0, 1e-12, "np.interp 左越界夹取")
close(np_interp(0.5, [0.0, 1.0], [10.0, 20.0]), 15.0, 1e-12, "np.interp 内点线性插值")

# ------------------------------------------------- 4. 分位数变换的行为
qt = QuantileTransformer(n_quantiles=1000).fit([list(range(10))])
ok(qt.n_quantiles_ == 10, "1.9 口径:n_quantiles 被样本数封顶")
close(qt.references_[0], 0.0, 1e-15, "references_ 起点 0")
close(qt.references_[-1], 1.0, 1e-15, "references_ 终点 1")

col = sorted(lognormal_sample(60, 7))
qt5 = QuantileTransformer(n_quantiles=5).fit([col])
landmark = qt5._transform_col(qt5.quantiles_[0], qt5.quantiles_[0])
close(max(abs(a - b) for a, b in zip(landmark, [0.0, 0.25, 0.5, 0.75, 1.0])), 0.0, 1e-12,
      "5 个地标点严格映射到 0/0.25/0.5/0.75/1")
qt20 = QuantileTransformer(n_quantiles=20).fit([col])
probe = [col[0] / 2, col[0], col[-1], col[-1] * 10]
got_u = qt20._transform_col(probe, qt20.quantiles_[0])
ok(got_u[0] == 0.0 and got_u[1] == 0.0, "低于最小值 → 0(uniform 用严格相等判边界)")
ok(got_u[2] == 1.0 and got_u[3] == 1.0, "高于最大值 → 1")
got_n = QuantileTransformer(20, "normal").fit([col])._transform_col(
    probe, qt20.quantiles_[0])
close(got_n[0], -5.199337582605575, 1e-12, "对拍 scipy:normal 输出越界被夹到 ppf(1e-7−eps)")
close(got_n[2], 5.1993375827, 1e-9, "上界:ppf(1 − (1e-7 − eps))")
ok(abs(got_n[2] + got_n[0]) > 1e-12,
   "上下夹取限不是严格对称的(|hi+lo| = %.1e):ppf(1−x) ≠ −ppf(x) 在双精度下成立"
   % abs(got_n[2] + got_n[0]))
ok(abs(got_n[1] - got_n[0]) < 1e-12, "等于最小值也被夹到同一点(normal 用阈值比较)")

tied = [1.0] * 10 + [2.0] * 10 + [3.0] * 10
qtt = QuantileTransformer(12, quantile_method="averaged_inverted_cdf").fit([tied])
gt = qtt.transform([tied])[0]
close(gt[0], 0.0, 1e-12, "平台 1 映射到 0")
close(gt[15], 0.5, 1e-12, "平台 2 映射到 0.5(两个方向插值取平均的结果)")
close(gt[25], 1.0, 1e-12, "平台 3 映射到 1")
ok(all(max(gt[k * 10:(k + 1) * 10]) - min(gt[k * 10:(k + 1) * 10]) == 0.0 for k in range(3)),
   "同一平台内的取值完全相同")
ok(BOUNDS_THRESHOLD == 1e-7, "BOUNDS_THRESHOLD = 1e-7(sklearn 源码常量)")

# ------------------------------------------------- 5. 线性相关在两个族上的不同命运
a = rng_normal(400, 0.0, 1.0, 11)
noise = rng_normal(400, 0.0, 0.5, 12)
b = [0.9 * a[i] + noise[i] for i in range(400)]
raw = pearson(a, b)
close(pearson(standardize(a), standardize(b)), raw, 1e-12,
      "标准化是线性变换,皮尔逊相关严格不变")
qa = QuantileTransformer(400, "normal").fit([a]).transform([a])[0]
qb = QuantileTransformer(400, "normal").fit([b]).transform([b])[0]
close(pearson(qa, qb), 0.8341, 1e-3, "对拍:normal 输出(q=400,带端点夹取)的相关")
close(pearson(normal_scores(a), normal_scores(b)), 0.8612, 1e-3,
      "对拍:Blom 精确正态得分的相关")
ok(pearson(qa, qb) < raw - 0.02,
   "分位数变换的 normal 输出把线性相关压低(doc 只说 may distort,这里是实测)")
ok(abs(pearson(QuantileTransformer(200, "uniform").fit([a]).transform([a])[0],
               QuantileTransformer(200, "uniform").fit([b]).transform([b])[0]) - raw) < 0.01,
   "uniform 输出几乎不动相关系数")

print("PASS %d" % PASS)
