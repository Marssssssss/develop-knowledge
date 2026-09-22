#!/usr/bin/env python3
"""把扇出放大与对冲的成本/收益跑成数字。运行：``python main.py``"""

import random

from fanout import (
    _pct,
    HedgeThrottle, amplify, completion_latencies, effective_attempts, hedge, leaf_latencies,
    max_tolerable_fanout, parse_pushback_ms,
)


def sampler(rng):
    """单台服务器的耗时：99% 落在 8–12ms，1% 落在 900–1100ms。"""
    return lambda: leaf_latencies(rng, 1)[0]


def demo_amplification():
    print("== 1. 扇出放大：单台很漂亮，端到端很难看 ==")
    for p, n in ((0.01, 1), (0.01, 10), (0.01, 100), (0.0001, 2000)):
        print(f"  单机慢概率={p:<8} 扇出={n:<5} -> 至少一个慢的概率 {amplify(p, n):.4f}")
    print(f"  倒推：容忍 5% 时 p=1% 最多扇出 {max_tolerable_fanout(0.01, 0.05)} 个下游")
    print(f"        容忍 5% 时 p=0.01% 最多扇出 {max_tolerable_fanout(0.0001, 0.05)} 个下游")


def demo_partial():
    print("\n== 2. 「等到多少算完成」是端到端延迟最大的杠杆 ==")
    rng = random.Random(20260922)
    r = completion_latencies(rng, fanout=100, trials=2000)
    print(f"  单个随机 leaf 完成 p99 : {r['one']:8.1f} ms")
    print(f"  95% 完成        p99 : {r['frac']:8.1f} ms")
    print(f"  全部完成        p99 : {r['all']:8.1f} ms")
    print(f"  -> 本合成分布下，等最后 5% 让 p99 从 {r['frac']:.0f}ms 涨到 {r['all']:.0f}ms")
    print("  论文 Table 1 的真实测量：单 leaf p99=10ms / 95% 完成 p99=70ms / 全部完成 p99=140ms")
    print("  —— 真实系统 leaf 分布的中间尾巴更厚，所以「等最后 5%」付出了 p99 的一半；")
    print("     合成分布（1% 直接掉到 1s）把这个效应放大成了「几乎全部」")


def demo_hedge():
    print("\n== 3. 对冲：delay 取 p95 时负载只加 ~5%，尾部大降 ==")
    rng = random.Random(20260922)
    base = [sampler(rng)() for _ in range(20000)]
    p95 = _pct(base, 95)
    print(f"  先量出这类请求的 p95 = {p95:.1f} ms（论文：secondary 推迟到 p95 之后发）")
    for delay in (p95, 12.5, 500.0):
        rng2 = random.Random(20260922)
        res = hedge(random.Random(7), 6000, delay, sampler(rng2))
        print(f"  delay={delay:6.1f}ms -> p99.9 {res['p999_base']:8.1f} -> {res['p999_hedged']:7.1f} ms | "
              f"p99 {res['p99_base']:6.1f} -> {res['p99_hedged']:6.1f} ms | "
              f"额外负载 {res['extra_load'] * 100:5.1f}%")


def demo_grpc_policy():
    print("\n== 4. gRPC hedging 策略：maxAttempts 封顶、限流、pushback ==")
    for n in (1, 3, 5, 9):
        print(f"  maxAttempts={n} -> 实际生效 {effective_attempts(n)}")
    th = HedgeThrottle(max_tokens=10, token_ratio=0.1)
    print(f"  初始 tokens={th.tokens} 阈值={th.threshold} 可对冲={th.may_hedge()}")
    for _ in range(6):
        th.on_failure()
    print(f"  连续 6 次失败后 tokens={th.tokens} 可对冲={th.may_hedge()}（≤阈值即停发对冲）")
    for _ in range(20):
        th.on_success()
    print(f"  20 次成功后 tokens={th.tokens}（封顶 {th.max_tokens}）可对冲={th.may_hedge()}")
    for v in ("0", "150", "-1", "abc", None):
        print(f"  pushback={v!r:6} -> 下一个对冲延迟 {parse_pushback_ms(v)} ms（None = 不要重试）")


if __name__ == "__main__":
    demo_amplification()
    demo_partial()
    demo_hedge()
    demo_grpc_policy()
