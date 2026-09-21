"""OpenSLO v1 的三种 `budgetingMethod`。

规范原文(实读):

- Occurrences method uses a ratio of counts of good events to the total count of
  the events.
- Timeslices method uses a ratio of good time slices to total time slices in a
  budgeting period.
- RatioTimeslices method uses an average of all time slices' success ratios in a
  budgeting period.

三者的差别**不是精度而是加权方式**:Occurrences 按事件数加权(大流量切片权重高),
Timeslices 先按 `timeSliceTarget` 把每个切片二值化再等权计数,RatioTimeslices 则把
每个切片的比值等权平均。流量不均时三者可以差到 0.5 与 0.999 这种量级。
"""

NO_DATA = object()  # 切片内 0 个事件:比值无定义,不能当 0 也不能当 1


def occurrences(good_counts, total_counts):
    """Occurrences:sum(good) / sum(total)。按事件数加权。"""
    g = sum(good_counts)
    t = sum(total_counts)
    if t == 0:
        return None
    return g / t


def slice_ratios(good_counts, total_counts):
    """每个切片的成功率;0 事件的切片返回 NO_DATA。"""
    out = []
    for g, t in zip(good_counts, total_counts):
        out.append(NO_DATA if t == 0 else g / t)
    return out


def timeslices(good_counts, total_counts, time_slice_target):
    """Timeslices:先把每个切片判成 good/bad,再 good_slices / total_slices。

    判据是"该切片成功率是否达到 timeSliceTarget"。规范给的 `timeSliceTarget`
    取值域是 (0.0, 1.0]。
    """
    if not 0.0 < time_slice_target <= 1.0:
        raise ValueError("timeSliceTarget must be in (0.0, 1.0]")
    ratios = slice_ratios(good_counts, total_counts)
    usable = [r for r in ratios if r is not NO_DATA]
    if not usable:
        return None
    good = sum(1 for r in usable if r >= time_slice_target)
    return good / len(usable)


def ratio_timeslices(good_counts, total_counts):
    """RatioTimeslices:所有切片成功率的**等权平均**。"""
    usable = [r for r in slice_ratios(good_counts, total_counts) if r is not NO_DATA]
    if not usable:
        return None
    return sum(usable) / len(usable)


def all_methods(good_counts, total_counts, time_slice_target):
    """一次算出三种口径,便于直接对比。"""
    return {
        "occurrences": occurrences(good_counts, total_counts),
        "timeslices": timeslices(good_counts, total_counts, time_slice_target),
        "ratio_timeslices": ratio_timeslices(good_counts, total_counts),
    }
