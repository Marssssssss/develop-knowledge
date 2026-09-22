#!/usr/bin/env python3
"""demo 598 关键路径与 self time 归因自检。"""

import sys

from spantree import (
    CLIENT, CONSUMER, INTERNAL, PRODUCER, SERVER, PAIRS, Span, anomalies, build,
    critical_path, pair_gaps, parallel_wait, self_time,
)

FAILED: list[str] = []
COUNT = 0


def check(name, cond):
    global COUNT
    COUNT += 1
    if not cond:
        FAILED.append(name)
        print(f"  FAIL {name}")


def eq(name, got, want):
    global COUNT
    COUNT += 1
    if got != want:
        FAILED.append(f"{name} (got={got!r} want={want!r})")
        print(f"  FAIL {name}: got={got!r} want={want!r}")


def close(name, got, want, tol=1e-9):
    global COUNT
    COUNT += 1
    if abs(got - want) > tol:
        FAILED.append(f"{name} (got={got} want={want})")
        print(f"  FAIL {name}: got={got} want={want}")


def chain():
    return [
        Span("A", None, "gateway", INTERNAL, 0, 100),
        Span("B", "A", "call user", CLIENT, 5, 95),
        Span("C", "B", "user.handle", SERVER, 10, 70),
    ]


def fanout():
    return [
        Span("R", None, "root", INTERNAL, 0, 100),
        Span("a", "R", "db", CLIENT, 0, 60),
        Span("b", "R", "cache", CLIENT, 0, 40),
        Span("c", "R", "rpc", CLIENT, 0, 30),
    ]


# --- 1. 单链的 self time ---
t = build(chain())
close("A self = 100-90", self_time(t, t["by_id"]["A"]), 10.0)
close("B self = 90-60", self_time(t, t["by_id"]["B"]), 30.0)
close("C self = 60（叶子即自身）", self_time(t, t["by_id"]["C"]), 60.0)
close("三者之和 = 根 duration", sum(self_time(t, t["by_id"][s]) for s in "ABC"), 100.0)

# --- 2. 单链的关键路径是整条链，且合计 == 根 duration ---
cost, path = critical_path(t, t["by_id"]["A"])
eq("关键路径覆盖全部节点", path, ["A", "B", "C"])
close("单链的关键路径合计 == 根 duration", cost, 100.0)

# --- 3. 关键路径会挑**慢**的分支，不是**宽**的分支 ---
t2 = build([
    Span("R", None, "root", INTERNAL, 0, 100),
    Span("slow", "R", "slow", INTERNAL, 0, 90),      # self = 90
    Span("f1", "R", "wide1", INTERNAL, 0, 5),        # 两个短分支，合计只有 10
    Span("f2", "R", "wide2", INTERNAL, 0, 5),
])
cost2, path2 = critical_path(t2, t2["by_id"]["R"])
eq("关键路径选 slow 分支", path2, ["R", "slow"])
close("关键路径合计 = self(R)=0 + 90", cost2, 90.0)

# --- 4. 跨进程配对与 gap ---
gaps = pair_gaps(t)
eq("只配出一对", len(gaps), 1)
eq("client 侧是 B", gaps[0]["client"], "B")
eq("server 侧是 C", gaps[0]["server"], "C")
close("gap = 90 - 60", gaps[0]["gap_ms"], 30.0)

# 负控：方向反过来（SERVER 父 + CLIENT 子）不该被配成对
t_rev = build([
    Span("S", None, "server", SERVER, 0, 100),
    Span("c", "S", "client", CLIENT, 0, 50),
])
eq("(SERVER, CLIENT) 不是合法配对", pair_gaps(t_rev), [])
# 负控：INTERNAL 父 + SERVER 子也不是
t_int = build([
    Span("I", None, "internal", INTERNAL, 0, 100),
    Span("s", "I", "server", SERVER, 0, 50),
])
eq("(INTERNAL, SERVER) 不是合法配对", pair_gaps(t_int), [])
# PRODUCER/CONSUMER 也应配成一对
t_mq = build([
    Span("P", None, "produce", PRODUCER, 0, 20),
    Span("Q", "P", "consume", CONSUMER, 100, 160),
])
eq("(PRODUCER, CONSUMER) 配成一对", len(pair_gaps(t_mq)), 1)
close("CONSUMER 可晚于 PRODUCER 开始，gap 为负", pair_gaps(t_mq)[0]["gap_ms"], -40.0)
eq("PAIRS 只含两个组合", len(PAIRS), 2)

# --- 5. 并发子 span 让 self time 变负 ---
t = build(fanout())
r = t["by_id"]["R"]
close("子 span 之和 130 > 根 duration 100", sum(c.duration for c in t["children"]["R"]), 130.0)
close("self_time(R) 为负", self_time(t, r), -30.0)
check("负 self time 是并发的信号", self_time(t, r) < 0)
close("parallel_wait 用 max 口径", parallel_wait(t, r), 40.0)
cost, path = critical_path(t, r)
eq("并发下关键路径取最长的那个分支", path, ["R", "a"])
close("关键路径合计 = -30 + 60", cost, 30.0)
check("并发时关键路径合计 < 根 duration", cost < r.duration)

# --- 6. 孤儿与多根 ---
t = build([
    Span("R", None, "root", INTERNAL, 0, 50),
    Span("D", "MISSING", "orphan", INTERNAL, 60, 90),
])
eq("两个根（含被提升的孤儿）", sorted(s.span_id for s in t["roots"]), ["D", "R"])
eq("孤儿被单独列出", [s.span_id for s in t["orphans"]], ["D"])
close("孤儿的 self time 等于自身时长", self_time(t, t["by_id"]["D"]), 30.0)
an = anomalies(t)
check("报出多根", any(a.startswith("multi_root") for a in an))
check("报出孤儿", any(a.startswith("orphan") for a in an))
check("此例无负 self time", not any(a.startswith("negative_self_time") for a in an))
an2 = anomalies(build(fanout()))
check("并发例报出负 self time", any(a == "negative_self_time:R" for a in an2))

# --- 7. 单节点树 ---
t = build([Span("Z", None, "solo", INTERNAL, 0, 7)])
cost, path = critical_path(t, t["by_id"]["Z"])
eq("单节点关键路径只有自己", path, ["Z"])
close("单节点关键路径合计 = duration", cost, 7.0)
close("单节点 parallel_wait = duration", parallel_wait(t, t["by_id"]["Z"]), 7.0)
eq("单节点无异常", anomalies(t), [])

# --- 8. 同父多子：children 顺序不影响结论（按集合比较） ---
spans_a = [
    Span("R", None, "root", INTERNAL, 0, 100),
    Span("x", "R", "x", INTERNAL, 0, 20),
    Span("y", "R", "y", INTERNAL, 20, 60),
]
spans_b = list(reversed(spans_a))
ta, tb = build(spans_a), build(spans_b)
eq("children 集合与顺序无关",
   {s.span_id for s in ta["children"]["R"]}, {s.span_id for s in tb["children"]["R"]})
close("self time 与子顺序无关", self_time(ta, ta["by_id"]["R"]), self_time(tb, tb["by_id"]["R"]))
eq("关键路径与子顺序无关",
   critical_path(ta, ta["by_id"]["R"])[1], critical_path(tb, tb["by_id"]["R"])[1])

print(f"\n{COUNT - len(FAILED)}/{COUNT} 断言通过")
sys.exit(1 if FAILED else 0)
