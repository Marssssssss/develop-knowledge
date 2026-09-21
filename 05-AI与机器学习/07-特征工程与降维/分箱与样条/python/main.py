#!/usr/bin/env python3
"""分箱与样条:6 组实验(KBinsDiscretizer 见 bins_core.py,SplineTransformer 见 spline_core.py)。

跑法: python main.py
权威依据见同目录 ../README.md「参考资料」;所有读数在开发期与 scikit-learn 1.9.1
/ scipy 1.18.1 逐值对拍过(见 selfcheck_bins.py)。
"""

from bins_core import (
    KBinsDiscretizer, percentiles, linspace, drop_narrow_edges,
)
from spline_core import SplineTransformer

# sklearn 文档里 KBinsDiscretizer 的例子数据
DOC_X = [[-2., 1., -4., -1.],
         [-1., 2., -3., -.5],
         [0., 3., -2., .5],
         [1., 4., -1., 2.]]


def _fmt(v):
    if v == float("inf"):
        return "+inf"
    if v == float("-inf"):
        return "-inf"
    return "%.4f" % v


def experiment_strategies():
    """E1:三种 strategy 的箱边界与编码。"""
    print("[E1] 三种 strategy(n_bins=3, data = sklearn 文档例)")
    for st in ("uniform", "quantile", "kmeans"):
        kb = KBinsDiscretizer(n_bins=3, encode="ordinal", strategy=st).fit(DOC_X)
        codes = kb.transform(DOC_X)
        print("     %-9s 列0 边界 = %s" % (st, [_fmt(v) for v in kb.bin_edges_[0]]))
        print("              列0 编码 = %s" % [int(r[0]) for r in codes])
    print("     文档给出的 uniform 编码 = [0,1,2,2],边界 = [-2,-1,0,1]")

    print("     三种 quantile_method 的边界差(单列 [1,2,3],n_bins=3):")
    tri = [[1.], [2.], [3.]]
    for m in ("linear", "averaged_inverted_cdf", "inverted_cdf"):
        kb = KBinsDiscretizer(n_bins=3, encode="ordinal", strategy="quantile",
                              quantile_method=m).fit(tri)
        note = "" if kb.n_bins_[0] == 3 else "  <- 窄箱被剔除,箱数掉到 %d" % kb.n_bins_[0]
        print("       %-22s -> %s   n_bins_=%s%s"
              % (m, [_fmt(v) for v in kb.bin_edges_[0]], kb.n_bins_, note))


def experiment_boundary():
    """E2:边界归属(落在内部边界上的点归上一箱)与越界折叠。"""
    print("[E2] 边界归属与越界折叠(n_bins=4 uniform,x = 0,0.25,...,1)")
    data = [[v] for v in linspace(0.0, 1.0, 11)]
    kb = KBinsDiscretizer(n_bins=4, encode="ordinal", strategy="uniform").fit(data)
    print("     边界 = %s" % [_fmt(v) for v in kb.bin_edges_[0]])
    probes = [-5.0, -0.001, 0.0, 0.25, 0.5, 0.75, 1.0, 1.001, 9.0]
    codes = [int(r[0]) for r in kb.transform([[p] for p in probes])]
    for p, c in zip(probes, codes):
        tag = ""
        if p in (0.25, 0.5, 0.75):
            tag = "  <- 恰在边界上,归上一箱(bisect_right)"
        elif p < 0.0 or p > 1.0:
            tag = "  <- 越界,折进端点箱"
        print("     x = %-8s -> 箱 %d%s" % (p, c, tag))


def experiment_degenerate():
    """E3:常量列与「宽度 <= 1e-8」的窄箱。"""
    print("[E3] 退化情形")
    Xc = [[1., 1.], [1., 2.], [1., 3.]]
    kb = KBinsDiscretizer(n_bins=3, encode="ordinal").fit(Xc)
    print("     常量列(列0 恒为 1):边界 = %s,n_bins_ = %s,transform 全 0"
          % ([_fmt(v) for v in kb.bin_edges_[0]], kb.n_bins_))
    print("       官方警告文案:Feature 0 is constant and will be replaced with 0.")
    print("       代价:inverse_transform 用箱中心 (edges[k]+edges[k+1])/2 还原,")
    print("             而 (-inf + +inf)/2 = nan —— 常量列反变换得到 nan 而不是 1。")

    xd = [[0.], [0.], [0.], [1e-12], [1.], [2.], [3.]]
    for m in ("linear", "averaged_inverted_cdf"):
        kb = KBinsDiscretizer(n_bins=5, encode="ordinal", strategy="quantile",
                              quantile_method=m).fit(xd)
        raw = percentiles([r[0] for r in xd], linspace(0, 100, 6), m)
        print("     7 点含 1e-12,n_bins=5,method=%-22s" % m)
        print("       未过滤分位数 = %s" % [_fmt(v) for v in raw])
        print("       过滤后边界   = %s  n_bins_ = %s"
              % ([_fmt(v) for v in kb.bin_edges_[0]], kb.n_bins_))
    kept, dropped = drop_narrow_edges([0.0, 0.0, 0.0, 1.0, 2.0, 3.0])
    print("     drop_narrow_edges([0,0,0,1,2,3]) -> %s(丢掉了 %d 个)"
          % (kept, 6 - len(kept)))


def experiment_spline_knots():
    """E4:B-spline 的结向量与单位分解。"""
    print("[E4] B-spline 基(n_knots=4,x = linspace(0,1,6))")
    xs = [[v] for v in linspace(0.0, 1.0, 6)]
    for deg in (1, 2, 3):
        st = SplineTransformer(n_knots=4, degree=deg, extrapolation="error").fit(xs)
        rows = st.transform(xs)
        base = st.knots_[0][deg:len(st.knots_[0]) - deg]
        print("     degree=%d  base 结 = %s" % (deg, [_fmt(v) for v in base]))
        print("               完整结 t = %s" % [_fmt(v) for v in st.knots_[0]])
        print("               n_features_out_ = %d = n_knots + degree - 1 = %d"
              % (st.n_features_out_, 4 + deg - 1))
        print("               每行之和 = %s(单位分解 => 隐含截距列)"
              % sorted(set(round(sum(r), 12) for r in rows)))
    print("     端外结不重复首末结,而是沿用首/末两结间距 dist = 1/3")
    print("     (Eilers & Marx 的建议,官方注释里明确否掉了 np.tile 写法)")


def experiment_extrapolation():
    """E5:五种 extrapolation 在端点外的取值。"""
    print("[E5] extrapolation 对照(degree=3,n_knots=4,训练域 [0,1];列 0 的基)")
    xs = [[v] for v in linspace(0.0, 1.0, 6)]
    probes = [-0.5, 0.5, 1.5]
    for ext in ("constant", "linear", "continue", "periodic"):
        st = SplineTransformer(n_knots=4, degree=3, extrapolation=ext).fit(xs)
        print("     %-9s n_features_out_=%d" % (ext, st.n_features_out_))
        for p in probes:
            row = st.transform([[p]])[0]
            print("       x=%-5s -> %s  sum=%.4f"
                  % (p, [_fmt(v) for v in row], sum(row)))
    st = SplineTransformer(n_knots=4, degree=3, extrapolation="error").fit(xs)
    try:
        st.transform([[-0.5]])
        print("     error     未报错(不应发生)")
    except ValueError as e:
        print("     error     x=-0.5 抛 ValueError:%s" % e)
    print("     要点:constant / linear 的越界行仍满足「行和 = 1」;")
    print("           continue 是多项式延拓,行和也保持 1(仍落在基的仿射包上);")
    print("           error 只在越界时报错,域内与 constant 完全相同;")
    print("           periodic 的周期 = base[-1]-base[0] = 1,-0.5 / 0.5 / 1.5 同余")
    print("           于 0.5,所以三行**完全相同**(周期特征不会在 12/31 与 1/1 之间跳变)。")


def experiment_periodic_and_quantile():
    """E6:periodic 结与 quantile 结。"""
    print("[E6] periodic 与 quantile 结")
    xp = [[0.0 + 2 * 3.141592653589793 * i / 8.0] for i in range(9)]
    for nk in (4, 5):
        st = SplineTransformer(n_knots=nk, degree=2, extrapolation="periodic").fit(xp)
        print("     periodic n_knots=%d -> n_features_out_=%d (= n_knots - 1)" % (nk, st.n_features_out_))
        print("       t = %s" % [_fmt(v) for v in st.knots_[0]])
    print("       周期 = base[-1] - base[0] = 2*pi,端外结按周期平移而来")

    xq = [[0.], [0.], [0.], [1.], [2.], [3.], [10.]]
    for kk in ("uniform", "quantile"):
        st = SplineTransformer(n_knots=5, degree=2, knots=kk).fit(xq)
        print("     knots=%-8s t = %s" % (kk, [_fmt(v) for v in st.knots_[0]]))
    print("       quantile 结用 np.nanpercentile(**默认 linear**),与 KBinsDiscretizer")
    print("       默认的 averaged_inverted_cdf 不是同一个口径;base 结允许重复(见上)")


def main():
    for fn in (experiment_strategies, experiment_boundary, experiment_degenerate,
               experiment_spline_knots, experiment_extrapolation,
               experiment_periodic_and_quantile):
        fn()
        print()


if __name__ == "__main__":
    main()
