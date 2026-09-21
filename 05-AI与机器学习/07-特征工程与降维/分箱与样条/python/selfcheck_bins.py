#!/usr/bin/env python3
"""分箱与样条自检:判据来自 sklearn / scipy 源码与 numpy 分位数定义。

凡标「对拍」的期望值,都是开发期用 scikit-learn 1.9.1 + scipy 1.18.1 + numpy 2.5.3
逐值比对后写死的(88 项全绿),见 README「与官方实现的对拍」。
"""

import math

from bins_core import (
    KBinsDiscretizer, linspace, drop_narrow_edges, bin_edges_uniform,
    bin_edges_quantile, bin_edges_kmeans, percentiles,
    percentile_linear, percentile_avg_inverted_cdf, percentile_inverted_cdf,
)
from spline_core import (
    SplineTransformer, bspline_values, bspline_derivative, build_knot_vector,
    base_knot_positions,
)
from main import DOC_X, experiment_strategies

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def close(a, b, tol, label):
    ok(abs(a - b) <= tol, "%s: 期望 %.12g ± %.3g,实得 %.12g" % (label, b, tol, a))


def closev(a, b, tol, label):
    ok(len(a) == len(b), "%s: 长度 %d vs %d" % (label, len(a), len(b)))
    for i, (x, y) in enumerate(zip(a, b)):
        close(x, y, tol, "%s[%d]" % (label, i))


# ------------------------------------------------------------------ 1. 分位数口径
close(percentile_linear([1.0, 2.0, 3.0], 1.0 / 3), 5.0 / 3, 1e-12,
      "linear(numpy 默认)在 1/3 处 = 5/3 [对拍]")
close(percentile_avg_inverted_cdf([1.0, 2.0, 3.0], 1.0 / 3), 1.5, 1e-12,
      "averaged_inverted_cdf 在 1/3 处 = 1.5 [对拍]")
close(percentile_inverted_cdf([1.0, 2.0, 3.0], 1.0 / 3), 1.0, 0.0,
      "inverted_cdf 在 1/3 处 = 1(离散口径)[对拍]")
ok(percentile_linear([1.0, 2.0, 3.0], 1.0 / 3)
   != percentile_avg_inverted_cdf([1.0, 2.0, 3.0], 1.0 / 3),
   "负向:两口径在 1/3 处确实不同")
close(percentile_avg_inverted_cdf([1.0, 2.0, 3.0], 0.0), 1.0, 1e-12,
      "q=0 越下界 → 夹到最小值 [对拍]")
close(percentile_avg_inverted_cdf([1.0, 2.0, 3.0], 1.0), 3.0, 1e-12,
      "q=1 越上界 → 夹到最大值 [对拍]")
close(percentile_linear([1.0, 2.0, 3.0], 0.0), 1.0, 1e-12, "linear 在 q=0 给最小值")
close(percentile_linear([1.0, 2.0, 3.0], 1.0), 3.0, 1e-12, "linear 在 q=1 给最大值")

xd7 = [0.0, 0.0, 0.0, 1e-12, 1.0, 2.0, 3.0]
lv = linspace(0.0, 100.0, 6)
closev(percentiles(xd7, lv, "linear"), [0.0, 0.0, 4e-13, 0.6, 1.8, 3.0], 1e-12,
       "7 点 linear 分位数 [对拍]")
closev(percentiles(xd7, lv, "averaged_inverted_cdf"), [0.0, 0.0, 0.0, 1.0, 2.0, 3.0],
       1e-12, "7 点 averaged_inverted_cdf 分位数 [对拍]")
closev(percentiles(xd7, lv, "inverted_cdf"), [0.0, 0.0, 0.0, 1.0, 2.0, 3.0], 1e-12,
       "7 点 inverted_cdf 分位数 [对拍]")
mono = percentiles([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0], linspace(0, 100, 9), "linear")
ok(all(a <= b for a, b in zip(mono, mono[1:])), "分位数随 q 单调不减")

# ------------------------------------------------------------------ 2. 箱边界
closev(bin_edges_uniform([1.0, 2.0, 3.0, 4.0], 3), [1.0, 2.0, 3.0, 4.0], 1e-12,
       "uniform 边界 = min..max 等分")
closev(bin_edges_quantile([1.0, 2.0, 3.0], 3, "linear"), [1.0, 5.0 / 3, 7.0 / 3, 3.0],
       1e-12, "quantile 边界(linear)[对拍]")
closev(bin_edges_quantile([1.0, 2.0, 3.0], 3, "averaged_inverted_cdf"),
       [1.0, 1.5, 2.5, 3.0], 1e-12, "quantile 边界(默认口径)[对拍]")
closev(bin_edges_quantile([1.0, 2.0, 3.0], 3, "inverted_cdf"), [1.0, 1.0, 2.0, 3.0],
       1e-12, "quantile 边界(inverted_cdf)首两结重复 → 由 fit 过滤 [对拍]")
km = bin_edges_kmeans([0.0 + i / 10.0 for i in range(11)], 4)
closev(km, [0.0, 0.25, 0.525, 0.775, 1.0], 1e-12,
       "1D k-means 收敛到 [0.25,0.525,0.775],**不是**等分 [对拍]")
ok(len(km) - 1 == 4, "kmeans 得到 4 个箱")
ok(abs(km[2] - 0.5) > 1e-3,
   "负向:kmeans 策略里内部边界并不落在等分点上(0.525 != 0.5)")

# ------------------------------------------------------------------ 3. 编码与边界归属
kb = KBinsDiscretizer(n_bins=3, encode="ordinal", strategy="uniform").fit(DOC_X)
closev(kb.bin_edges_[0], [-2.0, -1.0, 0.0, 1.0], 1e-12, "文档例列0 边界 [对拍]")
ok([int(r[0]) for r in kb.transform(DOC_X)] == [0, 1, 2, 2], "文档例列0 编码 [对拍]")
ok([int(r[1]) for r in kb.transform(DOC_X)] == [0, 1, 2, 2], "文档例列1 编码 [对拍]")
ok([int(r[2]) for r in kb.transform(DOC_X)] == [0, 1, 2, 2], "文档例列2 编码 [对拍]")
ok([int(r[3]) for r in kb.transform(DOC_X)] == [0, 0, 1, 2], "文档例列3 编码 [对拍]")

kbu = KBinsDiscretizer(n_bins=4, encode="ordinal", strategy="uniform").fit(
    [[v] for v in linspace(0.0, 1.0, 11)])
pr = [-5.0, -0.001, 0.0, 0.25, 0.5, 0.75, 1.0, 1.001, 9.0]
codes = [int(r[0]) for r in kbu.transform([[p] for p in pr])]
ok(codes == [0, 0, 0, 1, 2, 3, 3, 3, 3], "越界折叠 + 边界归上一箱 [对拍]")
ok(codes[0] == codes[3] - 1, "负向:远低于下界的值没有报错,而是折进箱 0")
ok(codes[7] == codes[8] == 3, "负向:高于上界的值全折进最后一箱,彼此不再可分")

# ------------------------------------------------------------------ 4. 常量列与窄箱
Xc = [[1.0, 1.0], [1.0, 2.0], [1.0, 3.0]]
kbc = KBinsDiscretizer(n_bins=3, encode="ordinal").fit(Xc)
ok(kbc.bin_edges_[0] == [-math.inf, math.inf], "常量列边界 = [-inf, +inf]")
ok(kbc.n_bins_[0] == 1, "常量列只留 1 个箱")
ok(all(int(r[0]) == 0 for r in kbc.transform(Xc)), "常量列全部编码为 0")
inv = kbc.inverse_transform(kbc.transform(Xc))
ok(all(math.isnan(r[0]) for r in inv), "常量列反变换得到 nan((-inf + +inf)/2)[对拍]")
ok(all(r[1] == r[1] for r in inv), "负向:非退化列反变换不是 nan")

kbn = KBinsDiscretizer(n_bins=5, encode="ordinal", strategy="quantile",
                       quantile_method="linear").fit([[v] for v in xd7])
closev(kbn.bin_edges_[0], [0.0, 0.6, 1.8, 3.0], 1e-12, "窄箱被剔除后的边界(linear)[对拍]")
ok(kbn.n_bins_[0] == 3, "n_bins 从 5 掉到 3")
kbn2 = KBinsDiscretizer(n_bins=5, encode="ordinal", strategy="quantile").fit(
    [[v] for v in xd7])
closev(kbn2.bin_edges_[0], [0.0, 1.0, 2.0, 3.0], 1e-12,
       "默认口径下同样掉到 3 箱,但边界值不同 [对拍]")
ok(kbn.bin_edges_[0] != kbn2.bin_edges_[0], "负向:两口径过滤后的边界确实不等")

kept, dropped = drop_narrow_edges([0.0, 0.0, 0.0, 1.0, 2.0, 3.0])
ok(kept == [0.0, 1.0, 2.0, 3.0] and dropped, "过滤宽度 <= 1e-8 的箱")
ok(drop_narrow_edges([-math.inf, math.inf]) == ([-math.inf, math.inf], False),
   "负向:常量列的 [-inf,+inf] 不会被当成窄箱丢掉")
ok(drop_narrow_edges([5.0, 5.0 + 1e-12])[0] == [5.0],
   "首元素永不丢弃(对应 ediff1d(to_begin=inf))")

# ------------------------------------------------------------------ 5. onehot 与反变换
kbo = KBinsDiscretizer(n_bins=3, encode="onehot-dense", strategy="uniform").fit(DOC_X)
enc = kbo.transform(DOC_X)
ok(all(sum(r) == 4.0 for r in enc), "onehot-dense:每行 4 列各命中 1 个箱,行和 = 4")
ok(len(enc[0]) == 12, "onehot-dense 宽度 = 4 列 x 3 箱")
ok(kbo.inverse_transform(enc) == kbo.inverse_transform(kbo.transform(DOC_X)),
   "onehot 与 ordinal 路径的反变换一致")
back = kbo.inverse_transform(enc)
close(back[0][0], -1.5, 1e-12, "反变换取箱中心:列0 箱0 = (-2 + -1)/2")
ok(back != DOC_X, "负向:反变换不等于原始数据(分箱不可逆)")

# ------------------------------------------------------------------ 6. B-spline 基与结
for deg in (1, 2, 3):
    t, ns = build_knot_vector([0.0, 1.0 / 3, 2.0 / 3, 1.0], deg)
    ok(ns == 4 + deg - 1, "degree=%d:n_splines = n_knots + degree - 1" % deg)
    ok(len(t) == 4 + 2 * deg, "degree=%d:结向量长度 = n_knots + 2*degree" % deg)
    ok(abs(t[0] - (0.0 - deg / 3.0)) < 1e-12,
       "degree=%d:下端外结 = base[0] - degree*dist(不是重复 base[0])" % deg)
    ok(abs(t[-1] - (1.0 + deg / 3.0)) < 1e-12,
       "degree=%d:上端外结 = base[-1] + degree*dist" % deg)
    for x in (0.0, 0.2, 0.5, 0.9, 1.0):
        row = bspline_values(t, deg, x)
        ok(len(row) == ns, "degree=%d,x=%.1f:基函数个数" % (deg, x))
        close(sum(row), 1.0, 1e-12, "degree=%d,x=%.1f:单位分解(行和 = 1)" % (deg, x))
t3, _ = build_knot_vector([0.0, 1.0 / 3, 2.0 / 3, 1.0], 3)
closev(t3, [-1.0, -2.0 / 3, -1.0 / 3, 0.0, 1.0 / 3, 2.0 / 3, 1.0, 4.0 / 3, 5.0 / 3, 2.0],
       1e-12, "degree=3 的完整结向量 [对拍]")

xq = [0.0, 0.0, 0.0, 1.0, 2.0, 3.0, 10.0]
closev(base_knot_positions(xq, 5, "uniform"), [0.0, 2.5, 5.0, 7.5, 10.0], 1e-12,
       "uniform base 结 [对拍]")
closev(base_knot_positions(xq, 5, "quantile"), [0.0, 0.0, 1.0, 2.5, 10.0], 1e-12,
       "quantile base 结(默认 linear 口径)[对拍]")
tq, nq = build_knot_vector([0.0, 0.0, 1.0, 2.5, 10.0], 2)
closev(tq, [0.0, 0.0, 0.0, 0.0, 1.0, 2.5, 10.0, 17.5, 25.0], 1e-12,
       "quantile 结允许重复,端外结仍按 dist 外推 [对拍]")
ok(nq == 6, "base 有 5 个结、degree=2 → n_splines = 5 + 2 - 1 = 6")

# ------------------------------------------------------------------ 7. 外推
xs = [[v] for v in linspace(0.0, 1.0, 6)]
probe = [-0.5, 0.5, 1.5]
rows = {}
for ext in ("constant", "linear", "continue", "periodic"):
    st = SplineTransformer(n_knots=4, degree=3, extrapolation=ext).fit(xs)
    rows[ext] = [st.transform([[p]])[0] for p in probe]
    if ext != "periodic":
        ok(st.n_features_out_ == 6, "%s:n_features_out_ = 6" % ext)
closev(rows["constant"][0], [1 / 6, 2 / 3, 1 / 6, 0.0, 0.0, 0.0], 1e-12,
       "constant 在 x=-0.5 处 = 边界值 f_min [对拍]")
closev(rows["constant"][2], [0.0, 0.0, 0.0, 1 / 6, 2 / 3, 1 / 6], 1e-12,
       "constant 在 x=1.5 处 = 边界值 f_max [对拍]")
closev(rows["linear"][0], [11.0 / 12, 2.0 / 3, -7.0 / 12, 0.0, 0.0, 0.0], 1e-12,
       "linear 在 x=-0.5 处 = f_min + (x-xmin)*f'_min [对拍]")
closev(rows["linear"][2], [0.0, 0.0, 0.0, -7.0 / 12, 2.0 / 3, 11.0 / 12], 1e-12,
       "linear 在 x=1.5 处镜像 [对拍]")
closev(rows["continue"][0], [2.604166666666667, -3.270833333333333,
                             2.229166666666667, -0.5625, 0.0, 0.0], 1e-9,
       "continue 在 x=-0.5 处 = 多项式延拓 [对拍]")
ok(rows["continue"][0] != rows["constant"][0], "负向:continue 与 constant 结果不同")
ok(rows["linear"][0] != rows["constant"][0], "负向:linear 与 constant 结果不同")
for ext in ("constant", "linear", "continue"):
    for i, p in enumerate(probe):
        close(sum(rows[ext][i]), 1.0, 1e-12,
              "%s 在 x=%.1f 处行和仍为 1" % (ext, p))
ok(rows["constant"][1] == rows["linear"][1] == rows["continue"][1],
   "负向:域内三个模式给出完全相同的行")
closev(rows["periodic"][1], [0.4791666666666667, 0.04166666666666666,
                             0.4791666666666667], 1e-9, "periodic 在 x=0.5 处 [对拍]")
for i in range(3):
    close(sum(rows["periodic"][i]), 1.0, 1e-12, "periodic 行和 = 1(x=%s)" % probe[i])

st_err = SplineTransformer(n_knots=4, degree=3, extrapolation="error").fit(xs)
try:
    st_err.transform([[-0.5]])
    ok(False, "error 模式应当抛 ValueError")
except ValueError:
    ok(True, "error 模式在越界时抛 ValueError")

st_nb = SplineTransformer(n_knots=4, degree=3, include_bias=False).fit(xs)
st_b = SplineTransformer(n_knots=4, degree=3, include_bias=True).fit(xs)
ok(st_nb.n_features_out_ == 5 and st_b.n_features_out_ == 6, "include_bias 少一列")
ok([r[:5] for r in st_b.transform(xs)] == st_nb.transform(xs),
   "include_bias=False 丢掉的正是每列最后一个基")

# ------------------------------------------------------------------ 8. 结的一阶导
for deg in (1, 2, 3):
    t, _ = build_knot_vector([0.0, 1.0 / 3, 2.0 / 3, 1.0], deg)
    n = len(t) - deg - 1
    xmin, xmax = t[deg], t[-deg - 1]
    dmin = bspline_derivative(t, deg, xmin)
    dmax = bspline_derivative(t, deg, xmax)
    if deg == 1:
        closev(dmin, [-3.0, 3.0, 0.0, 0.0], 1e-12, "degree=1:左端导数 [对拍]")
        closev(dmax, [0.0, 0.0, -3.0, 3.0], 1e-12,
               "degree=1:右端取左单侧导(k>=2 时两侧相同)[对拍]")
    if deg == 3:
        closev(dmax, [0.0, 0.0, 0.0, -1.5, 0.0, 1.5], 1e-12, "degree=3:右端导数 [对拍]")
    ok(len(dmin) == n and len(dmax) == n, "degree=%d:导数长度 = 基函数个数" % deg)
    close(sum(dmin), 0.0, 1e-12, "degree=%d:端点导数之和为 0(单位分解求导)" % deg)
    close(sum(dmax), 0.0, 1e-12, "degree=%d:右端导数之和为 0" % deg)

experiment_strategies()
print("PASS %d" % PASS)
