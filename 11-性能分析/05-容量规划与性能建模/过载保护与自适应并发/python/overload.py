"""过载保护：关键性分级、客户端自适应限流、Envoy 梯度控制器。

口径全部来自实读资料：
  * Google SRE Book《Chapter 21 - Handling Overload》
        四个 criticality 值：CRITICAL_PLUS / CRITICAL / SHEDDABLE_PLUS / SHEDDABLE
        "只有当所有更低关键性的请求都已经在被拒绝时，才会拒绝某个关键性的请求"
        任务级过载保护基于 utilization（CPU rate / reserved CPU，有时含内存）
        客户端自适应限流：两分钟窗口内记 requests（应用层尝试数）与 accepts
        （后端接受数），requests 达到 K 倍 accepts 时客户端开始本地拒绝；K 默认 2
        "即使在大过载下，后端最终是每处理一个请求就拒绝一个"（K=2）
        "把 K 降到 1.1 意味着每接受 10 个请求才拒绝 1 个"
        注：拒绝概率曲线在原书中只有图（Client request rejection probability），
        本 demo 不臆造该式，只建模原文明确写出的**门限条件与稳态结论**
  * Envoy《Adaptive Concurrency》文档（gradient controller）
        gradient = (minRTT + B) / sampleRTT,  B = minRTT * buffer_pct
        limit_new = gradient * limit_old + headroom
        headroom 不可配置，固定为并发上限的平方根
        minRTT 通过把并发钉在 min_concurrency 上周期性测得
"""

from __future__ import annotations

import math

# ------------------------------------------------------- 1. 关键性分级（SRE）
CRITICAL_PLUS = "CRITICAL_PLUS"
CRITICAL = "CRITICAL"
SHEDDABLE_PLUS = "SHEDDABLE_PLUS"
SHEDDABLE = "SHEDDABLE"

# 关键性从高到低；SRE 书明确写了四个值，且"越高关键性阈值越高"
CRITICALITY_ORDER = (SHEDDABLE, SHEDDABLE_PLUS, CRITICAL, CRITICAL_PLUS)


def criticality_rank(c: str) -> int:
    if c not in CRITICALITY_ORDER:
        raise ValueError(f"未知关键性：{c}")
    return CRITICALITY_ORDER.index(c)


class CriticalityShedder:
    """基于利用率的分级降级：每个关键性一个阈值，阈值随关键性升高而升高。"""

    def __init__(self, thresholds: dict):
        for c in CRITICALITY_ORDER:
            if c not in thresholds:
                raise ValueError(f"缺少关键性 {c} 的阈值")
        ranks = [criticality_rank(c) for c in CRITICALITY_ORDER]
        vals = [thresholds[c] for c in CRITICALITY_ORDER]
        if any(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)):
            raise ValueError("阈值必须随关键性升高而升高")
        if any(not 0.0 < v <= 1.0 for v in vals):
            raise ValueError("阈值必须在 (0,1]")
        self.thresholds = dict(thresholds)

    def serves(self, c: str, utilization: float) -> bool:
        """该关键性的请求在当前利用率下是否仍被服务。"""
        return utilization < self.thresholds[c]

    def rejected_set(self, utilization: float):
        """当前利用率下**已被拒绝**的关键性集合（按从低到高）。"""
        return [c for c in CRITICALITY_ORDER if not self.serves(c, utilization)]

    def check_invariant(self, utilization: float) -> bool:
        """SRE 书的不变量：拒绝某关键性 ⇒ 所有更低关键性都已被拒绝。"""
        rejected = self.rejected_set(utilization)
        if not rejected:
            return True
        highest = max(criticality_rank(c) for c in rejected)
        return all(criticality_rank(c) in
                   {criticality_rank(x) for x in rejected}
                   for c in CRITICALITY_ORDER[:highest])


# --------------------------------------------- 2. 客户端自适应限流（SRE 书）
def client_throttle_equilibrium(app_rate: float, backend_capacity: float,
                                K: float = 2.0):
    """客户端自适应限流的稳态点。

    backend_capacity 是后端**能真正接受**的速率 C（超过的部分被后端拒绝）。
    门限条件把发送速率钉在 S = K·C：
        未过载 (G ≤ C)        : 全部发送、全部接受，零拒绝
        后端过载 (C < G ≤ K·C): 全部发送，后端拒绝 G − C
        客户端自限 (G > K·C)  : 发送 S = K·C，本地拒绝 G − K·C，
                                后端接受 C、拒绝 (K−1)C
    返回 (sent, accepted, backend_rejected, local_rejected)。
    """
    if app_rate < 0 or backend_capacity < 0 or K <= 0:
        raise ValueError("参数非法")
    G, C = app_rate, backend_capacity
    sent = min(G, K * C)
    accepted = min(sent, C)
    backend_rejected = sent - accepted
    local_rejected = G - sent
    return sent, accepted, backend_rejected, local_rejected


def backend_reject_ratio(K: float) -> float:
    """稳态下后端「拒绝/接受」= K − 1（K=2 时 1:1，K=1.1 时 1:10）。"""
    return K - 1.0


# -------------------------------------------- 3. Envoy 梯度控制器
def envoy_gradient(min_rtt: float, sample_rtt: float, buffer_pct: float = 0.0):
    """gradient = (minRTT + B) / sampleRTT，B = minRTT * buffer_pct。"""
    if min_rtt <= 0 or sample_rtt <= 0:
        raise ValueError("RTT 必须为正")
    b = min_rtt * buffer_pct
    return (min_rtt + b) / sample_rtt


def envoy_headroom(limit: float) -> float:
    """headroom 不可配置，固定为 sqrt(limit)。"""
    if limit < 0:
        raise ValueError("limit 必须非负")
    return math.sqrt(limit)


def envoy_update(limit: float, gradient: float,
                 min_concurrency_limit: float = 1.0,
                 max_concurrency_limit: float = 1e9) -> float:
    """limit_new = gradient * limit_old + headroom，再夹到配置区间。"""
    new = gradient * limit + envoy_headroom(limit)
    if new < min_concurrency_limit:
        return min_concurrency_limit
    if new > max_concurrency_limit:
        return max_concurrency_limit
    return new
