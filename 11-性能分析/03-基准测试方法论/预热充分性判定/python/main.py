#!/usr/bin/env python3
"""预热充分性的**程序化判定**：pyperf 的 warmup 校准 vs JMH 的固定暖机次数。

口径全部来自逐行实读的源码：

psf/pyperf ``pyperf/_worker.py``（``WorkerTask.test_calibrate_warmups``）
  1. 把 ``warmups`` 里 nwarmup **之后**的值对半分成 sample1 / sample2：
     ``half = nwarmup + (len(warmups) - nwarmup) // 2``；
  2. **离群检验只查最大值**：``values = sample1[1:] + sample2``（**故意把首值排除在
     分布之外**），``outlier_max = q3 + 1.5 * iqr``，``outlier = not (first_value <= outlier_max)``；
  3. 五条不等式全部通过才算"预热够了"：
     ``not outlier``、``-0.5 <= mean_diff <= 0.10``、``abs(mad_diff) <= 0.10``、
     ``abs(q1_diff) <= 0.05``、``abs(q3_diff) <= 0.05``
     —— 其中 ``mean_diff = (mean1 - mean2) / mean2`` 是**非对称**的：
     第一段比第二段**慢**最多只能慢 10%，**快**却可以快到 50%；
  4. 离散度用 **MAD**（中位数绝对偏差）而不是 stdev（stdev 只在 verbose 里打印，不参与判定）；
  5. 失败就 ``nwarmup += 1`` 并**只补算缺的值**：``total = nwarmup + WARMUP_SAMPLE_SIZE * 2``、
     ``nvalue = total - len(self.warmups)``；``MAX_WARMUP_VALUES = 300`` 到顶即放弃。

psf/pyperf ``pyperf/_utils.py``（``percentile`` / ``median_abs_dev``）
  6. ``percentile`` 是**线性插值**：``k = (len-1) * p``，``f != c`` 时
     ``values[f] * (c - k) + values[c] * (k - f)``；
  7. ``median_abs_dev`` = ``median(|median(values) - x| for x in values)``。

psf/pyperf ``pyperf/_runner.py``
  8. 默认 ``warmups = 1``（非 JIT 且不在 worker 里）；``--debug-single-value`` 把
     ``warmups`` 直接置 0；``--calibrate-warmups`` 必须先给 ``--loops=N``（``loops < 1`` 报错）。

openjdk/jmh ``org/openjdk/jmh/runner/Defaults.java`` 与 ``annotations/Warmup.java``
  9. JMH **没有任何自动判定**：``WARMUP_ITERATIONS = 5``、``WARMUP_ITERATIONS_SINGLESHOT = 0``、
     ``WARMUP_FORKS = 0``、``WARMUP_TIME = 10s``、``WARMUP_MODE = INDI``，
     注解的 ``BLANK_ITERATIONS = -1`` 只是"未指定"的哨兵，最终落到 Defaults。

无第三方依赖；自检完全确定性（噪声用固定种子的 LCG，不依赖系统时间）。
"""

from __future__ import annotations

import math
import statistics
from typing import Callable, List, Sequence, Tuple

MAX_WARMUP_VALUES = 300
WARMUP_SAMPLE_SIZE = 20

# pyperf/_runner.py 里的默认值（实读）
PYPERF_DEFAULT_WARMUPS = 1          # 非 JIT 且不在 worker 里 → args.warmups = 1
PYPERF_DEBUG_SINGLE_VALUE_WARMUPS = 0   # --debug-single-value 直接置 0
PYPERF_CALIBRATE_REQUIRES_LOOPS = True  # --calibrate-warmups 要求 loops >= 1

# JMH Defaults.java 里的常量（实读）
JMH_DEFAULTS = {
    "WARMUP_ITERATIONS": 5,
    "WARMUP_ITERATIONS_SINGLESHOT": 0,
    "WARMUP_BATCHSIZE": 1,
    "WARMUP_TIME_S": 10,
    "MEASUREMENT_ITERATIONS": 5,
    "MEASUREMENT_FORKS": 5,
    "WARMUP_FORKS": 0,
    "WARMUP_MODE": "INDI",
}


class CalibrationError(Exception):
    """对应 pyperf 里 "ERROR: failed to calibrate the number of warmups" + sys.exit(1)。"""


# --------------------------------------------------------------------------
# pyperf 的两个统计原语
# --------------------------------------------------------------------------
def percentile(values: Sequence[float], p: float) -> float:
    if not isinstance(p, float) or not (0.0 <= p <= 1.0):
        raise ValueError("p must be a float in the range [0.0; 1.0]")
    vals = sorted(values)
    if not vals:
        raise ValueError("no value")
    k = (len(vals) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f != c:
        return vals[f] * (c - k) + vals[c] * (k - f)
    return vals[int(k)]


def median_abs_dev(values: Sequence[float]) -> float:
    med = float(statistics.median(values))
    return statistics.median([abs(med - sample) for sample in values])


# --------------------------------------------------------------------------
# pyperf 的稳态判据
# --------------------------------------------------------------------------
def warmup_diagnostics(warmups: Sequence[float], nwarmup: int) -> dict:
    """把五条判据各自的值与「哪条没过」摊平返回，便于逐条断言。"""
    half = nwarmup + (len(warmups) - nwarmup) // 2
    sample1 = list(warmups[nwarmup:half])
    sample2 = list(warmups[half:])
    first_value = sample1[0]

    # 首值单独拿出来比：分布里**不含**它自己
    values = sample1[1:] + sample2
    q1 = percentile(values, 0.25)
    q3 = percentile(values, 0.75)
    iqr = q3 - q1
    outlier_max = q3 + 1.5 * iqr
    outlier = not (first_value <= outlier_max)      # 只查最大值，不查最小值

    mean1 = statistics.mean(sample1)
    mean2 = statistics.mean(sample2)
    mean_diff = (mean1 - mean2) / float(mean2)

    s1_q1 = percentile(sample1, 0.25)
    s2_q1 = percentile(sample2, 0.25)
    s1_q3 = percentile(sample1, 0.75)
    s2_q3 = percentile(sample2, 0.75)
    q1_diff = (s1_q1 - s2_q1) / float(s2_q1)
    q3_diff = (s1_q3 - s2_q3) / float(s2_q3)

    mad1 = median_abs_dev(sample1)
    mad2 = median_abs_dev(sample2)
    # 官方此处有 "FIXME: handle division by zero"：mad2 为 0 会抛 ZeroDivisionError。
    # demo 里按 0.0 处理并在 README 标注，避免把这条已知缺陷当成"通过"。
    mad_diff = (mad1 - mad2) / float(mad2) if mad2 != 0 else 0.0

    reasons: List[str] = []
    if outlier:
        reasons.append("outlier")
    if not (-0.5 <= mean_diff <= 0.10):
        reasons.append("mean_diff")
    if abs(mad_diff) > 0.10:
        reasons.append("mad_diff")
    if abs(q1_diff) > 0.05:
        reasons.append("q1_diff")
    if abs(q3_diff) > 0.05:
        reasons.append("q3_diff")

    return {
        "first_value": first_value, "outlier": outlier, "outlier_max": outlier_max,
        "q1": q1, "q3": q3, "iqr": iqr,
        "mean1": mean1, "mean2": mean2, "mean_diff": mean_diff,
        "q1_diff": q1_diff, "q3_diff": q3_diff,
        "mad1": mad1, "mad2": mad2, "mad_diff": mad_diff,
        "sample1": sample1, "sample2": sample2, "failed": reasons,
    }


def test_calibrate_warmups(warmups: Sequence[float], nwarmup: int, verbose: bool = False) -> bool:
    """复刻 WorkerTask.test_calibrate_warmups：五条全过才算预热够了。"""
    d = warmup_diagnostics(warmups, nwarmup)
    if verbose:
        print("    nwarmup=%d outlier=%s(max=%.3f) mean_diff=%+.4f "
              "q1_diff=%+.4f q3_diff=%+.4f mad_diff=%+.4f"
              % (nwarmup, d["outlier"], d["outlier_max"], d["mean_diff"],
                 d["q1_diff"], d["q3_diff"], d["mad_diff"]))
    return not d["failed"]


def calibrate_warmups(
    source: Callable[[int, int], List[float]],
    nwarmup_start: int = 1,
    max_values: int = MAX_WARMUP_VALUES,
    sample_size: int = WARMUP_SAMPLE_SIZE,
) -> Tuple[int, List[float]]:
    """复刻 WorkerTask.calibrate_warmups：返回 (nwarmup, warmups)。

    ``source(start, n)`` 生成下标 start 起的 n 个值（对应 _compute_values）。
    """
    warmups: List[float] = []
    nwarmup = nwarmup_start
    while True:
        total = nwarmup + sample_size * 2
        nvalue = total - len(warmups)
        if nvalue:
            warmups.extend(source(len(warmups), nvalue))
        if test_calibrate_warmups(warmups, nwarmup):
            break
        if len(warmups) >= max_values:
            raise CalibrationError(
                "failed to calibrate the number of warmups (%d values)" % len(warmups))
        nwarmup += 1
    return nwarmup, warmups


# --------------------------------------------------------------------------
# JMH 侧：固定次数
# --------------------------------------------------------------------------
def jmh_warmup_plan(mode: str = "thrpt", iterations: int = -1, time_s: int = -1) -> dict:
    """复刻 @Warmup 的 BLANK_* 哨兵 + Defaults 兜底。"""
    if mode == "ss":                                     # SingleShotTime
        default_iters = JMH_DEFAULTS["WARMUP_ITERATIONS_SINGLESHOT"]
    else:
        default_iters = JMH_DEFAULTS["WARMUP_ITERATIONS"]
    iters = default_iters if iterations == -1 else iterations
    t = JMH_DEFAULTS["WARMUP_TIME_S"] if time_s == -1 else time_s
    return {"iterations": iters, "time_s": t,
            "batchSize": JMH_DEFAULTS["WARMUP_BATCHSIZE"],
            "warmup_forks": JMH_DEFAULTS["WARMUP_FORKS"],
            "warmup_mode": JMH_DEFAULTS["WARMUP_MODE"]}


def jmh_warmup_is_enough(curve: Sequence[float], iterations: int) -> Tuple[bool, float]:
    """JMH 没有判据，只能"跑满 N 次就算数"。返回 (是否真的到稳态, 最后一次相对稳态的偏差)。"""
    steady = statistics.median(curve[-20:]) if len(curve) >= 20 else statistics.median(curve)
    last = curve[iterations - 1] if 0 < iterations <= len(curve) else curve[-1]
    deviation = (last - steady) / steady
    return abs(deviation) <= 0.10, deviation


# --------------------------------------------------------------------------
# 确定性的"JIT 升温"曲线
# --------------------------------------------------------------------------
class Lcg:
    """固定种子的线性同余，保证噪声可复现（不依赖系统随机源）。"""

    def __init__(self, seed: int = 20260921) -> None:
        self.state = seed

    def uniform_pm1(self) -> float:
        self.state = (1103515245 * self.state + 12345) % (1 << 31)
        return (self.state / (1 << 31)) * 2.0 - 1.0


def jit_curve(n: int, *, steady: float = 100.0, overhead: float = 900.0,
              decay: float = 0.7, noise: float = 1.0, seed: int = 20260921) -> List[float]:
    """value(k) = steady + overhead * decay^k + 幅度 noise 的确定性抖动。"""
    rng = Lcg(seed)
    return [steady + overhead * decay ** k + noise * rng.uniform_pm1() for k in range(n)]


def curve_source(n: int, *, offset: int = 0, **kw) -> Callable[[int, int], List[float]]:
    """把整条曲线包装成 calibrate_warmups 需要的 source(start, nvalue)。"""
    full = jit_curve(n + 400, **kw)

    def src(start: int, nvalue: int) -> List[float]:
        return full[offset + start: offset + start + nvalue]

    return src


if __name__ == "__main__":
    src = curve_source(400)
    n, warmups = calibrate_warmups(src)
    print("pyperf 校准出的 warmup 次数 =", n, " 共采样", len(warmups), "个值")
    curve = jit_curve(400)
    enough, dev = jmh_warmup_is_enough(curve, JMH_DEFAULTS["WARMUP_ITERATIONS"])
    print("JMH 默认 %d 次暖机: 到稳态? %s, 最后一次相对稳态偏差 %+.2f%%"
          % (JMH_DEFAULTS["WARMUP_ITERATIONS"], enough, dev * 100))
    print("默认值对照: pyperf 非 JIT warmups=1 / --debug-single-value warmups=0；"
          "JMH Defaults =", JMH_DEFAULTS)
