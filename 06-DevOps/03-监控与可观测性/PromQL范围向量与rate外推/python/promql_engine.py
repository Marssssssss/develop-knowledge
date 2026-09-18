"""rate / increase / delta 的边界外推，以及 PromQL 求值引擎。

外推算法（Prometheus ``extrapolatedRate`` 的口径）：

    sampledInterval           = last.t - first.t
    avgDurationBetweenSamples = sampledInterval / (n - 1)
    durationToStart           = first.t - rangeStart
    durationToEnd             = rangeEnd - last.t
    extrapolationThreshold    = avgDurationBetweenSamples * 1.1
    # 任一端距边界过远（>= 阈值）时，最多只外推「平均样本间隔的一半」
    extrapolateToInterval     = sampledInterval + f(durationToStart) + f(durationToEnd)
    factor                    = extrapolateToInterval / sampledInterval
    rate                      = rawDelta * factor / (rangeEnd - rangeStart)
    increase                  = rawDelta * factor

``rawDelta`` 已补偿计数器重置：遇到 ``v[i] < v[i-1]``（重置）时把重置前的高度
``v[i-1]`` 加回去 —— 等价于假设重置瞬间计数器归零后重新计数。

官方文档只说明「外推**存在**且用于补偿漏抓/未对齐」，**未规定**上述两个分支
（超阈值降级 / 计数器零点截断）的先后；两种顺序在
``avg/2 < durationToZero < threshold`` 时结论不同，见 ``counter_order`` 参数与
demo 的 G 组断言。

解析（选择器 / 范围 / offset / @ / 子查询）见 ``promql_parser``；本模块转出
``parse`` / ``parse_duration`` / ``PromQLError`` 以保持调用方契约不变。
"""

from promql_parser import Expr, PromQLError, parse, parse_duration  # noqa: F401
from promql_store import STALE  # noqa: F401  (转出给调用方)

__all__ = ["Engine", "PromQLError", "apply_func", "extrapolated_rate",
           "instant_value", "parse", "parse_duration", "raw_delta"]


def raw_delta(points, is_counter):
    """范围向量首尾差，并按需补偿计数器重置。"""
    result = points[-1].v - points[0].v
    if is_counter:
        prev = points[0].v
        for p in points[1:]:
            if p.v < prev:
                result += prev
            prev = p.v
    return result


def extrapolated_rate(points, range_start, range_end, is_counter, is_rate,
                      counter_order="threshold_first"):
    """``rate`` / ``increase`` / ``delta`` 共用的外推计算。

    ``counter_order`` 选择「超阈值项替换为 avg/2」与「计数器零点截断」两步的先后：
    前者对应 ``"threshold_first"``，后者对应 ``"zero_first"``。
    """
    if len(points) < 2:
        return None
    sampled_interval = points[-1].t - points[0].t
    if sampled_interval <= 0:
        return None
    result = raw_delta(points, is_counter)

    duration_to_start = points[0].t - range_start
    duration_to_end = range_end - points[-1].t
    avg_between = sampled_interval / (len(points) - 1)
    threshold = avg_between * 1.1
    half = avg_between / 2

    def zero_point(ds):
        # 计数器不可能为负：若序列有上升趋势，可把「零点」当作序列起点，
        # 从而避免把结果外推到负的计数器值。
        if is_counter and result > 0 and points[0].v >= 0:
            dtz = sampled_interval * (points[0].v / result)
            if dtz < ds:
                return dtz
        return ds

    if counter_order == "threshold_first":
        if duration_to_start >= threshold:
            duration_to_start = half
        duration_to_start = zero_point(duration_to_start)
        if duration_to_end >= threshold:
            duration_to_end = half
    else:
        duration_to_start = zero_point(duration_to_start)
        if duration_to_start >= threshold:
            duration_to_start = half
        if duration_to_end >= threshold:
            duration_to_end = half

    extrapolate_to = sampled_interval + duration_to_start + duration_to_end
    factor = extrapolate_to / sampled_interval
    if is_rate:
        factor /= (range_end - range_start)
    return result * factor


def instant_value(points, is_counter, is_rate):
    """``irate`` / ``idelta``：只看最后两个点，不做外推。"""
    if len(points) < 2:
        return None
    last, prev = points[-1], points[-2]
    if is_rate and is_counter and last.v < prev.v:
        result = last.v            # 重置：假设从 0 重新计数
    else:
        result = last.v - prev.v
    span = last.t - prev.t
    if span <= 0:
        return None
    return result / span if is_rate else result


def apply_func(name, points, range_start, range_end):
    if name == "rate":
        return extrapolated_rate(points, range_start, range_end, True, True)
    if name == "increase":
        return extrapolated_rate(points, range_start, range_end, True, False)
    if name == "delta":
        return extrapolated_rate(points, range_start, range_end, False, False)
    if name == "irate":
        return instant_value(points, True, True)
    if name == "idelta":
        return instant_value(points, False, False)
    if name == "resets":
        return sum(1 for a, b in zip(points, points[1:]) if b.v < a.v)
    if name == "changes":
        return sum(1 for a, b in zip(points, points[1:]) if b.v != a.v)
    raise PromQLError("未实现的函数: %r" % name)


class Engine:
    """求值引擎。``eval_interval`` 即全局 evaluation interval（默认 1m）。"""

    def __init__(self, store, eval_interval=60.0):
        self.store = store
        self.eval_interval = eval_interval

    def _resolve_at(self, at, eval_t, query_range=None):
        if at is None:
            return eval_t
        if at == "start()":
            return query_range[0] if query_range else eval_t
        if at == "end()":
            return query_range[1] if query_range else eval_t
        return at

    def instant(self, text, eval_t, query_range=None):
        """即时查询。即时向量返回 [(labels, value)]，矩阵返回 [(labels, points)]。"""
        return self._eval(parse(text), eval_t, query_range)

    def _eval(self, ex, eval_t, query_range=None):
        at = self._resolve_at(ex.at, eval_t, query_range)

        if ex.sub_range is not None:
            return self._eval_subquery(ex, at, query_range)

        select_at = at - ex.offset_s
        if ex.range_s is None:
            return self.store.select(ex.name, ex.matchers, select_at)

        range_end = select_at
        range_start = range_end - ex.range_s
        matrix = self.store.range_select(ex.name, ex.matchers, range_start, range_end)
        if ex.func is None:
            return matrix
        out = []
        for labels, pts in matrix:
            v = apply_func(ex.func, pts, range_start, range_end)
            if v is not None:
                out.append((labels, v))
        return out

    def _eval_subquery(self, ex, at, query_range):
        """子查询：在内层表达式的 ``[at-range, at]`` 上按 resolution 逐步求值。"""
        res = ex.sub_res if ex.sub_res is not None else self.eval_interval
        if res <= 0:
            raise PromQLError("子查询 resolution 必须为正")
        inner = Expr()
        inner.func = ex.func
        inner.name, inner.matchers = ex.name, ex.matchers
        inner.range_s, inner.offset_s, inner.at = ex.range_s, ex.offset_s, ex.at
        steps = []
        t = at - ex.sub_range
        while True:
            for labels, val in self._eval(inner, t, query_range):
                steps.append((labels, t, val))
            if t >= at:
                break
            t = min(t + res, at)
        grouped = {}
        for labels, t, val in steps:
            key = tuple(sorted(labels.items()))
            grouped.setdefault(key, (labels, []))[1].append((t, val))
        return [(lb, pts) for lb, pts in grouped.values()]
