#!/usr/bin/env python3
"""JMH ``-prof gc`` 与 Go ``-benchmem`` 的**分配口径**逐条对拍。

两侧口径全部来自本轮逐行实读的源码，不凭记忆：

JMH 侧 ``jmh-core/.../profile/GCProfiler.java``
  1. ``gc.count`` = 遍历**所有** ``GarbageCollectorMXBean`` 取
     ``Σ(getCollectionCount_after − getCollectionCount_before)``，``AggregationPolicy.SUM``；
  2. ``gc.time`` **有条件才发射**：只有 ``gcCount != beforeGCCount || gcTime != beforeGCTime``
     时才加进结果（多轮求和后"这一轮没 GC"那一轮就完全没有 ``gc.time`` 行）；
  3. ``gc.cpuTime`` = ``(getTotalGcCpuTime() − before) / 1_000_000``，**仅当**
     ``getTotalGcCpuTime() > -1``（该 JVM 支持）才进结果；
  4. ``gc.alloc.rate`` = ``allocated / 1024 / 1024 * 1e9 / (afterTime − beforeTime)``，``AVG``；
  5. ``gc.alloc.rate.norm`` = ``1.0 * allocated / allOps``（**浮点** B/op），**只在
     ``allocated != 0`` 时才发射** —— 零分配时这一列**根本不存在**，而不是显示 0；
  6. ``allocated`` 优先走 ``getTotalThreadAllocatedBytes``（进程级），返回 ``-1`` 视为不支持；
     回退 ``getThreadAllocatedBytes(long[])`` 时**跳过当前线程**，且**中途创建又死亡的线程
     的分配会被整段漏掉**；``difference`` 出现负数时**钳成 0**（注释 "Do not allow negative values"）；
  7. ``churn`` 默认 **false**，``churnWait`` 默认 500 ms；churn 取每个 memory space 的
     ``before.getUsed() − after.getUsed()``，**只在 c > 0 时累加**。

Go 侧 ``src/testing/benchmark.go``
  1. ``StartTimer`` 里 ``ReadMemStats`` → ``startAllocs = Mallocs``、``startBytes = TotalAlloc``；
     ``StopTimer`` 里 ``netAllocs += Mallocs − startAllocs``、``netBytes += TotalAlloc − startBytes``
     —— **计时器停着时做的分配完全不计**；
  2. ``ResetTimer`` 只清 ``netAllocs/netBytes/duration``，timerOn 时顺带重设 start；
  3. ``runN`` 的顺序是 ``runtime.GC() → ResetTimer → StartTimer → benchFunc → StopTimer``，
     所以**这轮开头的 GC 不进 ns/op**，但确实改变了 NumGC；
  4. ``AllocedBytesPerOp = int64(MemBytes) / int64(N)`` 与 ``AllocsPerOp = int64(MemAllocs) / int64(N)``
     都是**整数除法**；``Extra["B/op"]`` 可覆盖内建值；
  5. 输出模板 ``"%8d B/op\t%8d allocs/op"``。

无第三方依赖；自检完全确定性，不依赖真实计时。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

NAN = float("nan")


# --------------------------------------------------------------------------
# JMH 侧
# --------------------------------------------------------------------------
@dataclass
class JmhSnapshot:
    """GCProfiler 在 beforeIteration / afterIteration 各取一次的快照。"""

    counts: Dict[str, int] = field(default_factory=dict)      # per GC bean: getCollectionCount
    times_ms: Dict[str, int] = field(default_factory=dict)    # per GC bean: getCollectionTime
    gc_cpu_ns: Optional[int] = None                           # MemoryMXBean.getTotalGcCpuTime
    total_alloc: Optional[int] = None                         # com.sun ThreadMXBean 全局口径
    thread_alloc: Optional[Dict[int, int]] = None             # 回退路径：per-thread
    pool_used: Dict[str, int] = field(default_factory=dict)   # churn 用的各 space used


@dataclass
class ChurnEvent:
    """一次 GarbageCollectionNotificationInfo：每个 space 的 (before.used, after.used)。"""

    before: Dict[str, int]
    after: Dict[str, int]


@dataclass
class ScalarResult:
    name: str
    value: float
    unit: str
    policy: str          # SUM 或 AVG（决定多轮怎么聚合）

    def __str__(self) -> str:
        return f"{self.name} = {self.value} {self.unit} [{self.policy}]"


def _allocated_bytes(before: JmhSnapshot, after: JmhSnapshot, current_thread: Optional[int]) -> Tuple[Optional[int], bool]:
    """复刻 VMSupport.getSnapshot + difference。返回 (字节数, 是否可用)。"""
    if before.total_alloc is not None and after.total_alloc is not None:
        if after.total_alloc == -1 or before.total_alloc == -1:
            return None, False          # getTotalThreadAllocatedBytes is disabled
        diff = after.total_alloc - before.total_alloc
        # GlobalHotspotAllocationSnapshot.difference：不允许负数
        return (diff if diff >= 0 else 0), True

    if before.thread_alloc is None or after.thread_alloc is None:
        return None, False

    allocated = 0
    for tid, val in after.thread_alloc.items():
        if current_thread is not None and tid == current_thread:
            continue                    # 当前线程被刻意排除
        allocated += val - before.thread_alloc.get(tid, 0)
    return allocated, True


def jmh_gc_profile(
    before: JmhSnapshot,
    after: JmhSnapshot,
    dt_ns: int,
    all_ops: int,
    *,
    alloc_enabled: bool = True,
    churn_enabled: bool = False,
    churn_wait_ms: int = 500,
    gc_cpu_supported: Optional[bool] = None,
    gc_cpu_before: Optional[int] = None,
    events: Optional[List[ChurnEvent]] = None,
    current_thread: Optional[int] = None,
) -> List[ScalarResult]:
    """复刻 GCProfiler.afterIteration 的结果构造顺序。"""
    out: List[ScalarResult] = []

    names = set(before.counts) | set(after.counts)
    gc_count = sum(after.counts.get(b, 0) - before.counts.get(b, 0) for b in names)
    gc_time = sum(after.times_ms.get(b, 0) - before.times_ms.get(b, 0) for b in names)

    out.append(ScalarResult("gc.count", float(gc_count), "counts", "SUM"))
    if gc_count != 0 or gc_time != 0:                      # 条件发射
        out.append(ScalarResult("gc.time", float(gc_time), "ms", "SUM"))

    if gc_cpu_supported is None:
        gc_cpu_supported = gc_cpu_before is not None and gc_cpu_before > -1
    if gc_cpu_supported and gc_cpu_before is not None and after.gc_cpu_ns is not None:
        out.append(ScalarResult("gc.cpuTime", (after.gc_cpu_ns - gc_cpu_before) // 1_000_000, "ms", "SUM"))

    if alloc_enabled:
        allocated, ok = _allocated_bytes(before, after, current_thread)
        if not ok:
            out.append(ScalarResult("gc.alloc.rate", NAN, "MB/sec", "AVG"))
        else:
            rate = (allocated / 1024 / 1024 * 1e9 / dt_ns) if dt_ns != 0 else NAN
            out.append(ScalarResult("gc.alloc.rate", rate, "MB/sec", "AVG"))
            if allocated != 0:                             # 零分配 ⇒ 整列消失
                norm = (1.0 * allocated / all_ops) if all_ops != 0 else NAN
                out.append(ScalarResult("gc.alloc.rate.norm", norm, "B/op", "AVG"))

    if churn_enabled:
        churn: Dict[str, int] = {}
        for ev in events or []:
            for space, after_used in ev.after.items():
                c = ev.before.get(space, 0) - after_used
                if c > 0:                                  # 只累加净回收为正的
                    churn[space] = churn.get(space, 0) + c
        for space in sorted(churn):
            rate = (churn[space] * 1e9 / dt_ns / 1024 / 1024) if dt_ns != 0 else NAN
            norm = churn[space] / all_ops if all_ops != 0 else NAN
            out.append(ScalarResult(f"gc.churn.{space}", rate, "MB/sec", "AVG"))
            out.append(ScalarResult(f"gc.churn.{space}.norm", norm, "B/op", "AVG"))

    return out


def aggregate(results_per_iter: List[List[ScalarResult]]) -> Dict[str, float]:
    """按 AggregationPolicy 聚合多轮：SUM 求和、AVG 取算术平均。"""
    sums: Dict[str, float] = {}
    avg_s: Dict[str, float] = {}
    avg_n: Dict[str, int] = {}
    for it in results_per_iter:
        for r in it:
            if r.policy == "SUM":
                sums[r.name] = sums.get(r.name, 0.0) + r.value
            else:
                avg_s[r.name] = avg_s.get(r.name, 0.0) + r.value
                avg_n[r.name] = avg_n.get(r.name, 0) + 1
    out = dict(sums)
    for k, v in avg_s.items():
        out[k] = v / avg_n[k] if avg_n[k] else NAN
    return out


# --------------------------------------------------------------------------
# Go 侧
# --------------------------------------------------------------------------
@dataclass
class MemStats:
    mallocs: int = 0        # 累计分配次数
    frees: int = 0
    total_alloc: int = 0    # 累计分配字节，**释放也不减少**


class GoBench:
    """只建模 testing.B 与分配/计时相关的那几条语句。"""

    def __init__(self, stats: MemStats, n: int) -> None:
        self.mem = stats
        self.n = n
        self.timer_on = False
        self.start_allocs = 0
        self.start_bytes = 0
        self.net_allocs = 0
        self.net_bytes = 0
        self.duration = 0
        self.num_gc = 0

    def start_timer(self) -> None:
        if not self.timer_on:
            self.start_allocs = self.mem.mallocs
            self.start_bytes = self.mem.total_alloc
            self.timer_on = True

    def stop_timer(self) -> None:
        if self.timer_on:
            self.net_allocs += self.mem.mallocs - self.start_allocs
            self.net_bytes += self.mem.total_alloc - self.start_bytes
            self.timer_on = False

    def reset_timer(self) -> None:
        if self.timer_on:                      # 只重设起点，不清 net
            self.start_allocs = self.mem.mallocs
            self.start_bytes = self.mem.total_alloc
        self.duration = 0
        self.net_allocs = 0
        self.net_bytes = 0

    def run_n(self, ops: List[Tuple[int, int]], elapsed_ns: int) -> None:
        """ops = [(分配次数, 字节数)]; 复刻 GC → ResetTimer → Start → benchFunc → Stop。"""
        self.mem.frees += 0                    # 与本模型无关，占位说明 Frees 不参与净值
        self.num_gc += 1                       # runtime.GC()，在 ResetTimer 之前
        self.reset_timer()
        self.start_timer()
        self.duration = elapsed_ns
        for k, b in ops:
            self.mem.mallocs += k
            self.mem.total_alloc += b
        self.stop_timer()

    # ---- 三个派生指标，全部整数除法 ----
    def ns_per_op(self, extra: Optional[Dict[str, float]] = None) -> int:
        if extra and "ns/op" in extra:
            return int(extra["ns/op"])
        return 0 if self.n <= 0 else self.duration // self.n

    def allocs_per_op(self, extra: Optional[Dict[str, float]] = None) -> int:
        if extra and "allocs/op" in extra:
            return int(extra["allocs/op"])
        return 0 if self.n <= 0 else self.net_allocs // self.n

    def alloced_bytes_per_op(self, extra: Optional[Dict[str, float]] = None) -> float:
        if extra and "B/op" in extra:
            return extra["B/op"]
        return 0 if self.n <= 0 else self.net_bytes // self.n

    def mem_string(self, extra: Optional[Dict[str, float]] = None) -> str:
        return "%8d B/op\t%8d allocs/op" % (self.alloced_bytes_per_op(extra), self.allocs_per_op(extra))


def go_benchmem_line(bench: GoBench, extra: Optional[Dict[str, float]] = None) -> str:
    return "%8d ns/op\t%s" % (bench.ns_per_op(extra), bench.mem_string(extra))


# --------------------------------------------------------------------------
# 对拍：把同一份"真实发生的事"分别喂给两个报告器
# --------------------------------------------------------------------------
def compare_true_vs_reported(
    bytes_per_op_true: float,
    n: int,
    *,
    gc_infra_bytes: int = 0,
    gc_infra_threads: Optional[Dict[int, int]] = None,
) -> Dict[str, object]:
    """同一份真实分配量下，两个报告器各自给出的 B/op。"""
    total_bytes = int(round(bytes_per_op_true * n)) + gc_infra_bytes

    # JMH：进程级口径，因此 JMH 基础设施的分配被算进去了
    before = JmhSnapshot(total_alloc=0)
    after = JmhSnapshot(total_alloc=total_bytes)
    jmh = jmh_gc_profile(before, after, dt_ns=n * 10, all_ops=n)
    jmh_norm = next((r.value for r in jmh if r.name == "gc.alloc.rate.norm"), None)

    # Go：只统计 StartTimer/StopTimer 窗口内的、净字节（不含基础设施）
    mem = MemStats()
    bench = GoBench(mem, n)
    bench.run_n([(n, int(round(bytes_per_op_true * n)))], elapsed_ns=n * 10)
    go_bop = bench.alloced_bytes_per_op()

    return {"jmh_B_per_op": jmh_norm, "go_B_per_op": go_bop, "true": bytes_per_op_true}


if __name__ == "__main__":
    r = compare_true_vs_reported(0.5, 1000)
    print("真实 0.5 B/op:", r)
    r = compare_true_vs_reported(24.0, 1000)
    print("真实 24  B/op:", r)
    b = GoBench(MemStats(), 1000)
    b.run_n([(1000, 24000)], 1000000)
    print("go:", go_benchmem_line(b))
    print("churn-only:", [str(x) for x in jmh_gc_profile(
        JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=0), 1000, 1,
        churn_enabled=True,
        events=[ChurnEvent(before={"PS Eden Space": 100}, after={"PS Eden Space": 20})])])
