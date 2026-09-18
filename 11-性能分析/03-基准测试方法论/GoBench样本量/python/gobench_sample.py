#!/usr/bin/env python3
"""Go ``testing.B`` 的迭代标定(benchtime)与样本量(-count):``-benchtime=1000000x`` 到底给了你多少个样本。

全部口径来自逐行实读的官方源码 ``golang/go`` ``src/testing/benchmark.go``(master)与
``pkg.go.dev/testing``,不凭记忆:

1. **``-benchtime`` 是 ``durationOrCountFlag``** —— 官方源码里并没有独立的 ``parseBenchTime``;
   解析就在 ``func (f *durationOrCountFlag) Set(s string) error`` 里:以 ``"x"`` 结尾走
   ``strconv.ParseInt`` 得到次数,否则走 ``time.ParseDuration``。``n < 0`` 或
   ``!allowZero && n == 0`` 一律 ``invalid count``;``d < 0`` 或零一律 ``invalid duration``
   —— 所以 ``0x`` / ``0s`` 都是**非法值**,不是"跑零次"。
   默认值写在变量初始化里:``benchTime = durationOrCountFlag{d: 1 * time.Second}``。

2. **``b.N`` 不是 1,2,5,10,20,50... 那种固定序列** —— 这是流传很广的误解。当前实现里
   ``run1`` 先 ``runN(1)``;``launch`` 若 ``benchTime.n > 0`` 且 ``n > 1`` 直接 ``runN(n)``
   (**``1x`` 时连这一次都不跑,直接复用 ``run1`` 的结果**,源码注释指向 golang.org/issue/32051);
   duration 形式则循环调用 ``predictN`` 动态预测。

3. **``predictN`` 四道钳制** —— ``n = goalns*prevIters/prevns``(**先乘后除**,注释明说先除会让
   极快基准得到 0/1 从而"hide an order of magnitude");``n += n/5`` 多跑 20%;
   ``n = min(n, 100*last)`` 防止上一次计时误差导致暴涨;``n = max(n, last+1)`` 至少多一次;
   最后 ``n = min(n, maxBenchPredictIters)``,该常量 ``= 1_000_000_000``,``launch`` 的循环条件
   也写着 ``n < 1e9``。

4. **每一轮 ``runN`` 都会 ``runtime.GC()``** —— 源码注释:"Try to get a comparable environment
   for each run by clearing garbage from previous runs"。紧接着才 ``ResetTimer/StartTimer``,
   所以 GC 时间不计入 ns/op。

5. **``ns/op`` 与 ``B/op`` 都是整数除法** —— ``NsPerOp = r.T.Nanoseconds()/int64(r.N)``,
   ``AllocedBytesPerOp = int64(r.MemBytes)/int64(r.N)``,没有四舍五入。

6. **样本量来自 ``-count`` 而不是 ``-benchtime``** —— benchstat 官方文档要求"Each benchmark
   should be run at least 10 times",并在 Tips 里给"at least 10, ideally 20";``-benchtime``
   只决定**一次测量内部**跑多少遍,``-count`` 才决定有几个独立测量值。

无第三方依赖;固定种子保证自检可复现。
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

# Go 源码常量
MAX_BENCH_PREDICT_ITERS = 1_000_000_000
DEFAULT_BENCHTIME = "1s"

_UNIT_NS = {
    "ns": 1,
    "us": 1_000,
    "µs": 1_000,          # Go 的 time.ParseDuration 同时接受 us 与 µs
    "μs": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "m": 60_000_000_000,
    "h": 3_600_000_000_000,
}


class InvalidValue(ValueError):
    """对应 Go 侧的 invalid count / invalid duration。"""


class DurationOrCount:
    """``durationOrCountFlag`` 的等价物:d 与 n 互斥,二者至多一个有意义。"""

    __slots__ = ("d", "n", "allow_zero")

    def __init__(self, d: int = 0, n: int = 0, allow_zero: bool = False):
        self.d = d
        self.n = n
        self.allow_zero = allow_zero

    def __repr__(self) -> str:
        if self.n > 0:
            return f"{self.n}x"
        return f"{self.d}ns"

    @property
    def is_count(self) -> bool:
        return self.n > 0


def parse_duration_ns(s: str) -> int:
    """``time.ParseDuration`` 的最小复刻:只支持带单位的组合形式(不接受裸数字)。"""
    if s == "" or s in ("+", "-"):
        raise InvalidValue("time: invalid duration " + repr(s))
    sign = 1
    body = s
    if body[0] in "+-":
        sign = -1 if body[0] == "-" else 1
        body = body[1:]
    total = 0
    i = 0
    seen_unit = False
    while i < len(body):
        j = i
        while j < len(body) and (body[j].isdigit() or body[j] == "."):
            j += 1
        if j == i:
            raise InvalidValue("time: invalid duration " + repr(s))
        num_txt = body[i:j]
        k = j
        while k < len(body) and body[k].isalpha() or (k < len(body) and body[k] in "µμ"):
            k += 1
        unit = body[j:k]
        if unit not in _UNIT_NS:
            raise InvalidValue("time: unknown unit " + repr(unit))
        seen_unit = True
        total += int(round(float(num_txt) * _UNIT_NS[unit]))
        i = k
    if not seen_unit:
        raise InvalidValue("time: missing unit in duration " + repr(s))
    return sign * total


def parse_benchtime(s: str, allow_zero: bool = False) -> DurationOrCount:
    """``durationOrCountFlag.Set`` 的逐行移植,错误消息与 Go 侧保持一致。"""
    if s.endswith("x"):
        txt = s[:-1]
        try:
            n = int(txt, 10)          # Go 用 strconv.ParseInt(s, 10, 0)
        except ValueError:
            raise InvalidValue("invalid count") from None
        if n < 0 or (not allow_zero and n == 0):
            raise InvalidValue("invalid count")
        return DurationOrCount(n=n, allow_zero=allow_zero)
    try:
        d = parse_duration_ns(s)
    except InvalidValue:
        raise InvalidValue("invalid duration") from None
    if d < 0 or (not allow_zero and d == 0):
        raise InvalidValue("invalid duration")
    return DurationOrCount(d=d, allow_zero=allow_zero)


def predict_n(goalns: int, prev_iters: int, prevns: int, last: int) -> int:
    """``predictN`` 的逐行移植(含四处钳制与除零兜底)。"""
    if prevns == 0:
        prevns = 1                                  # "Round up to dodge divide by zero" (issue 70709)
    n = goalns * prev_iters // prevns               # 先乘后除
    n += n // 5                                     # 多跑 20%(1.2x)
    n = min(n, 100 * last)                          # 别因上次计时误差而暴涨
    n = max(n, last + 1)                            # 至少比上次多一次
    n = min(n, MAX_BENCH_PREDICT_ITERS)             # 上限 1e9(也保证 32 位平台不溢出)
    return n


def launch_sequence(per_iter_ns: int, benchtime: DurationOrCount) -> List[int]:
    """复刻 ``run1`` + ``launch``:返回每轮 ``runN`` 的 N 值序列(含 run1 的那次 1)。

    每轮 ``runN`` 内部先 ``ResetTimer()``,故 ``b.duration`` 只是**本轮**耗时。
    """
    seq: List[int] = []
    last_n = 1
    duration = per_iter_ns * 1
    seq.append(1)                                    # run1 -> runN(1)
    if benchtime.is_count:
        if benchtime.n > 1:
            seq.append(benchtime.n)
        return seq                                   # 1x 时复用 run1,不再 runN
    n = 1
    while duration < benchtime.d and n < MAX_BENCH_PREDICT_ITERS:
        last = n
        prev_iters = last_n
        prevns = duration
        n = predict_n(benchtime.d, prev_iters, prevns, last)
        duration = per_iter_ns * n
        last_n = n
        seq.append(n)
    return seq


def ns_per_op(total_ns: int, n: int) -> int:
    """``BenchmarkResult.NsPerOp``:整数除法,直接截断。"""
    if n <= 0:
        return 0
    return total_ns // n


def alloced_bytes_per_op(net_bytes: int, n: int) -> int:
    """``BenchmarkResult.AllocedBytesPerOp``:同样是整数除法。"""
    if n <= 0:
        return 0
    return net_bytes // n


def run_parallel_grain(previous_n: int, previous_duration_ns: int) -> int:
    """``RunParallel`` 的 grain:目标 ~100µs,钳制在 [1, 1e4]。

    源码注释:grain 太小则原子加的开销占比过高,太大则负载不均衡。
    """
    grain = 0
    if previous_n > 0 and previous_duration_ns > 0:
        grain = 100_000 * previous_n // previous_duration_ns
    if grain < 1:
        grain = 1
    if grain > 10_000:
        grain = 10_000
    return grain


def median_ci_halfwidth(xs: List[float]) -> Optional[float]:
    """benchstat 风格的中位数置信区间半宽;n < 2 时无法给出(返回 None)。

    这正是 ``-count=1`` 的死穴:没有任何样本量能支撑"分布"的说法。
    """
    if len(xs) < 2:
        return None
    s = sorted(xs)
    m = len(s)
    # 基于次序统计量的近似区间(benchstat 用的是精确的 order-statistic 区间)
    z = 1.959963985
    lo = s[max(0, int((m - z * math.sqrt(m)) / 2))]
    hi = s[min(m - 1, int((m + z * math.sqrt(m)) / 2) + 1)]
    return (hi - lo) / 2.0


# --------------------------------------------------------------------------- 自检

def _self_test() -> None:
    # 1) 默认值与两种形态的解析
    default = parse_benchtime(DEFAULT_BENCHTIME)
    assert default.d == 1_000_000_000 and default.n == 0
    assert parse_benchtime("100x").n == 100
    assert parse_benchtime("1000000x").n == 1_000_000
    assert parse_benchtime("100ms").d == 100_000_000
    for bad, msg in (("0x", "invalid count"), ("-1x", "invalid count"),
                     ("0s", "invalid duration"), ("-1s", "invalid duration"),
                     ("abc", "invalid duration")):
        try:
            parse_benchtime(bad)
            raise AssertionError(f"{bad} 应当被拒绝")
        except InvalidValue as e:
            assert msg in str(e), (bad, str(e))
    print("[1] benchtime 解析:默认 1s;1x/100ms 合法;0x/0s/-1x/abc 全部被拒")

    # 2) predictN 的三处钳制(100*last / last+1 / 1.2x)
    assert predict_n(1_000_000_000, 1, 1_000, 1) == 100          # 1.2e6 被 100*last 压到 100
    assert predict_n(1_000_000_000, 100, 100_000, 100) == 10_000  # 被 100*100=1e4 压住
    assert predict_n(1_000_000_000, 10_000, 10_000_000, 10_000) == 1_000_000
    assert predict_n(1_000_000_000, 1, 0, 1) == 100               # prevns=0 -> 兜底 1
    assert predict_n(1_000_000_000, 1, 1, 1) == 100               # 极快基准同样被 100*last 压住
    print("[2] predictN:先乘后除 + 1.2x + 100*last + last+1 + 1e9 上限全部复现")

    # 3) 典型微基准(1 µs/op)的标定序列 —— 不是 1,2,5,10 而是 100 倍增长
    seq = launch_sequence(1_000, parse_benchtime("1s"))
    assert seq == [1, 100, 10_000, 1_000_000], seq
    assert sum(seq) > 1_000_000                                  # 预热性质的额外执行量被丢弃不计
    print(f"[3] 1µs/op 的 -benchtime=1s 标定序列 = {seq}(4 轮,非固定 1/2/5/10 序列)")

    # 4) 极快基准(1 ns/op):100*last 上限迫使更多轮
    fast = launch_sequence(1, parse_benchtime("1s"))
    assert fast[0] == 1 and fast[-1] == MAX_BENCH_PREDICT_ITERS, fast
    assert len(fast) == 6, fast
    print(f"[4] 1ns/op 需要 {len(fast)} 轮才收敛到 N=1e9(100 倍上限的直接后果)")

    # 5) Nx 形态:一次 runN 搞定,1x 时连 runN 都不调
    assert launch_sequence(1_000, parse_benchtime("1000000x")) == [1, 1_000_000]
    assert launch_sequence(1_000, parse_benchtime("1x")) == [1]
    print("[5] -benchtime=1000000x 只跑 1 次 runN;1x 复用 run1,样本量恒为 1")

    # 6) 样本量由 -count 决定:-count=1 时给不出任何分布信息
    assert median_ci_halfwidth([10.0]) is None
    assert median_ci_halfwidth([10.0, 12.0]) is not None
    print("[6] -count=1 -> 中位数置信区间不存在(benchstat 要求至少 10 次)")

    # 7) ns/op 与 B/op 的整数截断
    assert ns_per_op(1_501, 2) == 750            # 750.5 被截断,不是 751
    assert alloced_bytes_per_op(500_000, 1_000_000) == 0
    assert alloced_bytes_per_op(500_000, 1) == 500_000
    print("[7] ns/op=1501ns/2op -> 750(截断);500KB/1e6op -> 0 B/op(而非 0.5)")

    # 8) RunParallel 的 grain 钳制
    assert run_parallel_grain(1_000_000, 1_000_000_000) == 100      # 1µs/op -> 100 次约 100µs
    assert run_parallel_grain(100_000_000, 100_000_000) == 10_000   # 1ns/op -> 顶到 1e4
    assert run_parallel_grain(0, 0) == 1                            # 首轮无历史 -> 1
    print("[8] RunParallel grain:1µs/op=100、1ns/op 顶到 1e4、首轮=1")

    # 9) -benchtime 与 -count 的分工:前者改一次测量的精度,后者改样本量
    per_op = 1_000
    for bt in ("1s", "10s", "1000000x"):
        s = launch_sequence(per_op, parse_benchtime(bt))
        total = per_op * s[-1]
        print(f"      -benchtime={bt}: N={s[-1]}, 单次测量墙钟 {total/1e9:.3f}s, "
              f"ns/op={ns_per_op(total, s[-1])}")
    assert ns_per_op(per_op * 1_000_000, 1_000_000) == per_op


if __name__ == "__main__":
    _self_test()
    print("\ngobench_sample: 全部自检通过")
