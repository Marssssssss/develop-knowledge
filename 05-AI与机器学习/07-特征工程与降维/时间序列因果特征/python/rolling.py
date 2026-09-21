"""滚动/扩展窗口(纯标准库),对齐 pandas 3.0 的 rolling / expanding 语义。

规则(由 pandas 3.0.6 实测 + 官方文档反推,离线对拍见 README):
  * 基准窗口是 `closed='right'`,即覆盖区间 `(first, last]`;对整数窗口而言
    标签 i 对应的区间是 [i - window + 1, i]。
  * `center=True` 只把窗口的**末端**右移 `(window - 1) // 2`,再套用 closed 规则。
    偶数窗口因此是「偏左」的:w=4 时标签 i 覆盖 [i-2, i+1]。
  * `closed` 只调整 1 格:'left' → [start-1, end-1];'both' → [start-1, end];
    'neither' → [start, end-1]。
  * `min_periods` 为 None 时:整数窗口取 window;offset 窗口取 1。
    窗口内有效点数(裁剪到 [0, n-1] 后)不足 min_periods 就出 NaN。
  * `expanding(min_periods=k)` 覆盖 [0, i],min_periods 默认 1,故**含当期**。
  * `std` 默认 `ddof=1`(样本标准差),这是最容易写错的一处。
"""

import math


def _window_bounds(i, window, center, closed):
    """返回标签 i 对应的左闭右闭原始下标区间 [lo, hi]。"""
    end = i + (window - 1) // 2 if center else i
    start = end - window + 1
    if closed is None or closed == "right":
        lo, hi = start, end
    elif closed == "left":
        lo, hi = start - 1, end - 1
    elif closed == "both":
        lo, hi = start - 1, end
    elif closed == "neither":
        lo, hi = start, end - 1
    else:
        raise ValueError("closed 只能是 None/right/left/both/neither")
    return lo, hi


def rolling_apply(x, window, fn, min_periods=None, center=False, closed=None):
    """通用滚动窗口。返回与 x 等长的 list,不足位置为 None。"""
    if window < 1:
        raise ValueError("window 必须 >= 1")
    n = len(x)
    mp = window if min_periods is None else min_periods
    if mp > window:
        raise ValueError("min_periods %d must be <= window %d" % (mp, window))
    out = [None] * n
    for i in range(n):
        lo, hi = _window_bounds(i, window, center, closed)
        a, b = max(lo, 0), min(hi, n - 1)
        cnt = 0 if b < a else b - a + 1
        if cnt < mp:
            continue
        try:
            # min_periods=0 时 pandas 仍会对空窗口求值:sum 得 0.0,mean/min/max 得 NaN
            out[i] = fn(x[a:b + 1])
        except (ValueError, ZeroDivisionError, TypeError):
            out[i] = None
    return out


def rolling_sum(x, window, min_periods=None, center=False, closed=None):
    return rolling_apply(x, window, sum, min_periods, center, closed)


def rolling_mean(x, window, min_periods=None, center=False, closed=None):
    return rolling_apply(x, window, lambda w: sum(w) / len(w), min_periods, center, closed)


def rolling_std(x, window, ddof=1, min_periods=None, center=False, closed=None):
    """pandas Rolling.std 默认 ddof=1。"""
    def f(w):
        m = sum(w) / len(w)
        return math.sqrt(sum((v - m) ** 2 for v in w) / (len(w) - ddof))
    return rolling_apply(x, window, f, min_periods, center, closed)


def rolling_min(x, window, min_periods=None, center=False, closed=None):
    return rolling_apply(x, window, min, min_periods, center, closed)


def rolling_max(x, window, min_periods=None, center=False, closed=None):
    return rolling_apply(x, window, max, min_periods, center, closed)


def rolling_count(x, window, center=False, closed=None):
    """有效点数(不做 min_periods 过滤),对应 pandas Rolling.count。"""
    return rolling_apply(x, window, len, min_periods=1, center=center, closed=closed)


def expanding_apply(x, fn, min_periods=1):
    """覆盖 [0, i],默认 min_periods=1,即**含当期**。"""
    out = [None] * len(x)
    for i in range(len(x)):
        if i + 1 < min_periods:
            continue
        out[i] = fn(x[:i + 1])
    return out


def expanding_sum(x, min_periods=1):
    return expanding_apply(x, sum, min_periods)


def expanding_mean(x, min_periods=1):
    return expanding_apply(x, lambda w: sum(w) / len(w), min_periods)


def shift(x, periods=1, fill=None):
    """pandas shift:正数向后挪(新值来自过去),负数向前挪(会引入未来)。"""
    n = len(x)
    out = [fill] * n
    for i in range(n):
        j = i - periods
        if 0 <= j < n:
            out[i] = x[j]
    return out


def diff(x, periods=1):
    out = [None] * len(x)
    s = shift(x, periods)
    for i in range(len(x)):
        if s[i] is not None:
            out[i] = x[i] - s[i]
    return out


def pct_change(x, periods=1):
    out = [None] * len(x)
    s = shift(x, periods)
    for i in range(len(x)):
        if s[i] not in (None, 0):
            out[i] = (x[i] - s[i]) / s[i]
    return out
