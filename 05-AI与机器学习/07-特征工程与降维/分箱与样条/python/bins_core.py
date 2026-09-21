"""KBinsDiscretizer 的纯标准库实现(无 numpy / sklearn 依赖)。

对齐对象:scikit-learn 1.9.1 `sklearn/preprocessing/_discretization.py`
以及 numpy 的 `np.percentile` 三种离散/连续口径定义。

关键口径(全部在 README §一 有出处):
  * bin_edges_ 的首尾**只服务 inverse_transform**;transform 时把边界摊成 ±inf
  * transform 用 `bisect_right(edges[1:-1], x)` —— 恰好落在内部边界上的点归**上**一箱
  * 宽度 <= 1e-8 的箱被丢弃(只对 quantile / kmeans 策略)
  * 常量列被替换成单个「恒 0」箱,edges = [-inf, +inf]
"""

import bisect
import math

# --------------------------------------------------------------------------
# 分位数:三种口径。numpy 把虚拟下标写成 idx = n*q - 1(见 _QuantileMethods)
# --------------------------------------------------------------------------

def _neighbors(idx, n):
    """返回 (prev, next, gamma_raw)。越界按 numpy `_get_indexes` 处理。"""
    if idx < 0:
        return 0, 0, 0.0
    if idx >= n - 1:
        return n - 1, n - 1, 0.0
    prev = math.floor(idx)
    return prev, prev + 1, idx - prev


def percentile_linear(x, q):
    """numpy 默认口径(H&F type 7):idx = (n-1)*q。"""
    xs = sorted(x)
    n = len(xs)
    prev, nxt, g = _neighbors((n - 1) * q, n)
    return (1.0 - g) * xs[prev] + g * xs[nxt]


def percentile_inverted_cdf(x, q):
    """H&F type 1:idx = n*q - 1;gamma == 0 取 prev,否则取 next;负数夹到 0。"""
    xs = sorted(x)
    n = len(xs)
    idx = n * q - 1.0
    prev = math.floor(idx)
    g = idx - prev
    res = prev if g == 0 else prev + 1
    return xs[max(res, 0)]


def percentile_avg_inverted_cdf(x, q):
    """H&F type 2:idx = n*q - 1;gamma 被强制成 0.5(gamma==0)或 1.0。"""
    xs = sorted(x)
    n = len(xs)
    idx = n * q - 1.0
    prev, nxt, g = _neighbors(idx, n)
    g = 0.5 if g == 0 else 1.0
    return (1.0 - g) * xs[prev] + g * xs[nxt]


PERCENTILE_METHODS = {
    "linear": percentile_linear,
    "inverted_cdf": percentile_inverted_cdf,
    "averaged_inverted_cdf": percentile_avg_inverted_cdf,
}


def percentiles(x, levels, method="linear"):
    fn = PERCENTILE_METHODS[method]
    return [fn(x, lv / 100.0) for lv in levels]


# --------------------------------------------------------------------------
# 三种 strategy 的箱边界
# --------------------------------------------------------------------------

def linspace(a, b, num):
    if num == 1:
        return [float(a)]
    step = (b - a) / (num - 1)
    return [a + step * i for i in range(num)]


def bin_edges_uniform(x, n_bins):
    return linspace(min(x), max(x), n_bins + 1)


def bin_edges_quantile(x, n_bins, method="averaged_inverted_cdf"):
    levels = linspace(0.0, 100.0, n_bins + 1)
    return percentiles(x, levels, method)


def bin_edges_kmeans(x, n_bins, tol=1e-4, max_iter=300):
    """1D k-means:确定性初始化(均匀箱中点),Lloyd 迭代,收敛判据同 sklearn。"""
    lo, hi = min(x), max(x)
    edges = linspace(lo, hi, n_bins + 1)
    centers = [(edges[i] + edges[i + 1]) * 0.5 for i in range(n_bins)]
    for _ in range(max_iter):
        assign = [[0.0, 0] for _ in centers]
        for v in x:
            k = min(range(len(centers)), key=lambda i: (v - centers[i]) ** 2)
            assign[k][0] += v
            assign[k][1] += 1
        new = [c for c in centers]
        for i, (total, cnt) in enumerate(assign):
            if cnt:
                new[i] = total / cnt
        shift = sum((a - b) ** 2 for a, b in zip(new, centers))
        centers = sorted(new)
        if shift <= tol:
            break
    mids = [(centers[i] + centers[i + 1]) * 0.5 for i in range(len(centers) - 1)]
    return [lo] + mids + [hi]


def drop_narrow_edges(edges, threshold=1e-8):
    """丢弃宽度 <= threshold 的箱;首元素永远保留(to_begin=inf 的效果)。"""
    kept = [edges[0]]
    for i in range(1, len(edges)):
        if edges[i] - edges[i - 1] > threshold:
            kept.append(edges[i])
    return kept, len(kept) != len(edges)


# --------------------------------------------------------------------------

class KBinsDiscretizer:
    """逐列独立分箱。X 为 list[list[float]]。"""

    def __init__(self, n_bins=5, encode="onehot", strategy="quantile",
                 quantile_method="averaged_inverted_cdf"):
        if encode not in ("onehot", "onehot-dense", "ordinal"):
            raise ValueError("encode must be onehot/onehot-dense/ordinal")
        if strategy not in ("uniform", "quantile", "kmeans"):
            raise ValueError("strategy must be uniform/quantile/kmeans")
        self.n_bins = n_bins
        self.encode = encode
        self.strategy = strategy
        self.quantile_method = quantile_method
        self.bin_edges_ = None
        self.n_bins_ = None
        self.removed_narrow = False

    def fit(self, X):
        n_features = len(X[0])
        self.bin_edges_ = []
        self.n_bins_ = []
        self.removed_narrow = False
        for j in range(n_features):
            col = [row[j] for row in X]
            lo, hi = min(col), max(col)
            if lo == hi:                       # 常量列:单箱、边界 ±inf
                self.bin_edges_.append([-math.inf, math.inf])
                self.n_bins_.append(1)
                continue
            if self.strategy == "uniform":
                edges = bin_edges_uniform(col, self.n_bins)
            elif self.strategy == "quantile":
                edges = bin_edges_quantile(col, self.n_bins, self.quantile_method)
            else:
                edges = bin_edges_kmeans(col, self.n_bins)
            if self.strategy in ("quantile", "kmeans"):
                edges, dropped = drop_narrow_edges(edges)
                if dropped:
                    self.removed_narrow = True
            self.bin_edges_.append(edges)
            self.n_bins_.append(len(edges) - 1)
        return self

    def transform(self, X):
        out = []
        for row in X:
            codes = []
            for j, v in enumerate(row):
                edges = self.bin_edges_[j]
                codes.append(bisect.bisect_right(edges[1:-1], v))
            out.append(codes)
        if self.encode == "ordinal":
            return [[float(c) for c in row] for row in out]
        dense = []
        for row in out:
            enc = []
            for j, c in enumerate(row):
                one = [0.0] * self.n_bins_[j]
                one[c] = 1.0
                enc.extend(one)
            dense.append(enc)
        return dense

    def inverse_transform(self, Xt):
        if self.encode.startswith("onehot"):
            rows = []
            for row in Xt:
                pos, acc = [], 0
                for j in range(len(self.n_bins_)):
                    block = row[acc:acc + self.n_bins_[j]]
                    pos.append(block.index(max(block)))
                    acc += self.n_bins_[j]
                rows.append(pos)
        else:
            rows = [[int(c) for c in row] for row in Xt]
        out = []
        for row in rows:
            vals = []
            for j, c in enumerate(row):
                edges = self.bin_edges_[j]
                centers = [(edges[k + 1] + edges[k]) * 0.5 for k in range(len(edges) - 1)]
                vals.append(centers[c])
            out.append(vals)
        return out
