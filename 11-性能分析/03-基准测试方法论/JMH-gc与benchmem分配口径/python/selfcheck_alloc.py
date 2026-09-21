#!/usr/bin/env python3
"""JMH -prof gc 与 Go -benchmem 分配口径对拍的自检（确定性，不依赖真实计时）。

运行：python selfcheck_alloc.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    ChurnEvent,
    GoBench,
    JmhSnapshot,
    aggregate,
    compare_true_vs_reported,
    go_benchmem_line,
    jmh_gc_profile,
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


def names(res) -> list:
    return [r.name for r in res]


def get(res, name):
    for r in res:
        if r.name == name:
            return r
    return None


print("E1 零分配时 JMH 的 norm 列消失，Go 仍打印 0 B/op")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=0), 10**9, 1000)
ok("gc.alloc.rate.norm" not in names(r), "E1a JMH 无 norm 列", str(names(r)))
ok(get(r, "gc.alloc.rate").value == 0.0, "E1b JMH rate 仍为 0.0 MB/sec")
b = GoBench(__import__("main").MemStats(), 1000)
b.run_n([], 1000)
ok(b.mem_string() == "       0 B/op\t       0 allocs/op", "E1c Go 打印 0 B/op", repr(b.mem_string()))

print("E2 亚字节分配：JMH 给 0.5，Go 截断成 0")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=500), 10**9, 1000)
ok(abs(get(r, "gc.alloc.rate.norm").value - 0.5) < 1e-12, "E2a JMH 0.5 B/op")
b = GoBench(__import__("main").MemStats(), 1000)
b.run_n([(1000, 500)], 1000)
ok(b.alloced_bytes_per_op() == 0, "E2b Go 0 B/op（真值 0.5）", "截断")

print("E3 gc.time 只在次数或时间变化时才发射")
r = jmh_gc_profile(JmhSnapshot(counts={"G1": 5}, times_ms={"G1": 100}),
                   JmhSnapshot(counts={"G1": 5}, times_ms={"G1": 100}), 10**9, 1)
ok("gc.time" not in names(r), "E3a 都没变 ⇒ 无 gc.time")
r = jmh_gc_profile(JmhSnapshot(counts={"G1": 5}, times_ms={"G1": 100}),
                   JmhSnapshot(counts={"G1": 5}, times_ms={"G1": 130}), 10**9, 1)
ok(get(r, "gc.time").value == 30.0, "E3b 时间变了次数没变 ⇒ 有 gc.time=30ms")

print("E4 gc.count 是所有 GC bean 的求和（young + old）")
r = jmh_gc_profile(JmhSnapshot(counts={"G1 Young": 10, "G1 Old": 2}),
                   JmhSnapshot(counts={"G1 Young": 12, "G1 Old": 3}), 10**9, 1)
ok(get(r, "gc.count").value == 3.0, "E4a 2 + 1 = 3")

print("E5 gc.cpuTime 仅在 JVM 支持时出现，且整除 1e6 截断")
r = jmh_gc_profile(JmhSnapshot(), JmhSnapshot(), 10**9, 1, gc_cpu_supported=False)
ok("gc.cpuTime" not in names(r), "E5a 不支持 ⇒ 无该行")
r = jmh_gc_profile(JmhSnapshot(gc_cpu_ns=1_000_000), JmhSnapshot(gc_cpu_ns=3_499_999),
                   10**9, 1, gc_cpu_before=1_000_000)
ok(get(r, "gc.cpuTime").value == 2.0, "E5b 2499999ns → 2ms（截断非四舍五入）",
   str(get(r, "gc.cpuTime").value))

print("E6 gc.count 是 SUM 而 alloc.rate 是 AVG —— 两者不能互相除")
it1 = jmh_gc_profile(JmhSnapshot(counts={"G": 0}, total_alloc=0),
                     JmhSnapshot(counts={"G": 1}, total_alloc=1000), 10**7, 100)
it2 = jmh_gc_profile(JmhSnapshot(counts={"G": 0}, total_alloc=0),
                     JmhSnapshot(counts={"G": 1}, total_alloc=3000), 3 * 10**7, 1000)
agg = aggregate([it1, it2])
ok(agg["gc.count"] == 2.0, "E6a count 求和 = 2", str(agg["gc.count"]))
ok(abs(agg["gc.alloc.rate.norm"] - 6.5) < 1e-9, "E6b norm 按 AVG = (10+3)/2 = 6.5",
   str(agg["gc.alloc.rate.norm"]))
true_weighted = 4000 / 1100
ok(abs(agg["gc.alloc.rate.norm"] - true_weighted) > 1.0,
   "E6c 而「总字节/总操作」是 3.636 —— AVG 的 norm 不等于加权真值", f"真值 {true_weighted:.4f}")

print("E7 负差钳成 0（不允许负数）")
r = jmh_gc_profile(JmhSnapshot(total_alloc=1000), JmhSnapshot(total_alloc=800), 10**9, 100)
ok(get(r, "gc.alloc.rate").value == 0.0, "E7a 差为 -200 ⇒ 0", str(get(r, "gc.alloc.rate").value))

print("E8 getTotalThreadAllocatedBytes 返回 -1 ⇒ rate 为 NaN 且无 norm")
r = jmh_gc_profile(JmhSnapshot(total_alloc=-1), JmhSnapshot(total_alloc=-1), 10**9, 100)
ok(math.isnan(get(r, "gc.alloc.rate").value), "E8a rate = NaN")
ok("gc.alloc.rate.norm" not in names(r), "E8b 且没有 norm 列")

print("E9 per-thread 回退刻意跳过当前线程")
r = jmh_gc_profile(JmhSnapshot(thread_alloc={1: 100, 2: 50}),
                   JmhSnapshot(thread_alloc={1: 300, 2: 150}), 10**9, 100,
                   current_thread=1)
ok(abs(get(r, "gc.alloc.rate.norm").value - 1.0) < 1e-12,
   "E9a 只算线程 2 的 100 字节 / 100 ops = 1.0 B/op", str(get(r, "gc.alloc.rate.norm").value))

print("E10 per-thread 回退漏掉两次快照之间创建又死亡的线程")
r = jmh_gc_profile(JmhSnapshot(thread_alloc={2: 50}),
                   JmhSnapshot(thread_alloc={2: 150}), 10**9, 100, current_thread=None)
ok(abs(get(r, "gc.alloc.rate.norm").value - 1.0) < 1e-12,
   "E10a 短命线程的 500 字节完全没进统计", str(get(r, "gc.alloc.rate.norm").value))

print("E11 churn 默认关闭；开启后只累加 c > 0 的空间")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=0), 10**9, 100,
                   churn_enabled=False,
                   events=[ChurnEvent(before={"Eden": 100}, after={"Eden": 20})])
ok(not any(n.startswith("gc.churn") for n in names(r)), "E11a 默认不产生 churn 行")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=0), 10**9, 100,
                   churn_enabled=True,
                   events=[ChurnEvent(before={"Eden": 100}, after={"Eden": 120})])
ok(not any(n.startswith("gc.churn") for n in names(r)), "E11b before−after = −20 ⇒ 不累加")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=0), 10**9, 100,
                   churn_enabled=True,
                   events=[ChurnEvent(before={"Eden": 100}, after={"Eden": 20})])
ok(abs(get(r, "gc.churn.Eden.norm").value - 0.8) < 1e-12, "E11c 80 字节 / 100 ops = 0.8 B/op")

print("E12 窗口语义：Go 的 StopTimer 之后分配不计数")
b = GoBench(__import__("main").MemStats(), 100)
b.run_n([(100, 2400)], 1000)
before = b.net_bytes
b.mem.mallocs += 10
b.mem.total_alloc += 240
ok(b.net_bytes == before == 2400, "E12a 停表后 240 字节不计入", str(b.net_bytes))

print("E13 runN 开头的 runtime.GC() 计入 NumGC 但不进 duration")
b = GoBench(__import__("main").MemStats(), 100)
b.run_n([(100, 2400)], 1000)
ok(b.num_gc == 1, "E13a 每次 runN 强制 GC 一次")
b.run_n([(100, 2400)], 1000)
ok(b.num_gc == 2, "E13b 第二次 runN 再 GC 一次（随 -count 增长）")

print("E14 ResetTimer 只清净值，不清已发生的分配")
b = GoBench(__import__("main").MemStats(), 100)
b.run_n([(100, 2400)], 1000)
ok(b.net_bytes == 2400, "E14a 净值 2400")
b.reset_timer()
ok(b.net_bytes == 0 and b.mem.total_alloc == 2400, "E14b ResetTimer 后净值归零，全局计数不动")

print("E15 整数截断：1999 字节 / 1000 次")
b = GoBench(__import__("main").MemStats(), 1000)
b.run_n([(1000, 1999)], 1000)
ok(b.alloced_bytes_per_op() == 1, "E15a 报 1 B/op（真值 1.999）")
ok(b.allocs_per_op() == 1, "E15b allocs/op = 1000/1000 = 1")

print("E16 Extra 覆盖内建指标（ReportMetric）")
b = GoBench(__import__("main").MemStats(), 1000)
b.run_n([(1000, 24000)], 1000)
ok(b.alloced_bytes_per_op({"B/op": 7.5}) == 7.5, "E16a Extra['B/op'] = 7.5 生效")
ok(b.allocs_per_op({"allocs/op": 3.0}) == 3, "E16b Extra['allocs/op'] 生效")

print("E17 输出模板与 go test 一致")
b = GoBench(__import__("main").MemStats(), 1000)
b.run_n([(1000, 24000)], 2_000_000)
line = go_benchmem_line(b)
ok("ns/op" in line and "B/op" in line and "allocs/op" in line, "E17a 三列齐全", repr(line))
ok(b.ns_per_op() == 2000, "E17b 2000000ns / 1000 = 2000 ns/op")

print("E18 JMH 侧没有「对象数」列 —— 只有字节")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=24000), 10**9, 1000)
ok(not any("allocs" in n for n in names(r)), "E18a JMH 无 allocs 口径", str(names(r)))
ok(abs(get(r, "gc.alloc.rate.norm").value - 24.0) < 1e-12, "E18b 只有 B/op = 24")

print("E19 rate 的单位换算：1 MiB / 1 秒 = 1.0 MB/sec")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=1048576), 10**9, 1)
ok(abs(get(r, "gc.alloc.rate").value - 1.0) < 1e-12, "E19a MB/sec")

print("E20 边界：dt = 0 与 allOps = 0 都是 NaN")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=100), 0, 100)
ok(math.isnan(get(r, "gc.alloc.rate").value), "E20a dt=0 ⇒ rate NaN")
r = jmh_gc_profile(JmhSnapshot(total_alloc=0), JmhSnapshot(total_alloc=100), 10**9, 0)
ok(math.isnan(get(r, "gc.alloc.rate.norm").value), "E20b allOps=0 ⇒ norm NaN")

print("E21 同一次真实分配，两个报告器的差异")
r = compare_true_vs_reported(0.5, 1000)
ok(abs(r["jmh_B_per_op"] - 0.5) < 1e-12 and r["go_B_per_op"] == 0,
   "E21a 0.5 B/op：JMH 0.5 / Go 0")
r = compare_true_vs_reported(24.0, 1000)
ok(r["jmh_B_per_op"] == 24.0 and r["go_B_per_op"] == 24, "E21b 24 B/op：两者一致")
r = compare_true_vs_reported(24.0, 1000, gc_infra_bytes=8000)
ok(abs(r["jmh_B_per_op"] - 32.0) < 1e-12 and r["go_B_per_op"] == 24,
   "E21c JMH 进程级口径会把基础设施的 8000 字节也算进去")

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
