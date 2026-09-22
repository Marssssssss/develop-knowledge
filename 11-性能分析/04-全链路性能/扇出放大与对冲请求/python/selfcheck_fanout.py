#!/usr/bin/env python3
"""demo 600 扇出放大与对冲请求自检。随机部分全部固定种子，结果可复现。"""

import random
import sys

from fanout import (
    HedgeThrottle, _pct, amplify, completion_latencies, effective_attempts, hedge,
    leaf_latencies, max_tolerable_fanout, parse_pushback_ms,
)

FAILED, COUNT = [], 0


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
        FAILED.append(name)
        print(f"  FAIL {name}: got={got!r} want={want!r}")


def close(name, got, want, tol=1e-9):
    global COUNT
    COUNT += 1
    if abs(got - want) > tol:
        FAILED.append(name)
        print(f"  FAIL {name}: got={got} want={want}")


def sampler(rng):
    return lambda: leaf_latencies(rng, 1)[0]


# --- 1. 论文给出的两个放大数 ---
close("p=1% 扇出 100 -> 0.6340（论文 63%）", amplify(0.01, 100), 0.6339676, tol=1e-6)
check("落在 '63%' 附近", 0.62 < amplify(0.01, 100) < 0.64)
close("p=0.01% 扇出 2000 -> 0.1813（论文 'almost one in five'）",
      amplify(0.0001, 2000), 0.1812774, tol=1e-6)
check("落在 'one in five' 附近", 0.15 < amplify(0.0001, 2000) < 0.20)
close("扇出 1 就是不放大", amplify(0.01, 1), 0.01)
close("p=0 恒为 0", amplify(0.0, 999), 0.0)
close("p=1 恒为 1", amplify(1.0, 999), 1.0)

# 单调性
check("扇出越大越糟", all(amplify(0.01, n) < amplify(0.01, n + 1) for n in range(1, 60)))
check("单机越慢越糟", all(amplify(p, 50) < amplify(p * 1.5, 50) for p in (0.001, 0.01, 0.05)))

# --- 2. 倒推最大可容忍扇出 ---
eq("p=1% 容忍 5% -> 5 个下游", max_tolerable_fanout(0.01, 0.05), 5)
check("5 个确实达标", amplify(0.01, 5) <= 0.05)
check("6 个就超了", amplify(0.01, 6) > 0.05)
eq("p=0.01% 容忍 5% -> 512 个下游", max_tolerable_fanout(0.0001, 0.05), 512)
check("512 个确实达标", amplify(0.0001, 512) <= 0.05)
check("513 个就超了", amplify(0.0001, 513) > 0.05)
eq("p=0 视为无限", max_tolerable_fanout(0.0, 0.05), 10 ** 9)
eq("p=1 一个都不能扇出", max_tolerable_fanout(1.0, 0.05), 0)

# --- 3. 完成口径的单调顺序 ---
r = completion_latencies(random.Random(20260922), fanout=100, trials=2000)
check("单个 leaf < 95% 完成 < 全部完成", r["one"] < r["frac"] < r["all"])
check("全部完成的 p99 已经进入秒级", r["all"] > 500.0)
check("95% 完成仍留在毫秒级", r["frac"] < 100.0)
check("等最后 5% 付出了绝大部分 p99",
      (r["all"] - r["frac"]) / r["all"] > 0.9)

# --- 4. 对冲：delay = p95 → 额外负载 ≈ 5% ---
rng = random.Random(20260922)
base = [sampler(rng)() for _ in range(20000)]
p95 = _pct(base, 95)
check("p95 落在快档区间内", 8.0 <= p95 <= 12.0)
res = hedge(random.Random(7), 6000, p95, sampler(random.Random(20260922)))
close("额外负载 ≈ 5%（论文：limits the additional load to approximately 5%）",
      res["extra_load"], 0.05, tol=0.01)
check("p99.9 大幅下降", res["p999_hedged"] < res["p999_base"] * 0.1)
check("p99 也下降", res["p99_hedged"] < res["p99_base"] * 0.1)
check("对冲后的 p99.9 从秒级回到毫秒级", res["p999_hedged"] < 100.0)

# 对冲绝不会更差（min(t1, d+t2) 的分位不高于 t1 的分位）
res2 = hedge(random.Random(11), 4000, 0.0, sampler(random.Random(3)))
check("delay=0 时 p99.9 不劣于基线", res2["p999_hedged"] <= res2["p999_base"] + 1e-9)
check("delay=0 时几乎每个请求都发 secondary", res2["extra_load"] > 0.9)

# delay 过大：负载省了但尾部没救
res3 = hedge(random.Random(7), 6000, 500.0, sampler(random.Random(20260922)))
check("delay=500ms 额外负载很低", res3["extra_load"] < 0.02)
check("delay=500ms 救不了 p99.9", res3["p999_hedged"] > 400.0)

# --- 5. gRPC hedging 策略 ---
eq("maxAttempts 1 保持 1", effective_attempts(1), 1)
eq("maxAttempts 5 保持 5", effective_attempts(5), 5)
eq("maxAttempts 9 被压到 5", effective_attempts(9), 5)
eq("maxAttempts 100 也被压到 5", effective_attempts(100), 5)

th = HedgeThrottle(max_tokens=10, token_ratio=0.1)
close("初始 tokens = maxTokens", th.tokens, 10.0)
close("阈值 = maxTokens / 2", th.threshold, 5.0)
check("初始可对冲", th.may_hedge())
for _ in range(5):
    th.on_failure()
close("5 次失败后 tokens = 5", th.tokens, 5.0)
check("等于阈值即停发对冲（严格大于才算）", not th.may_hedge())
th.on_failure()
check("6 次失败后仍不可对冲", not th.may_hedge())
close("下限为 0 不会变负", HedgeThrottle(max_tokens=1, token_ratio=0.1).on_failure() or 0.0, 0.0)
t2 = HedgeThrottle(max_tokens=10, token_ratio=0.1)
for _ in range(6):
    t2.on_failure()
for _ in range(20):
    t2.on_success()
check("20 次成功后恢复可对冲", t2.may_hedge())
t3 = HedgeThrottle(max_tokens=10, token_ratio=0.1)
for _ in range(1000):
    t3.on_success()
close("成功加分封顶于 maxTokens", t3.tokens, 10.0)

# --- 6. pushback 解析 ---
close("'150' -> 150ms", parse_pushback_ms("150"), 150.0)
close("'0' -> 0ms（立即发下一个）", parse_pushback_ms("0"), 0.0)
eq("'-1' -> 不要重试", parse_pushback_ms("-1"), None)
eq("'abc' 不可解析 -> 不要重试", parse_pushback_ms("abc"), None)
eq("缺失 -> 不要重试", parse_pushback_ms(None), None)
eq("空串 -> 不要重试", parse_pushback_ms(""), None)

print(f"\n{COUNT - len(FAILED)}/{COUNT} 断言通过")
sys.exit(1 if FAILED else 0)
