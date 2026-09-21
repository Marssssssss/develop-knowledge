#!/usr/bin/env python3
"""参数化基准（hyperfine ``--parameter-scan`` / JMH ``@Param``）的结果怎么做**公平的多维比较**。

口径全部来自逐行实读的源码：

``sharkdp/hyperfine`` ``src/parameter/range_step.rs``
  1. ``RangeStep::new`` 的三条拒绝：``end < start`` → ``EmptyRange``、``step == 0`` → ``ZeroStep``、
     规模超过 ``MAX_PARAMETERS = 100_000`` → ``TooLarge``；
  2. 迭代是 ``state > end`` 才停，所以区间是**闭**的、步长累加、**最后一个值 ≤ end**；
  3. ``range_step_size_hint`` 用的是 ``(end - start + 1) / step`` —— **加的是绝对值 1，不是一个 step**。
     于是 step > 1 时**低估**（0..10 step 3 报 3 而实际 4 个值），step < 1 时**高估**
     （0..1 step 0.1 报 20 而实际 11 个值），只有 step == 1 时精确。

``sharkdp/hyperfine`` ``src/benchmark/relative_speed.rs`` 与 ``benchmark_result.rs``
  4. 参考点是 ``fastest_of``（**按均值最小的那条命令**），比值恒 ≥ 1：
     ``Less ⇒ reference.mean / result.mean``、``Greater ⇒ result.mean / reference.mean``、``Equal ⇒ 1.0``；
  5. ``mean == 0`` 的分支：非参考且自己 mean 为 0 → ``f64::INFINITY``；参考自己 mean 为 0 → ``1.0``；
  6. 相对速度的**标准差按误差传播**算（注释给了维基链接，并明确"Covariance assumed to be 0"）：
     ``ratio * sqrt((sd_result/mean_result)² + (sd_ref/mean_ref)²)``；任一侧 stddev 缺失 → ``None``；
  7. ``compare_mean_time`` 用 ``partial_cmp(...).unwrap_or(Ordering::Equal)`` —— 出现 NaN 时按相等处理；
  8. ``BenchmarkResult.parameters`` 是 ``BTreeMap<String, String>``，即**按参数名的字典序**排，
     导出 CSV 时列顺序由它决定。

``openjdk/jmh`` ``annotations/Param.java``
  9. 多参数时 JMH 走**外积**："When multiple @Param-s are needed for the benchmark run, JMH will
     compute the outer product of all the parameters in the run."；
  10. 哨兵 ``BLANK_ARGS = "blank_blank_blank_2014"``；``@Param`` 只能放在 `@State` 类的**非 final** 字段上，
      且在**任何 @Setup 之前**注入；
  11. 类型限于基本类型 / 包装类型 / String / Enum，其中 **Enum 默认取全部枚举常量**（唯一的默认例外）。

无第三方依赖；自检完全确定性。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import product
from typing import Dict, List, Optional, Sequence

MAX_PARAMETERS = 100_000
BLANK_ARGS = "blank_blank_blank_2014"


class ParameterScanError(Exception):
    pass


# --------------------------------------------------------------------------
# hyperfine 的参数范围
# --------------------------------------------------------------------------
def range_step_size_hint(start, end, step) -> int:
    """复刻官方公式：(end - start + 1) / step —— 加的是绝对值 1，不是一个 step。"""
    if step == 0:
        return None                       # (usize::MAX, None)
    return int((end - start + 1) // step) if isinstance(step, Decimal) else (end - start + 1) // step


class RangeStep:
    def __init__(self, start, end, step) -> None:
        if end < start:
            raise ParameterScanError("Empty parameter range")
        if step == 0:
            raise ParameterScanError("Zero is not a valid parameter step")
        hint = range_step_size_hint(start, end, step)
        if hint is not None and hint > MAX_PARAMETERS:
            raise ParameterScanError("Parameter range is too large")
        self.start, self.end, self.step = start, end, step

    def __iter__(self):
        state = self.start
        while state <= self.end:
            yield state
            state += self.step

    def values(self) -> List:
        return list(iter(self))

    def size_hint(self) -> Optional[int]:
        return range_step_size_hint(self.start, self.end, self.step)


# --------------------------------------------------------------------------
# hyperfine 的结果与相对速度
# --------------------------------------------------------------------------
@dataclass
class BenchmarkResult:
    command: str
    mean: float
    stddev: Optional[float] = None
    parameters: Dict[str, str] = field(default_factory=dict)

    @property
    def sorted_parameters(self) -> List[str]:
        """BTreeMap ⇒ 按 key 的字典序。"""
        return [self.parameters[k] for k in sorted(self.parameters)]


def _fdiv(a: float, b: float) -> float:
    """模拟 Rust f64 的除法语义：Python 会抛 ZeroDivisionError，Rust 给 ±inf / NaN。"""
    try:
        return a / b
    except ZeroDivisionError:
        if a == 0.0:
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def compare_mean_time(l: BenchmarkResult, r: BenchmarkResult) -> int:
    """返回 -1 / 0 / 1；NaN（不可比较）按相等处理，对应 unwrap_or(Ordering::Equal)。"""
    try:
        if math.isnan(l.mean) or math.isnan(r.mean):
            return 0
    except TypeError:
        return 0
    if l.mean < r.mean:
        return -1
    if l.mean > r.mean:
        return 1
    return 0


def fastest_of(results: Sequence[BenchmarkResult]) -> BenchmarkResult:
    return min(results, key=lambda r: r.mean)


@dataclass
class WithRelativeSpeed:
    result: BenchmarkResult
    relative_speed: float
    relative_speed_stddev: Optional[float]
    is_reference: bool
    relative_ordering: int


def compute_relative_speeds(results: Sequence[BenchmarkResult],
                            reference: BenchmarkResult) -> List[WithRelativeSpeed]:
    out = []
    for result in results:
        is_reference = result is reference
        ordering = compare_mean_time(result, reference)
        if result.mean == 0.0:
            out.append(WithRelativeSpeed(result, 1.0 if is_reference else math.inf,
                                         None, is_reference, ordering))
            continue
        if ordering < 0:
            ratio = _fdiv(reference.mean, result.mean)
        elif ordering == 0:
            ratio = 1.0
        else:
            ratio = _fdiv(result.mean, reference.mean)
        rs = None
        if result.stddev is not None and reference.stddev is not None:
            rs = ratio * math.sqrt(_fdiv(result.stddev, result.mean) ** 2 +
                                   _fdiv(reference.stddev, reference.mean) ** 2)
        out.append(WithRelativeSpeed(result, ratio, rs, is_reference, ordering))
    return out


def relative_speeds(results: Sequence[BenchmarkResult],
                    reference: Optional[BenchmarkResult] = None) -> List[WithRelativeSpeed]:
    if reference is None:
        reference = fastest_of(results)
    return compute_relative_speeds(results, reference)


def sort_by_mean_time(items: List[WithRelativeSpeed]) -> List[WithRelativeSpeed]:
    return sorted(items, key=lambda w: w.result.mean)


# --------------------------------------------------------------------------
# 多维时的"公平"：固定其它维度后再比
# --------------------------------------------------------------------------
def group_by(results: Sequence[BenchmarkResult], fixed: Sequence[str]) -> Dict[tuple, List[BenchmarkResult]]:
    """按 fixed 之外的维度分组：组内只有 fixed 那个维度在变。"""
    groups: Dict[tuple, List[BenchmarkResult]] = {}
    for r in results:
        key = tuple(sorted((k, v) for k, v in r.parameters.items() if k not in fixed))
        groups.setdefault(key, []).append(r)
    return groups


def grouped_relative_speeds(results: Sequence[BenchmarkResult],
                            varying: str) -> Dict[tuple, List[WithRelativeSpeed]]:
    """在每个"其它维度都相同"的组内各自选参考点，避免跨维度做比值。"""
    return {k: relative_speeds(v) for k, v in group_by(results, [varying]).items()}


# --------------------------------------------------------------------------
# JMH 的 @Param：外积 + Enum 默认全量
# --------------------------------------------------------------------------
def param_outer_product(params: Dict[str, List[str]]) -> List[Dict[str, str]]:
    """复刻"When multiple @Param-s are needed, JMH will compute the outer product"。"""
    keys = sorted(params)
    combos = []
    for values in product(*[params[k] for k in keys]):
        combos.append(dict(zip(keys, [str(v) for v in values])))
    return combos


def enum_defaults(constants: Sequence[str]) -> List[str]:
    """Enum 是唯一有隐式默认值的类型：全部枚举常量。"""
    return list(constants)


if __name__ == "__main__":
    print("RangeStep 0..10 step 3  ->", RangeStep(0, 10, 3).values(),
          " size_hint =", RangeStep(0, 10, 3).size_hint())
    print("RangeStep 0..1  step 0.1 ->", [str(v) for v in RangeStep(Decimal(0), Decimal(1), Decimal("0.1")).values()][:3],
          "... 共", len(RangeStep(Decimal(0), Decimal(1), Decimal("0.1")).values()),
          "个，size_hint =", RangeStep(Decimal(0), Decimal(1), Decimal("0.1")).size_hint())

    results = [
        BenchmarkResult("app --threads=1 --size=small", 10.0, 0.5, {"threads": "1", "size": "small"}),
        BenchmarkResult("app --threads=1 --size=large", 40.0, 2.0, {"threads": "1", "size": "large"}),
        BenchmarkResult("app --threads=8 --size=small", 4.0, 0.2, {"threads": "8", "size": "small"}),
        BenchmarkResult("app --threads=8 --size=large", 16.0, 0.8, {"threads": "8", "size": "large"}),
    ]
    print("\n全局参考（fastest = %s）：" % fastest_of(results).command)
    for w in relative_speeds(results):
        print("  %-32s ×%.2f  ±%.3f" % (w.result.command, w.relative_speed,
                                        w.relative_speed_stddev or 0.0))
    print("\n分组参考（固定 threads，只比 size）：")
    for key, items in grouped_relative_speeds(results, "size").items():
        print("  ", dict(key), "→", ["×%.2f(%s)" % (w.relative_speed, w.result.parameters["size"])
                                     for w in items])
    print("\nJMH @Param 外积 2×3 =", len(param_outer_product(
        {"mode": ["a", "b"], "size": ["s", "m", "l"]})))
