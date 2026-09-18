#!/usr/bin/env python3
"""-benchmem 的 B/op 与 allocs/op:净值语义、整数截断,以及 GC 怎么混进 ns/op。

全部口径来自逐行实读的 ``golang/go`` ``src/testing/benchmark.go`` 与 ``pkg.go.dev/runtime``
的 ``MemStats`` 字段文档,不凭记忆:

1. **B/op 与 allocs/op 是"净值 ÷ N",而且 Net 是**差值**不是绝对值** —— ``StartTimer`` 里
   ``runtime.ReadMemStats(&memStats); startAllocs = memStats.Mallocs; startBytes = memStats.TotalAlloc``;
   ``StopTimer`` 里 ``netAllocs += memStats.Mallocs - startAllocs``、
   ``netBytes += memStats.TotalAlloc - startBytes``。所以**计时器停着的时候做的分配根本不算**。

2. **``TotalAlloc`` 是累计量、释放也不减少** —— 官方文档原文:"TotalAlloc is cumulative bytes
   allocated for heap objects. ... unlike Alloc and HeapAlloc, it **does not decrease** when
   objects are freed." ``Mallocs`` 同理,存活对象数 = ``Mallocs - Frees``。这正是必须取
   两次采样之差的原因。

3. **整数除法,直接截断** —— ``AllocedBytesPerOp = int64(r.MemBytes) / int64(r.N)``、
   ``AllocsPerOp = int64(r.MemAllocs) / int64(r.N)``。于是"每次操作分配不到 1 字节"会被
   报成 **0 B/op**,"每两次操作分配一次"会被报成 **0 allocs/op** —— **0 不等于零分配**。

4. **每一轮 ``runN`` 前都 ``runtime.GC()``** —— 源码注释 "Try to get a comparable environment
   for each run by clearing garbage from previous runs",发生在 ``ResetTimer()`` 之前,
   所以 GC 时间不计入 ns/op,但 ``NumGC`` 会随 ``-count`` 增长。

5. **GC 暂停是墙钟的一部分** —— ns/op 用的是墙钟,本轮内发生的 STW 全部摊在里面。

6. **``GCCPUFraction`` 的分母是"可用 CPU"而不是墙钟** —— 官方文档:"A program's available CPU
   time is defined as the integral of GOMAXPROCS since the program started. That is, if
   GOMAXPROCS is 2 and a program has been running for 10 seconds, its 'available CPU' is
   20 seconds." 而且它 **does not include CPU time used for write barrier activity** —— 会低估。

无第三方依赖;自检全部确定性(不依赖真实计时)。
"""

from __future__ import annotations

from typing import Dict, Optional

MIN_NEXT_GC = 4 << 20          # Go 的最小目标堆(默认 4MB),保证 next_gc 不会退化到 0


class MemStats:
    """``runtime.MemStats`` 里与本 demo 相关的字段。"""

    def __init__(self) -> None:
        self.total_alloc = 0        # 累计分配字节,释放也不减少
        self.mallocs = 0            # 累计分配对象数
        self.frees = 0              # 累计释放对象数
        self.heap_alloc = 0         # 当前堆上对象(含未清扫的)
        self.pause_total_ns = 0     # 累计 STW 纳秒
        self.num_gc = 0
        self.next_gc = MIN_NEXT_GC

    @property
    def heap_objects(self) -> int:
        return self.mallocs - self.frees


class GC:
    """简化的 GC:堆达到 next_gc 就触发一轮 STW,之后按 GOGC 重算目标堆。"""

    def __init__(self, stats: MemStats, gogc: int = 100, pause_ns: int = 200_000):
        self.m = stats
        self.gogc = gogc
        self.pause_ns = pause_ns
        self.gc_cpu_ns = 0

    def allocate(self, size: int) -> int:
        """分配 size 字节;返回本次触发的 STW 纳秒(未触发则 0)。"""
        self.m.total_alloc += size
        self.m.mallocs += 1
        self.m.heap_alloc += size
        if self.m.heap_alloc >= self.m.next_gc:
            return self.collect()
        return 0

    def free(self, size: int) -> None:
        self.m.frees += 1
        self.m.heap_alloc = max(0, self.m.heap_alloc - size)

    def collect(self, forced_live: int = -1) -> int:
        """一轮 GC。``forced_live < 0`` 时存活量取当前 ``heap_alloc``
        (死对象已被 free 掉,所以这就是存活集);``runtime.GC()`` 走 ``forced_live=0``。"""
        live = self.m.heap_alloc if forced_live < 0 else forced_live
        self.m.heap_alloc = live
        self.m.num_gc += 1
        self.m.pause_total_ns += self.pause_ns
        self.gc_cpu_ns += self.pause_ns
        target = live * (100 + self.gogc) // 100
        self.m.next_gc = max(MIN_NEXT_GC, target)
        return self.pause_ns


class Timer:
    """``B`` 的计时/计数状态机:StartTimer / StopTimer / ResetTimer 的逐行复刻。"""

    def __init__(self, stats: MemStats):
        self.m = stats
        self.timer_on = False
        self.duration = 0
        self.net_allocs = 0
        self.net_bytes = 0
        self.start_allocs = 0
        self.start_bytes = 0

    def start(self) -> None:
        if not self.timer_on:
            self.start_allocs = self.m.mallocs
            self.start_bytes = self.m.total_alloc
            self.timer_on = True

    def stop(self) -> None:
        if self.timer_on:
            self.net_allocs += self.m.mallocs - self.start_allocs
            self.net_bytes += self.m.total_alloc - self.start_bytes
            self.timer_on = False

    def reset(self) -> None:
        """注意:只在**计时器开着**时才重采基线,但净值一律清零。"""
        if self.timer_on:
            self.start_allocs = self.m.mallocs
            self.start_bytes = self.m.total_alloc
        self.duration = 0
        self.net_allocs = 0
        self.net_bytes = 0


class BenchmarkResult:
    """``testing.BenchmarkResult`` 的三个取值方法。"""

    def __init__(self, n: int, t_ns: int, mem_allocs: int, mem_bytes: int,
                 extra: Optional[Dict[str, float]] = None):
        self.n = n
        self.t = t_ns
        self.mem_allocs = mem_allocs
        self.mem_bytes = mem_bytes
        self.extra = extra or {}

    def ns_per_op(self) -> int:
        if "ns/op" in self.extra:
            return int(self.extra["ns/op"])
        if self.n <= 0:
            return 0
        return self.t // self.n

    def allocs_per_op(self) -> int:
        if "allocs/op" in self.extra:
            return int(self.extra["allocs/op"])
        if self.n <= 0:
            return 0
        return self.mem_allocs // self.n

    def alloced_bytes_per_op(self) -> int:
        if "B/op" in self.extra:
            return int(self.extra["B/op"])
        if self.n <= 0:
            return 0
        return self.mem_bytes // self.n


def gccpu_fraction(gc_cpu_ns: int, gomaxprocs_integral_ns: int) -> float:
    """``GCCPUFraction``:分子是 GC 占用的 CPU 时间,分母是 GOMAXPROCS 的积分(可用 CPU)。"""
    if gomaxprocs_integral_ns <= 0:
        return 0.0
    return gc_cpu_ns / gomaxprocs_integral_ns


def run_n(timer: Timer, gc: GC, n: int, op_ns: int, alloc_per_op: int,
          live_fraction: float) -> BenchmarkResult:
    """复刻 ``runN``:先 ``runtime.GC()``,再 Reset/Start/循环/Stop。"""
    gc.collect(0)                       # runtime.GC():清空上一轮垃圾,不计入计时
    timer.reset()
    timer.start()
    dead = alloc_per_op - int(alloc_per_op * live_fraction)
    for _ in range(n):
        timer.duration += op_ns
        if alloc_per_op:
            timer.duration += gc.allocate(alloc_per_op)
            if dead:
                gc.free(dead)
    timer.stop()
    return BenchmarkResult(n, timer.duration, timer.net_allocs, timer.net_bytes)


def measured_allocation_boundary(old: BenchmarkResult, new: BenchmarkResult) -> bool:
    """「把分配当逻辑测」判别:B/op 的变化幅度远大于 ns/op 的变化幅度。"""
    if old.alloced_bytes_per_op() == 0 or old.ns_per_op() == 0:
        return False
    ratio_b = new.alloced_bytes_per_op() / old.alloced_bytes_per_op()
    ratio_t = new.ns_per_op() / old.ns_per_op()
    return ratio_b >= 3.0 and ratio_t < 1.5


# --------------------------------------------------------------------------- 自检

def _self_test() -> None:
    # 1) 净值是差值,且计时器停着时的分配不算
    m = MemStats()
    t = Timer(m)
    t.start()
    m.total_alloc += 1000
    m.mallocs += 10
    t.stop()
    m.total_alloc += 5                 # 计时器已停:这部分必须被忽略
    m.mallocs += 1
    t.start()
    m.total_alloc += 2000
    m.mallocs += 20
    t.stop()
    assert t.net_bytes == 3000, t.net_bytes
    assert t.net_allocs == 30, t.net_allocs
    print(f"[1] 净值语义:1000 + (停表期间 5) + 2000 -> net={t.net_bytes}(不是 3005)")

    # 2) TotalAlloc 单调不减;存活对象 = Mallocs - Frees
    m2 = MemStats()
    g2 = GC(m2)
    m2.total_alloc += 500
    m2.mallocs += 5
    g2.free(0)
    g2.free(0)
    assert m2.total_alloc == 500                 # 释放不减少 TotalAlloc
    assert m2.heap_objects == 3                  # 5 - 2
    print(f"[2] TotalAlloc 释放后仍是 {m2.total_alloc};存活对象 = Mallocs-Frees = {m2.heap_objects}")

    # 3) 整数截断:0 B/op 不等于零分配
    r = BenchmarkResult(1_000_000, 2_000_000_000, mem_allocs=500_000, mem_bytes=500_000)
    assert r.mem_bytes > 0 and r.alloced_bytes_per_op() == 0
    assert r.mem_allocs > 0 and r.allocs_per_op() == 0
    assert BenchmarkResult(1, 0, 1, 500_000).alloced_bytes_per_op() == 500_000
    print("[3] 整数截断:1e6 op 里分配 500KB -> B/op = 0、allocs/op = 0(但确实分配了)")

    # 4) 同一个 benchmark,N 越大越容易被截断成 0
    total = 500_000
    bops = [BenchmarkResult(n, 0, 0, total).alloced_bytes_per_op() for n in (1, 100, 10_000, 1_000_000)]
    assert bops == [500_000, 5_000, 50, 0], bops
    print(f"[4] 总分配 500KB 固定时 B/op 随 N 下降:{bops} —— -benchtime 越大越容易看到 0")

    # 5) GC 暂停摊进 ns/op,使小 N 低估含分配的 benchmark
    s5 = MemStats()
    g5 = GC(s5, gogc=100, pause_ns=2_000_000)
    gc_seen = []
    small = run_n(Timer(s5), g5, 1_000, 100, 1024, 0.05)
    gc_seen.append(s5.num_gc)
    big = run_n(Timer(s5), g5, 100_000, 100, 1024, 0.05)
    gc_seen.append(s5.num_gc - gc_seen[0])
    huge = run_n(Timer(s5), g5, 1_000_000, 100, 1024, 0.05)
    gc_seen.append(s5.num_gc - gc_seen[0] - gc_seen[1])
    assert gc_seen[0] <= 1 and gc_seen[1] >= 1, gc_seen
    assert small.ns_per_op() < big.ns_per_op(), (small.ns_per_op(), big.ns_per_op())
    # 非单调:N 继续增大后 next_gc 翻倍、GC 次数只按对数增长,摊到每 op 上反而回落
    assert small.ns_per_op() < huge.ns_per_op() < big.ns_per_op(), (
        small.ns_per_op(), big.ns_per_op(), huge.ns_per_op())
    print(f"[5] GC 摊入 ns/op:N=1e3 -> {small.ns_per_op()} ns/op({gc_seen[0]} 次 GC 含开场),"
          f"N=1e5 -> {big.ns_per_op()}(+{gc_seen[1]}),N=1e6 -> {huge.ns_per_op()}(+{gc_seen[2]})"
          f" —— 非单调,换 -benchtime 会得到不同的 ns/op")

    # 6) 每轮 runN 前都 GC:-count 越大 NumGC 越多,但都不计入计时
    s6 = MemStats()
    g6 = GC(s6, pause_ns=1000)
    before = g6.m.num_gc
    for _ in range(10):
        run_n(Timer(s6), g6, 10, 100, 0, 0.0)
    assert g6.m.num_gc - before >= 10
    print(f"[6] -count=10:每轮 runN 前 runtime.GC() -> NumGC 至少 +{g6.m.num_gc - before}"
          f"(GC 时间不计入 ns/op)")

    # 7) GCCPUFraction 的分母是 GOMAXPROCS 的积分,文档原例
    frac = gccpu_fraction(1_000_000_000, 20_000_000_000)   # GOMAXPROCS=2 跑 10s -> 20s
    assert abs(frac - 0.05) < 1e-12, frac
    print("[7] GCCPUFraction:GOMAXPROCS=2 跑 10s -> 可用 CPU=20s,GC 用 1s -> 0.05"
          f"(且不含写屏障,故低估)")

    # 8) ReportMetric 会覆盖内建列
    r8 = BenchmarkResult(100, 1_000_000, mem_allocs=100, mem_bytes=100_000,
                         extra={"B/op": 7.0, "allocs/op": 3.0})
    assert r8.alloced_bytes_per_op() == 7 and r8.allocs_per_op() == 3
    print("[8] Extra 优先:ReportMetric 写入的 B/op=7 / allocs/op=3 覆盖了内建计算值")

    # 9) 「把分配当逻辑测」判别
    old = BenchmarkResult(1000, 1_000_000, mem_allocs=1000, mem_bytes=8_000_000)
    new = BenchmarkResult(1000, 1_100_000, mem_allocs=1000, mem_bytes=80_000_000)
    assert measured_allocation_boundary(old, new)
    steady = BenchmarkResult(1000, 1_050_000, mem_allocs=1000, mem_bytes=8_400_000)
    assert not measured_allocation_boundary(old, steady)
    print("[9] 分配判别:B/op ×10 而 ns/op 只 ×1.1 -> 判为「测的是分配」;"
          f"B/op ×1.05、ns/op ×1.05 -> 不判")


if __name__ == "__main__":
    _self_test()
    print("\nallocstats: 全部自检通过")
