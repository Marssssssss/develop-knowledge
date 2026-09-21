#!/usr/bin/env python3
"""参数化基准多维比较的自检（确定性）。运行：python selfcheck_param.py"""

from __future__ import annotations

import math
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    BLANK_ARGS,
    MAX_PARAMETERS,
    BenchmarkResult,
    ParameterScanError,
    RangeStep,
    compare_mean_time,
    enum_defaults,
    fastest_of,
    group_by,
    grouped_relative_speeds,
    param_outer_product,
    relative_speeds,
    sort_by_mean_time,
)

PASS = 0
FAIL = 0


def ok(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", label, detail)
    else:
        FAIL += 1
        print("  FAIL", label, detail)


def err_of(fn) -> str:
    try:
        fn()
    except ParameterScanError as e:
        return str(e)
    return ""


print("E1 RangeStep：闭区间、按步长累加、越过 end 就停")
ok(RangeStep(0, 10, 3).values() == [0, 3, 6, 9], "E1a 0..10 step 3 = [0,3,6,9]")
ok(RangeStep(0, 10, 1).values() == list(range(11)), "E1b step 1 时取满闭区间")
ok(max(RangeStep(0, 10, 3).values()) <= 10, "E1c 最后一个值不越过 end")

print("E2 三条拒绝路径")
ok(err_of(lambda: RangeStep(11, 10, 1)) == "Empty parameter range", "E2a end < start")
ok(err_of(lambda: RangeStep(0, 10, 0)) == "Zero is not a valid parameter step", "E2b step = 0")
ok(err_of(lambda: RangeStep(0, 100_001, 1)) == "Parameter range is too large",
   "E2c 超过 MAX_PARAMETERS")

print("E3 size_hint 的官方公式加的是绝对值 1，不是一步")
r = RangeStep(0, 10, 3)
ok(r.size_hint() == 3 and len(r.values()) == 4,
   "E3a step>1 时**低估**：报 3 而实际 4",
   "hint=%d 实际=%d" % (r.size_hint(), len(r.values())))
d = RangeStep(Decimal(0), Decimal(1), Decimal("0.1"))
ok(d.size_hint() == 20 and len(d.values()) == 11,
   "E3b step<1 时**高估**：报 20 而实际 11",
   "hint=%d 实际=%d" % (d.size_hint(), len(d.values())))
r1 = RangeStep(0, 10, 1)
ok(r1.size_hint() == len(r1.values()) == 11, "E3c 只有 step == 1 时精确")

print("E4 低估的代价：在 100000 上限处会放过超限的范围")
big = RangeStep(0, 300_000, 3)
ok(big.size_hint() == MAX_PARAMETERS, "E4a size_hint 恰好等于上限，通过检查")
ok(len(big.values()) == MAX_PARAMETERS + 1,
   "E4b 但实际产生 %d 个值（多一个）" % len(big.values()))

print("E5 参考点是全局最快，比值恒 ≥ 1")
results = [
    BenchmarkResult("c1", 40.0, 2.0, {"threads": "1", "size": "large"}),
    BenchmarkResult("c2", 10.0, 0.5, {"threads": "1", "size": "small"}),
    BenchmarkResult("c3", 4.0, 0.2, {"threads": "8", "size": "small"}),
    BenchmarkResult("c4", 16.0, 0.8, {"threads": "8", "size": "large"}),
]
ok(fastest_of(results).command == "c3", "E5a fastest_of 取均值最小")
sp = {w.result.command: w for w in relative_speeds(results)}
ok(sp["c3"].is_reference and sp["c3"].relative_speed == 1.0, "E5b 参考自己 = 1.0")
ok(all(w.relative_speed >= 1.0 for w in sp.values()), "E5c 全部 ≥ 1")
ok(abs(sp["c1"].relative_speed - 10.0) < 1e-12, "E5d 最慢的 40/4 = 10.0")
ok(abs(sp["c2"].relative_speed - 2.5) < 1e-12, "E5e 10/4 = 2.5")

print("E6 mean == 0 的两个分支")
zero = BenchmarkResult("z", 0.0, None)
ref = BenchmarkResult("r", 5.0, None)
ok(fastest_of([ref, zero]).command == "z",
   "E6a mean=0 会被 fastest_of 当成最快（所以它通常自己就是参考）")
w = {x.result.command: x for x in relative_speeds([zero, ref], reference=ref)}
ok(math.isinf(w["z"].relative_speed) and not w["z"].is_reference,
   "E6b 显式指定参考后，非参考且 mean=0 ⇒ INFINITY")
w2 = {x.result.command: x for x in relative_speeds([ref, zero], reference=zero)}
ok(math.isinf(w2["r"].relative_speed) and w2["z"].relative_speed == 1.0,
   "E6c 参考 mean=0 时，别人是 5/0 ⇒ inf（Rust 的 f64 语义；Python 直写会抛异常）")
z0 = BenchmarkResult("z", 0.0, None)
w = {x.result.command: x for x in relative_speeds([z0], reference=z0)}
ok(w["z"].relative_speed == 1.0, "E6b 参考自己 mean=0 ⇒ 1.0（不是 INFINITY）")

print("E7 相对速度的标准差走误差传播（假定协方差 0）")
a = BenchmarkResult("a", 10.0, 1.0)
b = BenchmarkResult("b", 5.0, 0.5)
w = {x.result.command: x for x in relative_speeds([a, b])}
expect = 2.0 * math.sqrt((1.0 / 10.0) ** 2 + (0.5 / 5.0) ** 2)
ok(abs(w["a"].relative_speed_stddev - expect) < 1e-12,
   "E7a 2.0 × √((1/10)²+(0.5/5)²) = %.6f" % expect)
ok(w["b"].relative_speed_stddev is not None, "E7b 两侧都有 stddev 才算得出")
no_sd = BenchmarkResult("n", 20.0, None)
w = {x.result.command: x for x in relative_speeds([no_sd, b])}
ok(w["n"].relative_speed_stddev is None, "E7c 任一侧缺失 ⇒ None")

print("E8 compare_mean_time 对 NaN 按相等处理")
nan = BenchmarkResult("n", float("nan"))
ok(compare_mean_time(nan, a) == 0, "E8a NaN 参与比较 ⇒ Equal")

print("E9 排序：可以按命令顺序原样输出，也可以按均值排")
items = relative_speeds(results)
ok([w.result.mean for w in sort_by_mean_time(items)] == [4.0, 10.0, 16.0, 40.0],
   "E9a SortOrder::MeanTime")

print("E10 parameters 是 BTreeMap ⇒ 按参数名字典序")
r = BenchmarkResult("c", 1.0, None, {"size": "small", "threads": "8"})
ok(r.sorted_parameters == ["small", "8"],
   "E10a 列顺序按 key 字典序：size 在 threads 前", str(r.sorted_parameters))
ok(sorted(r.parameters) == ["size", "threads"], "E10b 与插入顺序无关")

print("E11 多维陷阱：全局参考会把两个维度混成一个数")
ok(abs(sp["c1"].relative_speed - (4.0 * 2.5)) < 1e-12,
   "E11a c1 的 ×10 = size 的 4× 乘 threads 的 2.5×（10 = 4 × 2.5）")
groups = grouped_relative_speeds(results, "size")
ok(len(groups) == 2, "E11b 按 threads 分成 2 组")
ratios = []
for key, items in groups.items():
    d = {w.result.parameters["size"]: w.relative_speed for w in items}
    ratios.append(round(d["large"] / d["small"], 6))
ok(len(set(ratios)) == 1 and ratios[0] == 4.0,
   "E11c 组内结论一致：large 恒为 small 的 4 倍", str(ratios))
ok(abs(sp["c1"].relative_speed / sp["c4"].relative_speed - 2.5) < 1e-12,
   "E11d 而全局比值里混进了 threads 的 2.5×，跨维度不可比")

print("E12 group_by 的键是「被固定的维度之外」的组合")
g = group_by(results, ["size"])
ok(sorted(k[0][1] for k in g) == ["1", "8"], "E12a 键只剩 threads", str(list(g)))

print("E13 JMH @Param：多参数走外积")
combos = param_outer_product({"mode": ["a", "b"], "size": ["s", "m", "l"]})
ok(len(combos) == 6, "E13a 2 × 3 = 6")
ok({"mode": "b", "size": "l"} in combos, "E13b 组合完整")
ok(len(param_outer_product({"a": ["1"], "b": ["2"], "c": ["3"]})) == 1, "E13c 单值组 = 1")

print("E14 Enum 是唯一有隐式默认值的类型")
ok(enum_defaults(["RED", "GREEN", "BLUE"]) == ["RED", "GREEN", "BLUE"], "E14a 取全部常量")
ok(BLANK_ARGS == "blank_blank_blank_2014", "E14b BLANK_ARGS 哨兵")

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
