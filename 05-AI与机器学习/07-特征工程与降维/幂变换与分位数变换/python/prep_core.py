#!/usr/bin/env python3
"""幂变换与分位数变换的核心实现(纯标准库,便于与 scipy/sklearn 逐值对拍)。

覆盖三件东西:
  * Box-Cox(1964)         —— 只接受严格正数
  * Yeo-Johnson(2000)     —— 正负都吃,负半轴用 2−λ 的镜像分支
  * QuantileTransformer   —— 经验 CDF → 均匀分布 → 目标分布的逆 CDF

λ 一律用**剖面对数似然**(scipy 的 `*_llf`,已丢掉与 λ 无关的常数项)的极大化来定。
"""

import math
from bisect import bisect_left, bisect_right
from statistics import NormalDist

BOUNDS_THRESHOLD = 1e-7          # sklearn.preprocessing._data.BOUNDS_THRESHOLD
SPACING_1 = 2.220446049250313e-16   # numpy.spacing(1)
_NORM = NormalDist()


def normal_ppf(p):
    """标准正态分位函数;0 与 1 上给 ∓inf,与 scipy.stats.norm.ppf 一致。"""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    return _NORM.inv_cdf(p)


# --------------------------------------------------------------------------
# 1. 两种幂变换及其剖面对数似然
# --------------------------------------------------------------------------


def boxcox(x, lam):
    """y = (x^λ − 1)/λ(λ≠0);y = log x(λ=0)。要求 x > 0。

    口径说明:scipy 有两条路——`scipy.special.boxcox` 对 x<0 返回 nan、x==0 且 λ<0 返回 −inf;
    `scipy.stats.boxcox` 则**直接要求数据为正**(否则 ValueError)。本实现按后者,
    非正数一律抛 ValueError。若不加这道闸,Python 的 `(-1.0)**0.5` 会**静默**返回复数。
    """
    if x <= 0.0:
        raise ValueError("Box-Cox 只接受严格正数,实得 %r" % (x,))
    if lam == 0.0:
        return math.log(x)
    return (x ** lam - 1.0) / lam


def yeojohnson(x, lam):
    """Yeo-Johnson 的四分支:正半轴用 λ,负半轴用 2−λ 的镜像。"""
    if x >= 0.0:
        if lam == 0.0:
            return math.log(x + 1.0)
        return ((x + 1.0) ** lam - 1.0) / lam
    if lam == 2.0:
        return -math.log(-x + 1.0)
    return -(((-x + 1.0) ** (2.0 - lam) - 1.0) / (2.0 - lam))


def boxcox_llf(lam, x):
    """scipy.stats.boxcox_llf:l = (λ−1)Σlog(x) − N/2·log(Σ(y−ȳ)²/N)。

    这是**剖面**似然(把 σ² 也优化掉了),与 λ 无关的常数项已丢弃,
    所以数值本身是负数、量纲也不重要 —— 只有它的**极大值位置**有意义。
    """
    n = len(x)
    y = [boxcox(v, lam) for v in x]
    mu = math.fsum(y) / n
    var = math.fsum((v - mu) ** 2 for v in y) / n
    if var <= 0.0:
        return -math.inf
    return (lam - 1.0) * math.fsum(math.log(v) for v in x) - n / 2.0 * math.log(var)


def yeojohnson_llf(lam, x):
    """scipy.stats.yeojohnson_llf:l = −N/2·log(σ̂²) + (λ−1)Σ sign(x)·log(|x|+1)。"""
    n = len(x)
    y = [yeojohnson(v, lam) for v in x]
    mu = math.fsum(y) / n
    var = math.fsum((v - mu) ** 2 for v in y) / n
    if var <= 0.0:
        return -math.inf
    s = math.fsum(math.copysign(math.log(abs(v) + 1.0), v) for v in x)
    return -n / 2.0 * math.log(var) + (lam - 1.0) * s


def argmax_llf(x, llf, lo=-5.0, hi=5.0, iters=200):
    """黄金分割求极点(剖面似然关于 λ 单峰,故可如此找)。"""
    invphi = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - invphi * (b - a), a + invphi * (b - a)
    fc, fd = llf(c, x), llf(d, x)
    for _ in range(iters):
        if fc > fd:
            b, d, fd = d, c, fc
            c = b - invphi * (b - a)
            fc = llf(c, x)
        else:
            a, c, fc = c, d, fd
            d = a + invphi * (b - a)
            fd = llf(d, x)
        if b - a < 1e-12:
            break
    return (a + b) / 2.0


def filliben_medians(n):
    """Filliben(1975)对均匀分布次序统计量中位数的近似,scipy 原样实现:

        v[-1] = 0.5^(1/n);v[0] = 1 − v[-1];v[i] = (i − 0.3175)/(n + 0.365),i = 2..n−1
    """
    v = [0.0] * n
    v[-1] = 0.5 ** (1.0 / n)
    v[0] = 1.0 - v[-1]
    for i in range(2, n):
        v[i - 1] = (i - 0.3175) / (n + 0.365)
    return v


def boxcox_normmax_pearsonr(x, lo=-3.0, hi=3.0):
    """复刻 scipy.stats.boxcox_normmax 的**默认** method='pearsonr'。

    目标不是极大似然,而是让概率图上「期望正态次序统计量」与「排序后的变换值」
    的皮尔逊相关最大(源码把目标写成 1−r 再最小化)。所以它与 boxcox() 的 MLE
    λ 是**两条不同的路**,数值一般不相等。
    """
    xvals = [normal_ppf(v) for v in filliben_medians(len(x))]
    obj = lambda lam, d: pearson(xvals, sorted(boxcox(v, lam) for v in d))  # noqa: E731
    return argmax_llf(x, obj, lo, hi)


# --------------------------------------------------------------------------
# 2. 基本统计量
# --------------------------------------------------------------------------


def mean(x):
    return math.fsum(x) / len(x)


def stdev(x, ddof=0):
    m = mean(x)
    return math.sqrt(math.fsum((v - m) ** 2 for v in x) / (len(x) - ddof))


def skewness(x):
    """偏度 g1 = m3 / m2^1.5(总体矩口径,与 scipy.stats.skew(bias=True) 一致)。"""
    m = mean(x)
    m2 = math.fsum((v - m) ** 2 for v in x) / len(x)
    m3 = math.fsum((v - m) ** 3 for v in x) / len(x)
    return m3 / m2 ** 1.5


def pearson(a, b):
    """皮尔逊相关系数(衡量「线性」关系,分位数变换会把它压掉)。"""
    ma, mb = mean(a), mean(b)
    num = math.fsum((u - ma) * (v - mb) for u, v in zip(a, b))
    da = math.sqrt(math.fsum((u - ma) ** 2 for u in a))
    db = math.sqrt(math.fsum((v - mb) ** 2 for v in b))
    return num / (da * db)


def standardize(x):
    m, s = mean(x), stdev(x)
    return [(v - m) / s for v in x]


# --------------------------------------------------------------------------
# 3. 分位数:averaged_inverted_cdf 与 QuantileTransformer
# --------------------------------------------------------------------------


def percentile_linear(sorted_x, q):
    """numpy.percentile 的默认口径(method="linear",Hyndman-Fan type 7):

        h = (n−1)·q/100,取 x[⌊h⌋] + (h−⌊h⌋)·(x[⌊h⌋+1] − x[⌊h⌋])

    **sklearn 1.9.1 的 QuantileTransformer 用的就是这一口径**;主干 1.10 起改成了
    averaged_inverted_cdf。两者在样本量小时差异可达 1e-1 量级,引用时必须写明版本。
    """
    n = len(sorted_x)
    if n == 1:
        return sorted_x[0]
    h = (n - 1) * q / 100.0
    lo = math.floor(h)
    if lo >= n - 1:
        return sorted_x[-1]
    return sorted_x[lo] + (h - lo) * (sorted_x[lo + 1] - sorted_x[lo])


def percentile_avg_inverted_cdf(sorted_x, q):
    """numpy.percentile(method="averaged_inverted_cdf"),q 取 0..100。

    sklearn 的 KBinsDiscretizer(1.9 起)与主干版 QuantileTransformer 都用这一口径:
    把 n·q/100 落在两个次序统计量之间时取平均,恰好落在整数上时取相邻两个的均值。
    """
    n = len(sorted_x)
    if q <= 0.0:
        return sorted_x[0]
    if q >= 100.0:
        return sorted_x[-1]
    pos = n * q / 100.0
    lo = math.floor(pos)
    frac = pos - lo
    if frac == 0.0:
        if lo == 0:
            return sorted_x[0]
        return (sorted_x[lo - 1] + sorted_x[lo]) / 2.0
    return sorted_x[min(lo, n - 1)]


def linspace(start, stop, num, endpoint=True):
    if num == 1:
        return [start]
    step = (stop - start) / (num - 1) if endpoint else (stop - start) / num
    return [start + i * step for i in range(num)]


def np_interp(x, xp, fp):
    """np.interp:xp 升序,区间外按端点夹取;xp 有重复时与 numpy 同样取左侧区间。"""
    if x <= xp[0]:
        return fp[0]
    if x >= xp[-1]:
        return fp[-1]
    i = bisect_right(xp, x) - 1
    if i >= len(xp) - 1:
        return fp[-1]
    x0, x1 = xp[i], xp[i + 1]
    if x1 == x0:
        return fp[i]
    w = (x - x0) / (x1 - x0)
    return fp[i] * (1.0 - w) + fp[i + 1] * w


class QuantileTransformer:
    """对齐 sklearn.preprocessing.QuantileTransformer 的正变换部分。

    quantile_method 默认 "linear",即 **sklearn 1.9.1 的行为**;
    主干 1.10 起改为 "averaged_inverted_cdf",可用参数切换。
    """

    def __init__(self, n_quantiles=1000, output_distribution="uniform",
                 quantile_method="linear"):
        self.n_quantiles = n_quantiles
        self.output_distribution = output_distribution
        self.quantile_method = quantile_method

    def _percentile(self, sorted_col, q):
        if self.quantile_method == "linear":
            return percentile_linear(sorted_col, q)
        return percentile_avg_inverted_cdf(sorted_col, q)

    def fit(self, columns):
        n = len(columns[0])
        # 1.9 及以前:n_quantiles 会被样本数封顶(1.10 起不再封顶)
        self.n_quantiles_ = min(self.n_quantiles, n)
        self.references_ = linspace(0.0, 1.0, self.n_quantiles_, endpoint=True)
        levels = [r * 100.0 for r in self.references_]
        self.quantiles_ = []
        for col in columns:
            s = sorted(col)
            self.quantiles_.append([self._percentile(s, q) for q in levels])
        return self

    def _transform_col(self, col, quantiles):
        refs = self.references_
        out = []
        for x in col:
            if self.output_distribution == "normal":
                lo_idx = x - BOUNDS_THRESHOLD < quantiles[0]
                hi_idx = x + BOUNDS_THRESHOLD > quantiles[-1]
            else:
                lo_idx = x == quantiles[0]
                hi_idx = x == quantiles[-1]
            # 两个方向各插值一次再取平均:重复值(平台)才不会只取到上/下沿
            fwd = np_interp(x, quantiles, refs)
            rev = np_interp(-x, [-q for q in reversed(quantiles)],
                            [-r for r in reversed(refs)])
            v = 0.5 * (fwd - rev)
            if hi_idx:
                v = 1.0
            if lo_idx:
                v = 0.0
            if self.output_distribution == "normal":
                # 顺序与 sklearn 一致:先做 ppf(0 与 1 会得到 ∓inf),再 clip 到有限值
                v = normal_ppf(v)
                lo = normal_ppf(BOUNDS_THRESHOLD - SPACING_1)
                hi = normal_ppf(1.0 - (BOUNDS_THRESHOLD - SPACING_1))
                v = min(max(v, lo), hi)
            out.append(v)
        return out

    def transform(self, columns):
        return [self._transform_col(col, q)
                for col, q in zip(columns, self.quantiles_)]
