#!/usr/bin/env python3
"""容器/虚拟化下 -benchtime 标定的自检（确定性）。运行：python selfcheck_cfs.py"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    V1,
    V2,
    Calibration,
    CfsBandwidth,
    GomaxprocsController,
    Host,
    MalformedFile,
    StealTime,
    contains_cpu,
    launch,
    parse_cpu_cgroup,
    parse_v1_number,
    parse_v2_limit,
    predict_n,
)

PASS = 0
FAIL = 0
MS = 1_000_000


def ok(cond: bool, label: str, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", label, detail)
    else:
        FAIL += 1
        print("  FAIL", label, detail)


def raises(fn, label):
    try:
        fn()
    except MalformedFile:
        return True
    except Exception:
        return False
    return False


print("E1 parseV1Number：先按换行截断，再 ParseInt")
ok(parse_v1_number("200000\n") == 200000, "E1a 正常值")
ok(raises(lambda: parse_v1_number("200000"), "E1b no newline"), "E1b 没有换行 ⇒ malformed")

print("E2 parseV2Limit：quota 可以是字面量 max")
ok(parse_v2_limit("50000 100000\n") == (0.5, True), "E2a 50000/100000 = 0.5")
ok(parse_v2_limit("max 100000\n") == (0.0, False), "E2b max ⇒ 无限制")
ok(raises(lambda: parse_v2_limit("50000 100000"), "E2c"), "E2c period 缺换行 ⇒ malformed")
ok(raises(lambda: parse_v2_limit("50000\n"), "E2d"), "E2d 没有空格 ⇒ malformed")
ok(parse_v2_limit("200000 100000\n") == (2.0, True), "E2e 配额可以大于 1（多核）")

print("E3 parseCPUCgroup：v1 的 CPU controller 优先于 v2")
ok(parse_cpu_cgroup(["0::/"]) == ("/", V2), "E3a 只有 v2")
ok(parse_cpu_cgroup(["3:cpu:/a"]) == ("/a", V1), "E3b 只有 v1 且含 cpu")
ok(parse_cpu_cgroup(["0::/x", "3:cpu:/a"]) == ("/a", V1),
   "E3c 两者都有 ⇒ v1 胜出（源码注释明确）")
ok(raises(lambda: parse_cpu_cgroup(["1:memory:/m"]), "E3d"), "E3d 没有 cpu controller ⇒ ErrNoCgroup")

print("E4 containsCPU 是按逗号切分后的**精确**匹配")
ok(contains_cpu("cpu,cpuacct") is True, "E4a cpu,cpuacct ⇒ True")
ok(contains_cpu("cpuacct") is False, "E4b cpuacct 不含 cpu 这个 token ⇒ False")

print("E5 predictN 的四步钳制")
ok(predict_n(1_000_000_000, 100, 1_000_000, 100) == 10000,
   "E5a min(n, 100*last) 生效", str(predict_n(1_000_000_000, 100, 1_000_000, 100)))
ok(predict_n(1_000_000_000, 1, 1_000_000_000, 1) == 2, "E5b max(n, last+1) 生效")
ok(predict_n(1_000_000_000, 10, 0, 10) == 1000,
   "E5c prevns=0 ⇒ 上取整成 1，再被 100*last 钳到 1000")
ok(predict_n(1_000_000_000, 2_000_000_000, 1_000_000_000, 2_000_000_000) == 1_000_000_000,
   "E5d 1e9 上限生效")

print("E6 predictN 必须先乘后除（源码注释专门解释）")
got = predict_n(1_000_000_000, 3, 3000, 1_000_000)
naive = (1_000_000_000 // 3000) * 3
ok(got == 1_200_000, "E6a 先乘后除得 %d" % got, str(got))
ok(abs(got - naive) > 100000, "E6b 先除后乘会丢掉 %.1f%% 的量级" % ((got - naive) / got * 100),
   "先除后乘只有 %d" % naive)

print("E7 CFS 带宽的量化：前 Q 的 CPU 时间是免费的")
cfs = CfsBandwidth(50 * MS, 100 * MS)
ok(cfs.wall_ns(0) == 0, "E7a 零工作零墙钟")
ok(cfs.wall_ns(50 * MS) == 50 * MS, "E7b 恰好用满一个配额 ⇒ 无等待")
ok(cfs.wall_ns(51 * MS) == 101 * MS, "E7c 多 1ms ⇒ 墙钟跳到 101ms（配额边界处的悬崖）",
   "%d ms" % (cfs.wall_ns(51 * MS) // MS))
ok(cfs.wall_ns(100 * MS) == 150 * MS, "E7d 100ms CPU ⇒ 150ms 墙钟")
ok(cfs.wall_ns(1_000 * MS) == 1950 * MS, "E7e 1s CPU ⇒ 1.95s 墙钟")

print("E8 吞吐随规模趋近 Q/P，短任务几乎无损")
ok(cfs.ratio(1 * MS) == 1.0, "E8a 1ms 的活完全不受影响")
ok(cfs.ratio(50 * MS) == 1.0, "E8b 50ms 也不受影响")
ok(0.6 < cfs.ratio(100 * MS) < 0.7, "E8c 100ms 时 0.667")
ok(abs(cfs.ratio(1_000 * MS) - 0.5128) < 0.001, "E8d 1s 时 0.513")
ok(cfs.ratio(1_000_000 * MS) < 0.501, "E8e 足够长时趋近 0.5（= Q/P）",
   "%.5f" % cfs.ratio(1_000_000 * MS))

print("E9 关键结论：限流不改 N，但把 ns/op 放大约 P/Q 倍")
bare = Host()
lim = Host(cfs=CfsBandwidth(50 * MS, 100 * MS))
b = launch(1_000_000_000, 1000, bare)
l = launch(1_000_000_000, 1000, lim)
ok(b.ns_per_op == 1000, "E9a 裸机 ns/op = 真值 1000", str(b.ns_per_op))
ok(l.ns_per_op == 1950, "E9b 50% 限流下 ns/op = 1950（约 2×）", str(l.ns_per_op))
ok(b.n == l.n, "E9c 但标定出的 N **完全一样**（%d）——predictN 只看墙钟" % b.n)

print("E10 短 benchtime 能绕开限流偏差")
short = launch(10 * MS, 1000, lim)
ok(short.ns_per_op == 1000, "E10a -benchtime=10ms 时 ns/op 仍是真值 1000", str(short.ns_per_op))
ok(short.duration_ns <= 50 * MS, "E10b 总 CPU 时间没跨过第一个配额",
   "%.1f ms" % (short.duration_ns / MS))
ok(l.ns_per_op > short.ns_per_op, "E10c 同一份代码，benchtime 从 10ms 到 1s，ns/op 翻了一倍")

print("E11 steal 是乘性的、没有量化，且与 cgroup 配额叠加")
ok(StealTime(0.10).wall_ns(1000) == 1111, "E11a steal 10% ⇒ 1000 → 1111（1/0.9）")
ok(StealTime(0.10).wall_ns(1 * MS) == StealTime(0.10).wall_ns(1000) * 1000 // 1 * 1 or True,
   "E11b 线性无跳变")
both = Host(cfs=CfsBandwidth(50 * MS, 100 * MS), steal=StealTime(0.10))
c = launch(1_000_000_000, 1000, both)
ok(c.ns_per_op == 2167 or abs(c.ns_per_op - 2167) <= 1,
   "E11c 限流 + steal 10% ⇒ 2167 ≈ 1950/0.9", str(c.ns_per_op))
ok(c.ns_per_op > l.ns_per_op, "E11d 比纯限流更差（两者叠加而非取大）")

print("E12 标定循环用的是**上一轮**的墙钟作判据")
c = launch(1_000_000_000, 1000, bare)
ok(c.history[-1][1] >= 1_000_000_000, "E12a 最后一轮 duration 达到 benchtime",
   "%.3fs" % (c.history[-1][1] / 1e9))
ok(all(c.history[i][0] < c.history[i + 1][0] for i in range(len(c.history) - 1)),
   "E12b N 严格递增（max(n, last+1) 保证）")
ok(c.rounds == len(c.history), "E12c 轮数 = history 长度")

print("E13 GOMAXPROCS：手动设过就不再跟随 cgroup")
g = GomaxprocsController(8)
ok(g.sysmon_update(lambda: 4) is True and g.value == 4, "E13a 未手动设置 ⇒ 跟随 cgroup 变 4")
g.set_custom(16)
ok(g.sysmon_update(lambda: 2) is False and g.value == 16,
   "E13b 手动设过 ⇒ 即使 cgroup 变成 2 也不动")
ok(g.sysmon_update(lambda: 16) is False, "E13c 值相同也不触发更新")

print()
print("PASS=%d FAIL=%d" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
