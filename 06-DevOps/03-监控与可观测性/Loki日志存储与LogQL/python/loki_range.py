"""范围聚合求值：把日志范围向量折叠成指标样本，再做外层聚合。

与 Prometheus 一致的两点，必须写死而不是"大概"：

* `stddev` / `stdvar` 用**总体**（除以 N）而非样本（除以 N-1）口径。
* `quantile_over_time` 用 Prometheus 的线性插值：rank = φ·(N-1)，
  floor 与 ceil 相同则直接取该元素，否则在相邻两点间线性插值。
  所以 `quantile_over_time(0.5, [10,20,30,40])` 是 25 而**不是** 20 或 30。

另有一条硬约束：**指标查询不允许携带错误**。官方原文「Metric queries cannot
contain errors, in case errors are found during execution, Loki will return an
error and appropriate status code.」因此管线跑完只要还剩 `__error__`，这里就
直接抛错，而不是返回 0 —— 这正是官方要求 `unwrap` 后紧跟 `| __error__ = ""`
的原因。
"""

from __future__ import annotations

import math

from logql_ast import LogQLError, MetricQuery, Unwrap
from logql_eval import ERROR_LABEL, UNWRAP_LABEL, Entry, run_pipeline
from logql_parse import LOG_RANGE_FUNCS, UNWRAP_RANGE_FUNCS

__all__ = [
    "series_key",
    "label_subset",
    "prom_quantile",
    "population_stdvar",
    "evaluate",
    "aggregate",
]

_SPECIAL_LABELS = frozenset({ERROR_LABEL, UNWRAP_LABEL})


def series_key(labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """序列标识：标签名排序后的 (名,值) 元组。

    `__error__` / `__unwrap__` 不参与标识 —— 前者是管线诊断量，后者是数值
    样本的搬运通道，都不属于流的身份。
    """
    return tuple(sorted((k, v) for k, v in labels.items() if k not in _SPECIAL_LABELS))


def label_subset(key: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return dict(key)


def prom_quantile(phi: float, sorted_values: list[float]) -> float:
    """Prometheus 口径的分位数（与 PromQL quantile() 同算法）。"""
    if not sorted_values:
        return float("nan")
    if not 0.0 <= phi <= 1.0:
        raise LogQLError("φ 必须落在 [0,1]")
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    rank = phi * (n - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[int(rank)]
    frac = rank - lower
    return sorted_values[lower] * (1.0 - frac) + sorted_values[upper] * frac


def population_stdvar(values: list[float]) -> float:
    """总体方差（除以 N）。Prometheus 的 stdvar 就是总体口径。"""
    if not values:
        return float("nan")
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _log_range_value(func: str, entries: list[Entry], window_s: float) -> float:
    if func == "count_over_time":
        return float(len(entries))
    if func == "bytes_over_time":
        return float(sum(len(e.line.encode("utf-8")) for e in entries))
    if func == "rate":
        return len(entries) / window_s
    if func == "bytes_rate":
        return sum(len(e.line.encode("utf-8")) for e in entries) / window_s
    raise LogQLError(f"未知的日志范围函数: {func}")


def _unwrap_range_value(mq: MetricQuery, entries: list[Entry]) -> float:
    values = [e.value for e in entries if e.value is not None]
    if not values:
        return float("nan")
    func = mq.func
    if func == "sum_over_time":
        return float(sum(values))
    if func == "avg_over_time":
        return sum(values) / len(values)
    if func == "max_over_time":
        return float(max(values))
    if func == "min_over_time":
        return float(min(values))
    if func == "first_over_time":
        return float(values[0])
    if func == "last_over_time":
        return float(values[-1])
    if func == "stdvar_over_time":
        return population_stdvar(values)
    if func == "stddev_over_time":
        return math.sqrt(population_stdvar(values))
    if func == "quantile_over_time":
        assert mq.quantile is not None
        return prom_quantile(mq.quantile, sorted(values))
    raise LogQLError(f"未知的 unwrap 范围函数: {func}")


def evaluate(
    mq: MetricQuery,
    entries: list[Entry],
    now_ns: int | None = None,
    streams: list[dict[str, str]] | None = None,
) -> dict[tuple[tuple[str, str], ...], float]:
    """执行一条指标查询，返回 {序列标识: 值}。

    执行顺序严格是「选择器 → 管线 → 窗口 → 函数 → 聚合」：

    1. 用**原始流标签**过选择器（此时还没有解析出来的标签）
    2. 跑管线，被过滤的直接丢
    3. 管线跑完仍有 `__error__` 残留 → 抛错（指标查询不允许携带错误）
    4. 按窗口裁时间范围
    5. 按序列标识分组求值
    6. 应用 by/without 外层聚合
    """
    has_unwrap = any(isinstance(s, Unwrap) for s in mq.query.stages)
    if has_unwrap and mq.func not in UNWRAP_RANGE_FUNCS:
        raise LogQLError(
            f"{mq.func} 不接受 unwrap 之后的数值范围向量；"
            f"unwrap 只支持 {sorted(UNWRAP_RANGE_FUNCS)}"
        )
    if not has_unwrap and mq.func not in LOG_RANGE_FUNCS:
        raise LogQLError(f"{mq.func} 需要 unwrap 阶段提供数值")

    start_ns = None if now_ns is None else now_ns - int(mq.window_s * 1e9)

    groups: dict[tuple[tuple[str, str], ...], list[Entry]] = {}
    for raw in entries:
        if not mq.query.selector.matches(raw.labels):
            continue
        entry = run_pipeline(raw.copy(), mq.query.stages)
        if entry is None:
            continue
        if ERROR_LABEL in entry.labels:
            raise LogQLError(
                f"metric query contains errors: {entry.labels[ERROR_LABEL]}"
                "（在范围聚合之前追加 | __error__ = \"\" 过滤掉错误行）"
            )
        if start_ns is not None and not (start_ns < entry.ts_ns <= now_ns):
            continue
        groups.setdefault(series_key(entry.labels), []).append(entry)

    result: dict[tuple[tuple[str, str], ...], float] = {}
    if mq.func == "absent_over_time":
        # absent_over_time 是「补空」用的：流存在但窗口内无数据时返回 1。
        # candidates 必须**先过选择器** —— 否则会把与本次选择器无关的流也算进来，
        # 得出「{app="idle"} 的缺失情况」时顺带报告 {app="api"}。
        candidates = {series_key(s) for s in (streams or []) if mq.query.selector.matches(s)}
        candidates |= {series_key(e.labels) for e in entries if mq.query.selector.matches(e.labels)}
        candidates |= set(groups)
        for key in candidates:
            result[key] = 0.0 if groups.get(key) else 1.0
    else:
        for key, bucket in groups.items():
            bucket.sort(key=lambda e: e.ts_ns)
            if mq.func in UNWRAP_RANGE_FUNCS:
                result[key] = _unwrap_range_value(mq, bucket)
            else:
                result[key] = _log_range_value(mq.func, bucket, mq.window_s)

    if mq.outer:
        result = aggregate(result, mq.outer, mq.grouping, mq.by)
    return result


def aggregate(
    samples: dict[tuple[tuple[str, str], ...], float],
    op: str,
    grouping: str | None,
    by: tuple[str, ...],
) -> dict[tuple[tuple[str, str], ...], float]:
    """外层聚合。

    grouping 三档语义与 Prometheus / Loki 一致：
      None      —— 全部序列合成一个组（`sum(rate(...))`）
      "by"      —— 只按列出的标签分组
      "without" —— 按「除列出的标签之外」的所有标签分组
    """
    buckets: dict[tuple[tuple[str, str], ...], list[float]] = {}
    for key, value in samples.items():
        labels = label_subset(key)
        if grouping == "by":
            gk = tuple(sorted((name, labels.get(name, "")) for name in by))
        elif grouping == "without":
            excluded = set(by)
            gk = tuple((k, v) for k, v in key if k not in excluded)
        else:
            gk = ()
        buckets.setdefault(gk, []).append(value)

    out: dict[tuple[tuple[str, str], ...], float] = {}
    for gk, values in buckets.items():
        if op == "sum":
            out[gk] = float(sum(values))
        elif op == "avg":
            out[gk] = sum(values) / len(values)
        elif op == "max":
            out[gk] = float(max(values))
        elif op == "min":
            out[gk] = float(min(values))
        elif op == "count":
            out[gk] = float(len(values))
        elif op == "stdvar":
            out[gk] = population_stdvar(values)
        elif op == "stddev":
            out[gk] = math.sqrt(population_stdvar(values))
        else:
            raise LogQLError(f"未实现的外层聚合算子: {op}")
    return out
