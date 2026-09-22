#!/usr/bin/env python3
"""扇出放大的算术与对冲（hedging）的成本/收益。

量化口径全部来自实读的 Dean & Barroso《The Tail at Scale》与 gRPC 官方 Request Hedging 指南：

《The Tail at Scale》
  1. "each server typically responds in 10ms but with a 99th-percentile latency of one second"
     → 扇出 100 台时 "63% of user requests will take more than one second"；
  2. "Even for services with only one in 10,000 requests experiencing more than one-second
     latencies at the single-server level, a service with 2,000 such servers will see almost
     one in five user requests taking more than one second"；
  3. Table 1：单个随机 leaf 完成的 p99 = 10ms、全部完成的 p99 = 140ms、**95% 完成**的 p99 = 70ms
     —— "waiting for the slowest 5% of the requests is responsible for half of the total
     99%-percentile latency"；
  4. 对冲：secondary 请求推迟到第一个请求已 outstanding 超过 **95th-percentile** 时再发，
     "limits the additional load to approximately 5% while substantially shortening the
     latency tail"；Google benchmark：1000 keys / 100 servers，**10ms 后对冲**，
     p99.9 从 **1800ms 降到 74ms**，多发的请求只有 **2%**；
  5. tied requests：发给两台，间隔 **2 倍平均网络消息延迟**（现代数据中心 ≤1ms）；
     Table 2 隔离场景下 median −16%、p99.9 近 −40%，磁盘开销 <1%。

gRPC Request Hedging
  6. ``maxAttempts`` 必填，**>5 时按 5 处理**；``hedgingDelay`` 不填则**所有请求同时发出**；
  7. deadline 作用于**整条对冲链**（"gRPC call deadlines apply to the entire chain"）；
  8. 限流：``maxTokens``/``tokenRatio``，失败 −1、成功 +token_ratio，
     对冲请求仅在 ``token_count > maxTokens/2`` 时发出；只有 non-fatal 状态码或
     "pushback 不要重试"才算失败（避免把 INVALID_ARGUMENT 当成服务故障）；
  9. pushback 用 metadata ``grpc-retry-pushback-ms``，负值或不可解析 = **不要重试**。
"""

from __future__ import annotations

import math
import random


def amplify(p: float, n: int) -> float:
    """至少一个下游超阈值的概率：``1 − (1−p)^n``。"""
    return 1.0 - (1.0 - p) ** n


def max_tolerable_fanout(p: float, target: float) -> int:
    """在"至少一个下游慢"的概率不超过 ``target`` 的前提下，最多能扇出多少个下游。

    ``1 − (1−p)^n ≤ target  ⟺  n ≤ log(1−target) / log(1−p)``。
    """
    if p <= 0:
        return 10 ** 9
    if p >= 1:
        return 0
    return max(0, int(math.floor(math.log(1.0 - target) / math.log(1.0 - p))))


def leaf_latencies(rng: random.Random, n: int, slow_p: float = 0.01,
                   fast_ms: tuple[float, float] = (8.0, 12.0),
                   slow_ms: tuple[float, float] = (900.0, 1100.0)) -> list[float]:
    """采样 n 个下游的耗时：99% 落在 8–12ms，1% 落在 900–1100ms（对应论文的 10ms / 1s 口径）。"""
    out = []
    for _ in range(n):
        lo, hi = (slow_ms if rng.random() < slow_p else fast_ms)
        out.append(rng.uniform(lo, hi))
    return out


def completion_latencies(rng: random.Random, fanout: int, trials: int, **kw) -> dict:
    """对每次请求采样 ``fanout`` 个下游，给出三种完成口径的 p99。

    - ``one``：单个随机 leaf 完成（≈ 最快的那个）；
    - ``frac``：已有 95% 的下游完成；
    - ``all``：全部完成。
    """
    one, frac, allv = [], [], []
    for _ in range(trials):
        ls = sorted(leaf_latencies(rng, fanout, **kw))
        one.append(ls[0])
        frac.append(ls[int(math.ceil(0.95 * len(ls))) - 1])
        allv.append(ls[-1])
    return {"one": _pct(one, 99), "frac": _pct(frac, 99), "all": _pct(allv, 99)}


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(math.ceil(q / 100 * len(s))) - 1))
    return s[idx]


def hedge(rng: random.Random, trials: int, delay_ms: float, sampler) -> dict:
    """对冲模拟：primary 立即发，``delay_ms`` 后发 secondary，取先回的那个。

    ``extra_load`` = 触发 secondary 的比例（primary 超过 delay 才发），
    论文把它控制在 ~5%（delay 取 p95）。
    """
    base, hedged, extra = [], [], 0
    for _ in range(trials):
        t1 = sampler()
        t2 = sampler()
        base.append(t1)
        if t1 > delay_ms:
            extra += 1
            hedged.append(min(t1, delay_ms + t2))
        else:
            hedged.append(t1)
    return {
        "p999_base": _pct(base, 99.9),
        "p999_hedged": _pct(hedged, 99.9),
        "p99_base": _pct(base, 99),
        "p99_hedged": _pct(hedged, 99),
        "extra_load": extra / trials,
    }


class HedgeThrottle:
    """gRPC 的 ``RetryThrottlingPolicy``：失败 −1、成功 +token_ratio。"""

    def __init__(self, max_tokens: int = 10, token_ratio: float = 0.1) -> None:
        self.max_tokens = max_tokens
        self.token_ratio = token_ratio
        self.tokens = float(max_tokens)

    @property
    def threshold(self) -> float:
        """对冲请求只有在 ``token_count > max_tokens / 2`` 时才发出。"""
        return self.max_tokens / 2.0

    def on_success(self) -> None:
        self.tokens = min(float(self.max_tokens), self.tokens + self.token_ratio)

    def on_failure(self) -> None:
        self.tokens = max(0.0, self.tokens - 1.0)

    def may_hedge(self) -> bool:
        return self.tokens > self.threshold


def effective_attempts(max_attempts: int) -> int:
    """``If the specified value is greater than 5, gRPC uses a value of 5.``"""
    return min(max_attempts, 5)


def parse_pushback_ms(value: str | None) -> float | None:
    """``grpc-retry-pushback-ms``：负值或不可解析都表示"不要重试"（返回 ``None``）。"""
    if value is None:
        return None
    try:
        v = float(value.strip())
    except (TypeError, ValueError):
        return None
    return None if v < 0 else v
