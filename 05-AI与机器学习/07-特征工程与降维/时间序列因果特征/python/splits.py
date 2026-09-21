"""TimeSeriesSplit(纯标准库),逐字对齐 sklearn 1.9.1 的 model_selection._split.TimeSeriesSplit。

源码实读要点:
  * 先把 **全部样本** 切成 `n_splits + 1` 段,`test_size` 默认 = `n_samples // (n_splits + 1)`;
    第 k 折(0-based)用第 k+1 段做测试,前面所有段做训练 —— 故训练集是**超集递增**的。
  * `gap` 是「训练集末尾与测试集开头之间被剔除的样本数」,即 `train_end = test_start - gap`。
  * `max_train_size` 只在 `max_train_size < train_end` 时才把训练集截成滑动窗口。
  * 两条硬校验:`n_folds > n_samples` 报错;`n_samples - gap - test_size * n_splits <= 0` 报错。
"""


class TimeSeriesSplit:
    def __init__(self, n_splits=5, *, max_train_size=None, test_size=None, gap=0):
        self.n_splits = n_splits
        self.max_train_size = max_train_size
        self.test_size = test_size
        self.gap = gap

    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits

    def split(self, X, y=None, groups=None):
        n_samples = len(X)
        n_folds = self.n_splits + 1
        gap = self.gap
        test_size = self.test_size if self.test_size is not None else n_samples // n_folds

        if n_folds > n_samples:
            raise ValueError(
                "Cannot have number of folds=%d greater than the number of "
                "samples=%d." % (n_folds, n_samples))
        if n_samples - gap - (test_size * self.n_splits) <= 0:
            raise ValueError(
                "Too many splits=%d for number of samples=%d with test_size=%d "
                "and gap=%d." % (self.n_splits, n_samples, test_size, gap))

        test_starts = range(n_samples - self.n_splits * test_size, n_samples, test_size)
        for test_start in test_starts:
            train_end = test_start - gap
            if self.max_train_size and self.max_train_size < train_end:
                yield (list(range(train_end - self.max_train_size, train_end)),
                       list(range(test_start, test_start + test_size)))
            else:
                yield (list(range(train_end)),
                       list(range(test_start, test_start + test_size)))


def rolling_origin_splits(n_samples, n_splits=5, horizon=1, min_train=1):
    """部署视角的切分:每个测试点是「用 t 之前的信息预测 t+horizon」。

    与 TimeSeriesSplit 的区别:这里的 horizon 是**预测跨度**,不是训练/测试之间的缝隙。
    它决定「特征在 t 时刻能看到什么」,而 gap 只决定「训练集能用到哪一天」。
    """
    out = []
    last_start = n_samples - n_splits * horizon
    for k in range(n_splits):
        test_start = last_start + k * horizon
        train_end = test_start
        if train_end < min_train:
            continue
        out.append((list(range(train_end)), list(range(test_start, test_start + horizon))))
    return out
