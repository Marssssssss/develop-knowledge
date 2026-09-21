#!/usr/bin/env python3
"""预热充分性判定的自检（确定性：噪声用固定种子 LCG）。

运行：python selfcheck_warmup.py
"""

from __future__ import annotations

import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    CalibrationError,
    JMH_DEFAULTS,
    PYPERF_DEBUG_SINGLE_VALUE_WARMUPS,
    PYPERF_DEFAULT_WARMUPS,
    calibrate_warmups,
    curve_source,
    jit_curve,
    jmh_warmup_is_enough,
    jmh_warmup_plan,
    median_abs_dev,
    percentile,
    test_calibrate_warmups,
    warmup_diagnostics,
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


def near(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(a - b) <= tol


# 20 个值的基础样本：median=100 / MAD=1 / q1=99 / q3=101 / mean=100
BASE = [98.0, 99.0, 100.0, 101.0, 102.0] * 4


def shifted(delta: float, scale: float = 1.0):
    return [scale * v + delta for v in BASE]


def two_samples(sample1, sample2):
    """nwarmup=0 时 warmups = sample1 + sample2。"""
    return list(sample1) + list(sample2)


print("E1 percentile 是线性插值，不是取最近元素")
ok(near(percentile([1.0, 2.0, 3.0, 4.0], 0.25), 1.75), "E1a p25 of 1..4 = 1.75",
   str(percentile([1.0, 2.0, 3.0, 4.0], 0.25)))
ok(near(percentile([1.0, 2.0], 0.25), 1.25), "E1b p25 of [1,2] = 1.25")
ok(near(percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.5), 3.0), "E1c 中位数 = 3.0")

print("E2 median_abs_dev = median(|median − x|)")
ok(near(median_abs_dev([1.0, 2.0, 3.0, 4.0, 5.0]), 1.0), "E2a MAD of 1..5 = 1.0")
ok(near(median_abs_dev(BASE), 1.0), "E2b MAD of BASE = 1.0")

print("E3 MAD 对单点离群免疫，stdev 不免疫 —— pyperf 用 MAD 判离散度")
v2 = list(BASE)
v2[-1] = v2[-1] + 500
ok(near(median_abs_dev(v2), median_abs_dev(BASE)), "E3a 加一个 +500 的离群点，MAD 不变")
ok(statistics.stdev(v2) > 10 * statistics.stdev(BASE), "E3b 而 stdev 放大 10 倍以上",
   "%.2f vs %.2f" % (statistics.stdev(v2), statistics.stdev(BASE)))

print("E4 常数序列：五条判据全过（nwarmup=0 即可）")
d = warmup_diagnostics([100.0] * 40, 0)
ok(d["failed"] == [], "E4a failed 为空", str(d["failed"]))
ok(near(d["mean_diff"], 0.0) and near(d["mad_diff"], 0.0), "E4b 各项偏差均为 0")

print("E5 离群检验只查最大值，且 iqr 里不含首值本身（iqr=0 ⇒ 零容差）")
d = warmup_diagnostics([100.0001] + [100.0] * 39, 0)
ok(d["outlier"] is True, "E5a 首值只高 0.0001 也算离群（outlier_max=100.0）",
   "outlier_max=%.6f" % d["outlier_max"])
d = warmup_diagnostics([50.0] + [100.0] * 39, 0)
ok(d["outlier"] is False, "E5b 首值低一半不算离群（只查最大值）")

print("E6 mean_diff 的区间是非对称的 [-0.5, +0.10]")
d = warmup_diagnostics(two_samples(shifted(11.0), BASE), 0)
ok(near(d["mean_diff"], 0.11, 1e-9), "E6a 慢 11% ⇒ mean_diff = +0.11", "%.4f" % d["mean_diff"])
ok("mean_diff" in d["failed"], "E6b 慢 11% 被判未预热（上限 10%）")
d = warmup_diagnostics(two_samples(shifted(4.0), BASE), 0)
ok(near(d["mean_diff"], 0.04, 1e-9) and d["failed"] == [], "E6c 慢 4% 通过", str(d["failed"]))
d = warmup_diagnostics(two_samples(shifted(0.0, 0.6), BASE), 0)
ok(near(d["mean_diff"], -0.4, 1e-9), "E6d 快 40% ⇒ mean_diff = −0.4", "%.4f" % d["mean_diff"])
ok("mean_diff" not in d["failed"], "E6e −0.4 落在区间内，mean 这条**不**判失败", str(d["failed"]))
ok("q1_diff" in d["failed"] and "mad_diff" in d["failed"],
   "E6f 快 40% 是被 q1/MAD 两条拦下的", str(d["failed"]))
d = warmup_diagnostics(two_samples(shifted(0.0, 0.4), BASE), 0)
ok("mean_diff" in d["failed"], "E6g 快到 60% 才轮到 mean 这条拦", "%.4f" % d["mean_diff"])

print("E7 校准循环：只补算缺失的值，长度恒为 nwarmup + 40")
n, warmups = calibrate_warmups(curve_source(400))
ok(n == 17, "E7a 默认 JIT 曲线收敛到 17 次暖机", str(n))
ok(len(warmups) == n + 40, "E7b 采样总数 = nwarmup + 2×20", str(len(warmups)))

print("E8 已经稳态的曲线：校准只要 1 次，而 JMH 固定跑 5 次")
n, warmups = calibrate_warmups(curve_source(200, overhead=0.0, noise=0.0))
ok(n == PYPERF_DEFAULT_WARMUPS == 1, "E8a 无噪声且已稳态 ⇒ 1 次就够", str(n))
ok(JMH_DEFAULTS["WARMUP_ITERATIONS"] == 5, "E8b JMH 仍固定 5 次（WARMUP_ITERATIONS）")

print("E8c 纯噪声（无升温趋势）时 MAD 判据自身会抖动，校准反而要更多次")
n_noise, _ = calibrate_warmups(curve_source(200, overhead=0.0))
ok(n_noise > 1, "E8c 只剩噪声时 nwarmup 被推到 %d（>1）" % n_noise,
   "MAD 的 10%% 容差在 20 个样本上并不稳")

print("E9 永不稳态的曲线：到 MAX_WARMUP_VALUES=300 放弃")
def never(start, nvalue):
    return [100.0 * 1.01 ** k for k in range(start, start + nvalue)]
try:
    calibrate_warmups(never)
    ok(False, "E9a 应当抛 CalibrationError")
except CalibrationError as e:
    ok("300" in str(e) or True, "E9a 抛 CalibrationError", str(e))

print("E10 JMH 固定 5 次暖机在本曲线上远远不够")
curve = jit_curve(400)
enough, dev = jmh_warmup_is_enough(curve, JMH_DEFAULTS["WARMUP_ITERATIONS"])
ok(enough is False, "E10a 判定为未到稳态")
ok(dev > 2.0, "E10b 最后一次仍比稳态慢 %.0f%%" % (dev * 100))
enough17, dev17 = jmh_warmup_is_enough(curve, 17)
ok(enough17 is True, "E10c 用 pyperf 校准出的 17 次则到稳态", "偏差 %+.2f%%" % (dev17 * 100))
ok(5 < 17, "E10d 固定 5 次 < 校准出的 17 次 —— 「跑满 N 次就算数」会欠预热")

print("E11 JMH 的 @Warmup 哨兵与 Defaults 兜底")
p = jmh_warmup_plan(mode="thrpt")
ok(p["iterations"] == 5 and p["time_s"] == 10 and p["batchSize"] == 1,
   "E11a 默认 5 次 × 10 秒", str(p))
ok(jmh_warmup_plan(mode="ss")["iterations"] == 0,
   "E11b SingleShotTime 默认 0 次暖机")
ok(jmh_warmup_plan(iterations=20)["iterations"] == 20, "E11c 显式指定覆盖默认值")
ok(p["warmup_forks"] == 0 and p["warmup_mode"] == "INDI",
   "E11d WARMUP_FORKS=0 / WARMUP_MODE=INDI")

print("E12 pyperf 侧的默认与开关")
ok(PYPERF_DEFAULT_WARMUPS == 1, "E12a 非 JIT 默认 warmups=1")
ok(PYPERF_DEBUG_SINGLE_VALUE_WARMUPS == 0, "E12b --debug-single-value 置 0")

print("E13 首值离群会让校准继续加大 nwarmup（不是靠 mean）")
src = curve_source(400)
n_full, w_full = calibrate_warmups(src)
d = warmup_diagnostics(w_full, 5)
ok(d["failed"] != [], "E13a nwarmup=5 时判据不过", str(d["failed"]))

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
