#!/usr/bin/env python3
"""把 span 树的四种归因跑出来。运行：``python main.py``"""

from spantree import CLIENT, INTERNAL, SERVER, Span, anomalies, build, critical_path, pair_gaps, parallel_wait, self_time


def chain_trace():
    """单链：A(根,INTERNAL) → B(CLIENT) → C(SERVER)。"""
    return [
        Span("A", None, "gateway", INTERNAL, 0, 100),
        Span("B", "A", "call user", CLIENT, 5, 95),
        Span("C", "B", "user.handle", SERVER, 10, 70),
    ]


def fanout_trace():
    """扇出：根下三个**并发**子 span（时间上重叠）。"""
    return [
        Span("R", None, "root", INTERNAL, 0, 100),
        Span("a", "R", "db", CLIENT, 0, 60),
        Span("b", "R", "cache", CLIENT, 0, 40),
        Span("c", "R", "rpc", CLIENT, 0, 30),
    ]


def broken_trace():
    """D 的父 span 缺失（上报到了别的实例 / 压根没报）。"""
    return [
        Span("R", None, "root", INTERNAL, 0, 50),
        Span("D", "MISSING", "orphan", INTERNAL, 60, 90),
    ]


def main():
    print("== 1. 单链：self time 直接给出每一段的独占时间 ==")
    t = build(chain_trace())
    for sid in ("A", "B", "C"):
        s = t["by_id"][sid]
        print(f"  {sid} dur={s.duration:6.1f}ms  self={self_time(t, s):6.1f}ms  "
              f"parallel_wait={parallel_wait(t, s):6.1f}ms")
    cost, path = critical_path(t, t["by_id"]["A"])
    print(f"  关键路径: {path}  自耗合计={cost:.1f}ms  （根 duration={t['by_id']['A'].duration:.1f}ms）")

    print("\n== 2. 跨进程一跳：两侧各有一个 span，差值才是网络+排队 ==")
    for g in pair_gaps(t):
        print(f"  {g['client']}(CLIENT) {g['client_ms']:.0f}ms  vs  "
              f"{g['server']}(SERVER) {g['server_ms']:.0f}ms  ->  gap={g['gap_ms']:.0f}ms")
    print("  这段 gap 在 client 侧和 server 侧的 profiler 里都看不见")

    print("\n== 3. 扇出：子 span 并发时 self time 会变负 ==")
    t = build(fanout_trace())
    r = t["by_id"]["R"]
    print(f"  R duration={r.duration:.0f}ms  子 span 之和={sum(c.duration for c in t['children']['R']):.0f}ms")
    print(f"  self_time(R)={self_time(t, r):.0f}ms   <- 负数，说明子 span 是并发的")
    print(f"  parallel_wait(R)={parallel_wait(t, r):.0f}ms   <- 并发口径下的真实等待")
    cost, path = critical_path(t, r)
    print(f"  关键路径: {path}  自耗合计={cost:.0f}ms")

    print("\n== 4. 断链：父 span 缺失的孤儿会被提升为伪根 ==")
    t = build(broken_trace())
    print(f"  roots={sorted(s.span_id for s in t['roots'])}  orphans={[s.span_id for s in t['orphans']]}")
    print(f"  anomalies={anomalies(t)}")


if __name__ == "__main__":
    main()
