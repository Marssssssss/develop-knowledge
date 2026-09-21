"""Prometheus 区间向量与 rate/increase 的纯 Python 模型。

本模块是 **Prometheus `promql/functions.go` 中 `extrapolatedRate` 的逐行转写**,
不是"照概念重写"。转写对象:`rangeStart/rangeEnd`、`durationToStart/durationToEnd`、
`sampledInterval`、`averageDurationBetweenSamples`、`extrapolationThreshold =
averageDurationBetweenSamples * 1.1`、`factor` 的两段式计算。

语言差异显式落地(Go float64 与 Python float 同精度,故只剩下面几条):
- Go 的 `enh.Out` 为空向量表示"该序列被丢弃",Python 返回 `None`。
- Go 里「单样本且没有合适的 start timestamp」直接 return,Python 同样返回 `None`。
- Go 的 `samples.Floats[0].F` 在 `resultFloat <= 0` 时不参与零点钳制,这里照抄分支。

时间戳一律用**秒**(float);源码里是毫秒故除以 1000,转写时统一到秒。
"""

# ---------------------------------------------------------------- 区间选择


def select_range(series, ts, range_s):
    """区间向量选择器:`m[range_s]` 在求值时刻 ts。

    Prometheus 文档原文: "The range is a left-open and right-closed interval,
    i.e. samples with timestamps coinciding with the left boundary of the range
    are excluded from the selection, while samples coinciding with the right
    boundary of the range are included."

    即 (ts - range_s, ts]。注意左端**开**:正好落在左边界上的样本被排除。
    """
    range_start = ts - range_s
    return [(t, v) for (t, v) in series if range_start < t <= ts]


# ---------------------------------------------------------------- 外推


def extrapolated_rate(points, ts, range_s, is_counter=True, is_rate=True):
    """照 `extrapolatedRate` 转写。points 是已经过 `select_range` 的 [(t, v)]。"""
    if not points:
        return None

    range_start = ts - range_s
    range_end = ts
    first_t, first_v = points[0]
    last_t, last_v = points[-1]
    num_samples_minus_one = len(points) - 1

    result = last_v - first_v
    if is_counter:
        # 计数器回绕:后一个点比前一个小,就把前一个点的整个值加回来。
        for i in range(1, len(points)):
            if points[i][1] < points[i - 1][1]:
                result += points[i - 1][1]

    duration_to_start = first_t - range_start
    duration_to_end = range_end - last_t
    sampled_interval = last_t - first_t

    average_duration = (sampled_interval / num_samples_minus_one
                        if num_samples_minus_one > 0 else 0.0)
    extrapolation_threshold = average_duration * 1.1

    if num_samples_minus_one == 0:
        # 源码:单样本且没有可用 start timestamp 时"return nothing"。
        return None

    if duration_to_start >= extrapolation_threshold:
        duration_to_start = average_duration / 2
        if is_counter:
            # 计数器不能为负:把外推起点退回到"计数器值为 0 的时刻"。
            duration_to_zero = duration_to_start
            if result > 0 and first_v >= 0:
                duration_to_zero = sampled_interval * (first_v / result)
            if duration_to_zero < duration_to_start:
                duration_to_start = duration_to_zero

    if duration_to_end >= extrapolation_threshold:
        duration_to_end = average_duration / 2

    factor = 1.0
    if sampled_interval != 0:
        factor = (sampled_interval + duration_to_start + duration_to_end) / sampled_interval
    if is_rate:
        factor /= range_s
    return result * factor


def rate(points, ts, range_s, is_counter=True):
    """`rate(m[range_s])`:每秒平均增长速率。"""
    return extrapolated_rate(points, ts, range_s, is_counter, is_rate=True)


def increase(points, ts, range_s, is_counter=True):
    """`increase(m[range_s])`:文档说是 rate 乘以窗口秒数的语法糖。"""
    return extrapolated_rate(points, ts, range_s, is_counter, is_rate=False)


def irate(points):
    """`irate(m[range_s])`:只看最后两个点,无外推。"""
    if len(points) < 2:
        return None
    (t0, v0), (t1, v1) = points[-2], points[-1]
    if t1 == t0:
        return None
    d = v1 - v0
    if d < 0:  # 计数器回绕
        d = v1
    return d / (t1 - t0)
