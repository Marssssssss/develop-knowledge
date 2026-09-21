#!/usr/bin/env python3
"""虚拟化与容器环境（cgroup CPU quota、steal time）对 ``-benchtime`` 标定的影响。

口径全部来自逐行实读的源码：

``golang/go`` ``src/internal/runtime/cgroup/cgroup.go``
  1. v2 的 ``cpu.max`` 是 ``"<quota> <period>"`` 两字段，**quota 可以是字面量 ``max``**（无限制）；
     解析结果是 ``float64(quota) / float64(period)``，失败返回 ``errMalformedFile``；
  2. v1 走 ``parseV1Number``：先按 ``\n`` 截断再 ``ParseInt``，没有换行直接算 malformed；
  3. ``parseCPUCgroup`` 里 **v1 的 CPU controller 优先于 v2**：见注释 "cgroup v2 has hierarchy-ID 0.
     If a v1 hierarchy contains cpu, that is the CPU controller. Otherwise the v2 hierarchy
     (if any) is the CPU controller." 且命中 v1 立刻 return。

``golang/go`` ``src/testing/benchmark.go``
  4. ``predictN`` 的**顺序很重要**：先乘后除（注释写明"divide first ... can hide an order of
     magnitude"），再 ``n += n/5``（1.2×），再 ``min(n, 100*last)``、``max(n, last+1)``、
     ``min(n, 1e9)``；``prevns == 0`` 时**上取整成 1** 躲除零（go.dev/issue/70709）；
  5. 标定循环是 ``for n := 1; !b.failed && b.duration < d && n < 1e9``，
     每次 ``last = n`` 后 ``n = predictN(goalns, b.N, b.duration, last)`` 再 ``runN(n)``；
     ``runN`` 内部 ``ResetTimer`` 把 duration 归零，所以循环判据用的是**上一轮**的墙钟。

``golang/go`` ``src/runtime/proc.go``
  6. ``sysmonUpdateGOMAXPROCS`` 里先读 ``sched.customGOMAXPROCS``，**手动设过 GOMAXPROCS 就不再
     自动跟随 cgroup 变化**（直接 return），否则每轮都调 ``defaultGOMAXPROCS(0)`` 重新求值。

Linux ``Documentation/filesystems/proc.rst``
  7. ``/proc/stat`` 的 cpu 行里 ``steal: involuntary wait`` —— 是**自开机累计**的量，
     必须取两次差值；它反映的是宿主把 CPU 给了别的 guest，**与 cgroup 配额用尽不是一回事**，
     两者会叠加。

关于 CFS 带宽（cfs bandwidth）的模型：一个 period 长 P、配额 Q 的 cgroup，在每个 period 内最多
跑 Q 的 CPU 时间，配额耗尽后被 throttle 到下一个 period。于是跑完 R 的 CPU 工作需要的墙钟是
``(k-1)*P + (R - (k-1)*Q)``，其中 ``k = ceil(R/Q)``。**前 Q 的 CPU 时间是"免费"的**，所以短任务
几乎不受影响，长任务才被拉到 P/Q —— 限流带来的偏差随 benchtime 增大而增大。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

MAX_BENCH_PREDICT_ITERS = 1_000_000_000      # maxBenchPredictIters


class MalformedFile(Exception):
    """对应 cgroup.go 的 errMalformedFile。"""


# --------------------------------------------------------------------------
# cgroup 解析
# --------------------------------------------------------------------------
def parse_v1_number(buf: str) -> int:
    """复刻 parseV1Number：先按 \n 截断，再 ParseInt。"""
    i = buf.find("\n")
    if i < 0:
        raise MalformedFile("v1: no trailing newline")
    return int(buf[:i])


def parse_v2_limit(buf: str) -> Tuple[float, bool]:
    """复刻 parseV2Limit：返回 (quota/period, 是否有限制)。"""
    i = buf.find(" ")
    if i < 0:
        raise MalformedFile("v2: no space separator")
    quota_str = buf[:i]
    if quota_str == "max":
        return 0.0, False                        # No limit.
    period_str = buf[i + 1:]
    i = period_str.find("\n")
    if i < 0:
        raise MalformedFile("v2: period without newline")
    period_str = period_str[:i]
    return int(quota_str) / int(period_str), True


V1, V2 = 1, 2


def parse_cpu_cgroup(lines: List[str]) -> Tuple[str, int]:
    """复刻 parseCPUCgroup：v1 的 CPU controller 优先；hierarchy-ID 为 0 表示 v2。"""
    out = ""
    for line in lines:
        i = line.find(":")
        if i < 0:
            raise MalformedFile("no hierarchy separator")
        hierarchy, rest = line[:i], line[i + 1:]
        i = rest.find(":")
        if i < 0:
            raise MalformedFile("no controller separator")
        controllers, path = rest[:i], rest[i + 1:]
        if not path.startswith("/"):
            raise MalformedFile("path must start with /")
        if hierarchy == "0":
            out = path                            # 继续找，v1 可能覆盖它
        elif contains_cpu(controllers):
            return path, V1
    if not out:
        raise MalformedFile("no cgroup found")
    return out, V2


def contains_cpu(controllers: str) -> bool:
    return "cpu" in [c for c in controllers.split(",")]


# --------------------------------------------------------------------------
# CFS 带宽与 steal
# --------------------------------------------------------------------------
@dataclass
class CfsBandwidth:
    """period_ns 与 quota_ns 的默认值因发行版/编排器而异，一律作为参数注入。"""

    quota_ns: int
    period_ns: int

    def wall_ns(self, cpu_ns: int) -> int:
        """跑完 cpu_ns 的 CPU 工作所需的墙钟（含被 throttle 的等待）。"""
        if self.quota_ns <= 0:
            return cpu_ns                         # 无限制
        if cpu_ns <= 0:
            return 0
        k = math.ceil(cpu_ns / self.quota_ns)
        return (k - 1) * self.period_ns + (cpu_ns - (k - 1) * self.quota_ns)

    def ratio(self, cpu_ns: int) -> float:
        """实际吞吐 = cpu_ns / wall_ns。"""
        w = self.wall_ns(cpu_ns)
        return 1.0 if w == 0 else cpu_ns / w


@dataclass
class StealTime:
    """/proc/stat 的 steal：宿主把 CPU 给了别的 guest，表现为墙钟被乘性拉长。"""

    fraction: float                                # 0.0 ~ 1.0

    def wall_ns(self, cpu_ns: int) -> int:
        if self.fraction >= 1.0:
            raise ValueError("steal fraction must be < 1")
        return int(round(cpu_ns / (1.0 - self.fraction)))


class Host:
    """把 cfs 限流与 steal 叠加起来。"""

    def __init__(self, cfs: Optional[CfsBandwidth] = None,
                 steal: Optional[StealTime] = None) -> None:
        self.cfs = cfs or CfsBandwidth(0, 1)       # quota<=0 视为不限
        self.steal = steal

    def wall_ns(self, cpu_ns: int) -> int:
        w = self.cfs.wall_ns(cpu_ns)
        if self.steal is not None:
            # steal 作用在**已经包含 throttle 等待**的墙钟上（CPU 变慢 ⇒ 墙钟按比例拉长）
            w = int(round(w / (1.0 - self.steal.fraction)))
        return w


# --------------------------------------------------------------------------
# -benchtime 标定
# --------------------------------------------------------------------------
def predict_n(goalns: int, prev_iters: int, prevns: int, last: int) -> int:
    """复刻 predictN，四步钳制一步不少。"""
    if prevns == 0:
        prevns = 1                                # Round up to dodge divide by zero.
    n = goalns * prev_iters // prevns             # 先乘后除
    n += n // 5                                   # 1.2x
    n = min(n, 100 * last)                        # 不要增长太快
    n = max(n, last + 1)                          # 至少比上次多 1
    n = min(n, MAX_BENCH_PREDICT_ITERS)           # 上限 1e9
    return int(n)


@dataclass
class Calibration:
    n: int
    duration_ns: int
    rounds: int
    history: List[Tuple[int, int]]

    @property
    def ns_per_op(self) -> int:
        return 0 if self.n <= 0 else self.duration_ns // self.n


def launch(benchtime_ns: int, ns_per_op_cpu: int, host: Host) -> Calibration:
    """复刻 launch 的标定循环：run1 之后反复 predictN + runN。"""
    n = 1
    duration = host.wall_ns(n * ns_per_op_cpu)     # run1
    history = [(n, duration)]
    rounds = 1
    while duration < benchtime_ns and n < MAX_BENCH_PREDICT_ITERS:
        last = n
        n = predict_n(benchtime_ns, n, duration, last)
        duration = host.wall_ns(n * ns_per_op_cpu)
        history.append((n, duration))
        rounds += 1
        if rounds > 200:                           # 防御：模型里不该发生
            break
    return Calibration(n, duration, rounds, history)


class GomaxprocsController:
    """复刻 sysmonUpdateGOMAXPROCS 的两道闸门。

    源码顺序：先读 ``sched.customGOMAXPROCS``，手动设过就**直接 return**（不再跟随 cgroup）；
    否则 ``procs := defaultGOMAXPROCS(0)``，与当前值相同也直接 return。
    """

    def __init__(self, initial: int) -> None:
        self.value = initial
        self.custom = False

    def set_custom(self, v: int) -> None:
        self.value = v
        self.custom = True

    def sysmon_update(self, default_gomaxprocs) -> bool:
        """返回是否真的改了值。"""
        if self.custom:
            return False
        procs = default_gomaxprocs()
        if procs == self.value:
            return False
        self.value = procs
        return True


if __name__ == "__main__":
    bare = Host()
    limited = Host(cfs=CfsBandwidth(quota_ns=50_000_000, period_ns=100_000_000))
    for name, host in (("裸机", bare), ("cgroup 50%", limited),
                       ("cgroup 50% + steal 10%", Host(
                           cfs=CfsBandwidth(50_000_000, 100_000_000), steal=StealTime(0.10)))):
        c = launch(1_000_000_000, 1000, host)
        print("%-22s N=%-10d 墙钟=%.3fs ns/op=%d  轮数=%d"
              % (name, c.n, c.duration_ns / 1e9, c.ns_per_op, c.rounds))
    print()
    print("限流下的吞吐随 benchtime 变化（Q=50ms / P=100ms）：")
    for ms in (1, 10, 50, 100, 250, 1000):
        w = limited.wall_ns(ms * 1_000_000)
        print("  CPU %4d ms → 墙钟 %7.1f ms (吞吐 %.3f)" % (ms, w / 1e6, ms * 1e6 / w))
