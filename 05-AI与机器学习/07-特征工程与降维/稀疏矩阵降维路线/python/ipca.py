"""IncrementalPCA 与 Youngs-Cramer 增量均值/方差更新(纯标准库)。

对齐对象:sklearn 1.9.1 的
  - sklearn.utils.extmath._incremental_mean_and_var
  - sklearn.decomposition.IncrementalPCA(partial_fit / fit)
两者源码均已逐行读过(见 README 参考资料)。

要点(全部来自源码而非推测):
  * `fit` 的分块大小在 batch_size=None 时是 **5 * n_features**(1.9.1 的值,不是旧版的 10);
    gen_batches 带 min_batch_size=n_components,尾部不够就并进最后一块而不单发。
  * 方差更新是 Chan/Golub/LeVeque 的**校正两趟法**:
      T = new_sum / new_count;  temp = X - T;
      new_unnorm = Σtemp² - (Σtemp)² / new_count;
      updated_unnorm = last_unnorm + new_unnorm
                       + (last_count/new_count) / updated_count * (last_sum/(last_count/new_count) - new_sum)²
    最后一项在 last_count == 0 时是 0/0 = NaN,官方用 `zeros` 掩码直接覆盖成 new_unnorm。
  * partial_fit 的减均值分两种情形:首块减**本块均值** col_mean;后续块减**本块均值** col_batch_mean,
    再把「上一轮奇异值×分量」「去均值后的新块」「mean_correction」三块拼起来做 SVD,
    其中 mean_correction = sqrt((seen/total) * n_batch) * (last_mean - col_batch_mean)。
  * explained_variance_ = S² / (n_total - 1);explained_variance_ratio_ = S² / Σ(col_var * n_total)。
  * noise_variance_ 只在 `n_components_ not in (n_samples, n_features)` 时取尾部均值,否则 0.0。
  * transform 是 (X - mean_) @ components_.T —— 用的是**跨块累积**的 mean_,不是本块均值。
"""

import math

from linalg import svd_jacobi, svd_flip


def gen_batches(n, batch_size, min_batch_size=0):
    """sklearn.utils.gen_batches 的逐字复刻:返回 (start, end) 左闭右开区间。

    尾部若不足 min_batch_size,该切片被跳过但 start 不推进,于是余量并进最后一块。
    """
    start = 0
    for _ in range(int(n // batch_size)):
        end = start + batch_size
        if end + min_batch_size > n:
            continue
        yield (start, end)
        start = end
    if start < n:
        yield (start, n)


def _as_vec(v, n):
    """last_mean / last_variance 首轮是标量 0.0,之后是 list,统一成 list。"""
    if isinstance(v, (int, float)):
        return [float(v)] * n
    return list(v)


def incremental_mean_and_var(X, last_mean, last_variance, last_sample_count):
    """增量均值 + 校正两趟方差。返回 (mean, var, count),三者都是逐列 list。

    last_variance 传 None 则不更新方差(返回 None)。
    """
    n_samples, n_features = len(X), len(X[0])
    last_sum = [last_mean[j] * last_sample_count[j] for j in range(n_features)]
    new_sum = [sum(X[i][j] for i in range(n_samples)) for j in range(n_features)]
    new_sample_count = [n_samples] * n_features          # 本模型不处理 NaN
    updated_sample_count = [last_sample_count[j] + new_sample_count[j]
                            for j in range(n_features)]
    updated_mean = [(last_sum[j] + new_sum[j]) / updated_sample_count[j]
                    for j in range(n_features)]
    if last_variance is None:
        return updated_mean, None, updated_sample_count
    updated_variance = []
    for j in range(n_features):
        T = new_sum[j] / new_sample_count[j]
        temp = [X[i][j] - T for i in range(n_samples)]
        correction = sum(temp)
        new_unnorm = sum(t * t for t in temp) - correction * correction / new_sample_count[j]
        if last_sample_count[j] == 0:
            updated_unnorm = new_unnorm           # 官方用 zeros 掩码覆盖 0/0 产生的 NaN
        else:
            last_over_new = last_sample_count[j] / new_sample_count[j]
            updated_unnorm = (last_variance[j] * last_sample_count[j] + new_unnorm
                              + last_over_new / updated_sample_count[j]
                              * (last_sum[j] / last_over_new - new_sum[j]) ** 2)
        updated_variance.append(updated_unnorm / updated_sample_count[j])
    return updated_mean, updated_variance, updated_sample_count


class IncrementalPCA:
    """分块 partial_fit 的 PCA:每块只保留 2 * batch_size 行在内存里。"""

    def __init__(self, n_components=None, *, batch_size=None):
        self.n_components = n_components
        self.batch_size = batch_size

    # ---------------------------------------------------------------- 单块
    def partial_fit(self, X, y=None, check_input=True):
        first_pass = not hasattr(self, "components_")
        if first_pass:
            self.components_ = None
            self.n_samples_seen_ = 0
            self.mean_ = 0.0
            self.var_ = 0.0
        n_samples, n_features = len(X), len(X[0])
        if first_pass:
            self.n_features_in_ = n_features
        elif n_features != self.n_features_in_:
            raise ValueError(
                "X has %d features, but IncrementalPCA is expecting %d features "
                "as input." % (n_features, self.n_features_in_))
        if self.n_components is None:
            self.n_components_ = (min(n_samples, n_features) if self.components_ is None
                                  else len(self.components_))
        elif not self.n_components <= n_features:
            raise ValueError(
                "n_components=%r invalid for n_features=%d, need more rows than "
                "columns for IncrementalPCA processing" % (self.n_components, n_features))
        elif self.n_components > n_samples and first_pass:
            raise ValueError(
                "n_components=%d must be less or equal to the batch number of "
                "samples %d for the first partial_fit call." % (self.n_components, n_samples))
        else:
            self.n_components_ = self.n_components
        if self.components_ is not None and len(self.components_) != self.n_components_:
            raise ValueError(
                "Number of input features has changed from %d to %d between calls "
                "to partial_fit! Try setting n_components to a fixed value."
                % (len(self.components_), self.n_components_))
        if not hasattr(self, "n_samples_seen_"):
            self.n_samples_seen_ = 0
            self.mean_ = 0.0
            self.var_ = 0.0

        col_mean, col_var, n_total_vec = incremental_mean_and_var(
            X, _as_vec(self.mean_, n_features), _as_vec(self.var_, n_features),
            [self.n_samples_seen_] * n_features)
        n_total = n_total_vec[0]
        if self.n_samples_seen_ == 0:
            X = [[X[i][j] - col_mean[j] for j in range(n_features)] for i in range(n_samples)]
        else:
            col_batch_mean = [sum(X[i][j] for i in range(n_samples)) / n_samples
                              for j in range(n_features)]
            centered = [[X[i][j] - col_batch_mean[j] for j in range(n_features)]
                        for i in range(n_samples)]
            scale = math.sqrt((self.n_samples_seen_ / n_total) * n_samples)
            mean_correction = [scale * (self.mean_[j] - col_batch_mean[j])
                               for j in range(n_features)]
            stacked = [[self.singular_values_[k] * self.components_[k][j]
                        for j in range(n_features)]
                       for k in range(len(self.singular_values_))]
            X = stacked + centered + [mean_correction]

        U, S, Vt = svd_jacobi(X)
        U, Vt = svd_flip(U, Vt, u_based_decision=False)
        explained_variance = [s * s / (n_total - 1) for s in S]
        denom = sum(col_var[j] * n_total for j in range(n_features))
        explained_variance_ratio = [s * s / denom for s in S]

        self.n_samples_seen_ = n_total
        self.components_ = Vt[:self.n_components_]
        self.singular_values_ = S[:self.n_components_]
        self.mean_ = col_mean
        self.var_ = col_var
        self.explained_variance_ = explained_variance[:self.n_components_]
        self.explained_variance_ratio_ = explained_variance_ratio[:self.n_components_]
        if self.n_components_ not in (n_samples, n_features):
            tail = explained_variance[self.n_components_:]
            self.noise_variance_ = sum(tail) / len(tail) if tail else float("nan")
        else:
            self.noise_variance_ = 0.0
        return self

    # ---------------------------------------------------------------- 全量
    def fit(self, X, y=None):
        n_samples, n_features = len(X), len(X[0])
        self.batch_size_ = 5 * n_features if self.batch_size is None else self.batch_size
        for (a, b) in gen_batches(n_samples, self.batch_size_,
                                  min_batch_size=self.n_components or 0):
            self.partial_fit([row[:] for row in X[a:b]], check_input=False)
        return self

    def transform(self, X):
        """(X - mean_) @ components_.T,用的是跨块累积的 mean_。"""
        mean = _as_vec(self.mean_, len(X[0]))
        k = len(self.components_)
        return [[sum((X[i][j] - mean[j]) * self.components_[c][j]
                     for j in range(len(X[0]))) for c in range(k)]
                for i in range(len(X))]

    def fit_transform(self, X, y=None):
        return self.fit(X).transform(X)
