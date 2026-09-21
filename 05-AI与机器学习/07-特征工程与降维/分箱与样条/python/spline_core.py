"""SplineTransformer 的纯标准库实现(B-spline 基、结扩展、外推)。

对齐对象:scikit-learn 1.9.1 `sklearn/preprocessing/_polynomial.py` 的
`SplineTransformer` 与 scipy 的 `BSpline`。

三个必须记住的官方口径:
  1. n_splines = n_knots + degree - 1(periodic 时为 n_knots - 1)
  2. 端外结**不重复**首末结,而是沿用首/末两结的间距(Eilers & Marx 的建议)
  3. bases 在数据范围内恒满足「单位分解」:每行之和 = 1(隐含截距列)
"""

import math

from bins_core import linspace, percentile_linear

# --------------------------------------------------------------------------
# Cox-de Boor 递推:B_{i,0} 为区间指示函数,再逐阶升
#   分母为 0 时该项取 0(重结情形,官方同样如此)
# --------------------------------------------------------------------------

def _cox_de_boor(t, degree, x, left_closed=False):
    """left_closed=True 时 degree-0 基用 `t[i] < x <= t[i+1]`(取左极限)。"""
    n = len(t) - degree - 1
    if degree == 0:
        if left_closed:
            return [1.0 if t[i] < x <= t[i + 1] else 0.0 for i in range(n)]
        return [1.0 if t[i] <= x < t[i + 1] else 0.0 for i in range(n)]
    prev = _cox_de_boor(t, degree - 1, x, left_closed)
    out = []
    for i in range(n):
        d1 = t[i + degree] - t[i]
        d2 = t[i + degree + 1] - t[i + 1]
        a = (x - t[i]) / d1 * prev[i] if d1 > 0 else 0.0
        b = (t[i + degree + 1] - x) / d2 * prev[i + 1] if d2 > 0 else 0.0
        out.append(a + b)
    return out


def _lagrange(nodes, values, x):
    total = 0.0
    for i, xi in enumerate(nodes):
        term = values[i]
        for j, xj in enumerate(nodes):
            if j != i:
                term *= (x - xj) / (xi - xj)
        total += term
    return total


def bspline_values(t, degree, x, extrapolate=False):
    """长度 = len(t) - degree - 1 的基函数值向量。

    区间内直接递推;区间外且 extrapolate=True 时,在端点所在 span 内取
    degree+1 个采样点做拉格朗日延拓 —— 该 span 上每个基函数都是次数 <= degree
    的多项式,degree+1 个值足以唯一确定它,因此结果是**精确的**多项式延拓。
    """
    n = len(t) - degree - 1
    if extrapolate and degree >= 1 and (x < t[degree] or x > t[n]):
        # 注意上下两端用的 span **不对称**(与 scipy `find_interval` 一致):
        #   下端 x < t[k]   -> span [t[k],  t[k+1]]
        #   上端 x > t[n]   -> span [t[n-1], t[n]]
        if x < t[degree]:
            lo, hi = t[degree], t[degree + 1]
        else:
            lo, hi = t[n - 1], t[n]
        if hi <= lo:
            return [0.0] * n
        span = hi - lo
        nodes = [lo + span * (k + 1) / (degree + 2) for k in range(degree + 1)]
        cols = [_cox_de_boor(t, degree, nd) for nd in nodes]
        return [_lagrange(nodes, [c[i] for c in cols], x) for i in range(n)]
    return _cox_de_boor(t, degree, x)


def bspline_derivative(t, degree, x):
    """一阶导:d/dx B_{i,k} = k*(B_{i,k-1}/(t_{i+k}-t_i) - B_{i+1,k-1}/(t_{i+k+1}-t_{i+1}))。

    与 scipy 的唯一分歧点在**最右结 t[n]** 上:scipy 在此取左单侧导。
    k>=2 时一阶导在内部结连续(degree-1 基连续),取哪侧都一样;
    只有 k=1 的 B-spline 在结处是 C^0,方向会直接改变 extrapolation='linear' 的斜率
    —— 所以这里对 x == t[n] 用「左闭」的 degree-0 指示函数。
    """
    n = len(t) - degree - 1
    if degree == 0:
        return [0.0] * n
    low = _cox_de_boor(t, degree - 1, x, left_closed=(x == t[n]))   # 长度 n+1
    out = []
    for i in range(n):
        d1 = t[i + degree] - t[i]
        d2 = t[i + degree + 1] - t[i + 1]
        a = low[i] / d1 if d1 > 0 else 0.0
        b = low[i + 1] / d2 if d2 > 0 else 0.0
        out.append(degree * (a - b))
    return out


# --------------------------------------------------------------------------
# 结位置与结扩展
# --------------------------------------------------------------------------

def base_knot_positions(col, n_knots, knots="uniform"):
    if not isinstance(knots, str):
        return list(knots)
    if knots == "quantile":
        levels = linspace(0.0, 100.0, n_knots)
        return [percentile_linear(col, lv / 100.0) for lv in levels]
    return linspace(min(col), max(col), n_knots)


def build_knot_vector(base, degree, extrapolation="constant"):
    """返回 (t, n_splines)。"""
    if extrapolation == "periodic":
        n_splines = len(base) - 1
        period = base[-1] - base[0]
        below = [base[i] - period for i in range(len(base) - degree - 1, len(base) - 1)]
        above = [base[i] + period for i in range(1, degree + 1)]
        return below + list(base) + above, n_splines
    n_splines = len(base) + degree - 1
    dist_min = base[1] - base[0]
    dist_max = base[-1] - base[-2]
    below = linspace(base[0] - degree * dist_min, base[0] - dist_min, degree)
    above = linspace(base[-1] + dist_max, base[-1] + degree * dist_max, degree)
    return below + list(base) + above, n_splines


# --------------------------------------------------------------------------

class SplineTransformer:
    """逐列生成 B-spline 基。X 为 list[list[float]]。"""

    def __init__(self, n_knots=5, degree=3, knots="uniform",
                 extrapolation="constant", include_bias=True):
        if extrapolation not in ("error", "constant", "linear", "continue", "periodic"):
            raise ValueError("bad extrapolation")
        self.n_knots = n_knots
        self.degree = degree
        self.knots = knots
        self.extrapolation = extrapolation
        self.include_bias = include_bias
        self.knots_ = None
        self.n_splines_ = None
        self.n_features_out_ = None

    def fit(self, X):
        n_features = len(X[0])
        self.knots_ = []
        for j in range(n_features):
            col = [row[j] for row in X]
            base = base_knot_positions(col, self.n_knots, self.knots)
            if len(base) < 2:
                raise ValueError("Number of knots, knots.shape[0], must be >= 2.")
            t, n_splines = build_knot_vector(base, self.degree, self.extrapolation)
            if self.extrapolation == "periodic" and len(base) <= self.degree:
                raise ValueError("Periodic splines require degree < n_knots.")
            self.knots_.append(t)
            self.n_splines_ = n_splines
        # 与 sklearn 一致:include_bias=False 时 n_features_out_ 少一列(丢掉每列最后一个基)
        per_feature = self.n_splines_ if self.include_bias else self.n_splines_ - 1
        self.n_features_out_ = n_features * per_feature
        return self

    # -- 单列、单个样本值 -> 长度 n_splines 的列表 -------------------------
    def _column_row(self, j, x):
        t = self.knots_[j]
        d = self.degree
        n_s = self.n_splines_
        xmin, xmax = t[d], t[-d - 1]
        if self.extrapolation == "periodic":
            span = t[len(t) - d - 1] - t[d]              # = 周期,不是 t[n_s]-t[d]
            xx = t[d] + (x - t[d]) % span if span > 0 else 0.0
            full = bspline_values(t, d, xx, True)        # 长度 n_splines + degree
            return [full[i] + full[n_s + i] for i in range(d)] + full[d:n_s]
        if self.extrapolation == "continue":
            return bspline_values(t, d, x, True)
        if self.extrapolation == "error":
            if x < xmin or x > xmax:
                raise ValueError("`X` contains values beyond the limits of the knots.")
            return bspline_values(t, d, x)
        row = bspline_values(t, d, min(max(x, xmin), xmax))
        if self.extrapolation == "constant":
            if x < xmin:
                fmin = bspline_values(t, d, xmin)
                return fmin[:d] + [0.0] * (n_s - d)
            if x > xmax:
                fmax = bspline_values(t, d, xmax)
                return [0.0] * (n_s - d) + fmax[-d:]
            return row
        # linear:只有首/末 degree 个基非零,按边界导数线性延拓
        steps = d + 1 if d <= 1 else d
        if x < xmin:
            fmin = bspline_values(t, d, xmin)
            fpm = bspline_derivative(t, d, xmin)
            out = [0.0] * n_s
            for k in range(steps):
                out[k] = fmin[k] + (x - xmin) * fpm[k]
            return out
        if x > xmax:
            fmax = bspline_values(t, d, xmax)
            fpm = bspline_derivative(t, d, xmax)
            out = [0.0] * n_s
            for k in range(steps):
                idx = n_s - 1 - k
                out[idx] = fmax[idx] + (x - xmax) * fpm[idx]
            return out
        return row

    def transform(self, X):
        out = []
        for row in X:
            enc = []
            for j, x in enumerate(row):
                block = self._column_row(j, x)
                if self.include_bias:
                    enc.extend(block)
                else:
                    enc.extend(block[:-1])          # 丢掉每列最后一个基
            out.append(enc)
        return out
