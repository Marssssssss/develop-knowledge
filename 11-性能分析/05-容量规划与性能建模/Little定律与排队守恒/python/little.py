"""Little 定律与 M/M/1 排队守恒 —— 最小可验证实现。

全部公式口径来自实读资料：
  * Wikipedia《Little's law》: L = lambda * W（唯一假设：稳态、系统守恒）
  * Wikipedia《M/M/1 queue》: rho = lambda/mu, pi_i = (1-rho) rho^i
  * cloudandsre《Queueing Theory for SREs》: W = S/(1-rho) 的拐点表、
    "10000 深队列 + 2s 超时 = 全部作废" 的死队列判据
  * Mor Harchol-Balter《Performance Modeling and Design of Computer Systems》
    Ch.1（CMU 公开样章）: 排队论的两个目标 = 预测延迟 + 用容量规划选设计
"""

from __future__ import annotations

import math
import random


# ---------------------------------------------------------------- Little 定律
def little_l(L=None, W=None, lam=None):
    """三选二求第三个：L = lambda * W。"""
    if L is None:
        return lam * W
    if W is None:
        return L / lam
    if lam is None:
        return L / W
    raise ValueError("exactly one of L/W/lam must be None")


def utilization(lam, S, servers=1):
    """利用率定律 U = lambda * S / m。"""
    return lam * S / servers


# ---------------------------------------------------------------------- M/M/1
class MM1:
    """单服务台、泊松到达、指数服务的稳态量。"""

    def __init__(self, lam: float, mu: float):
        if lam <= 0 or mu <= 0:
            raise ValueError("lam 和 mu 必须为正")
        if lam >= mu:
            raise ValueError(f"不稳定：lam={lam} 必须小于 mu={mu}（rho<1）")
        self.lam = lam
        self.mu = mu

    @property
    def rho(self) -> float:
        return self.lam / self.mu

    @property
    def S(self) -> float:
        return 1.0 / self.mu

    @property
    def L(self) -> float:
        """系统内平均数量 = rho/(1-rho)。"""
        return self.rho / (1.0 - self.rho)

    @property
    def Lq(self) -> float:
        """平均排队长度 = rho^2/(1-rho)。"""
        return self.rho * self.rho / (1.0 - self.rho)

    @property
    def W(self) -> float:
        """平均逗留时间 = 1/(mu-lam)。"""
        return 1.0 / (self.mu - self.lam)

    @property
    def Wq(self) -> float:
        """平均排队等待 = rho/(mu-lam)。"""
        return self.rho / (self.mu - self.lam)

    def wait_multiplier(self) -> float:
        """W/S —— 相对服务时间被放大的倍数 = 1/(1-rho)。"""
        return 1.0 / (1.0 - self.rho)

    def state_prob(self, k: int) -> float:
        """pi_k = (1-rho) rho^k。"""
        return (1.0 - self.rho) * self.rho ** k


def knee_table(rhos=(0.1, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99), S=0.1):
    """拐点表：给定服务时间 S，(rho, 等待倍率, 实际 W)。"""
    out = []
    for r in rhos:
        m = MM1(r / S, 1.0 / S)
        out.append((r, m.wait_multiplier(), m.W))
    return out


# -------------------------------------------------------------- 死队列判据
def dead_queue(depth: int, drain_rate: float, timeout: float):
    """队列里已有 depth 个元素、以 drain_rate 个/秒排空、调用方超时 timeout 秒。

    返回 (作废数, 有效数)。排空顺序即入队顺序：第 i 个（0-based）在 t=i/drain_rate
    完成，因此完成时刻 **严格小于** timeout 的才算有效（恰好等于超时时刻视为作废，
    调用方已经放弃）。
    """
    if depth <= 0:
        raise ValueError("depth 必须为正")
    if drain_rate <= 0:
        raise ValueError("drain_rate 必须为正")
    useful = 0
    while useful < depth and useful / drain_rate < timeout:
        useful += 1
    return depth - useful, useful


def time_to_drain(depth: int, drain_rate: float) -> float:
    return depth / drain_rate


# --------------------------------------------------------------- 池子容量
def pool_size(lam: float, W: float, headroom: float = 1.0) -> int:
    """由 L = lambda*W 反推需要的并发额度，headroom 为放大系数。"""
    return math.ceil(lam * W * headroom)


def throughput_ceiling(S: float, workers: int = 1) -> float:
    """每个 worker 一次只处理一个请求时的吞吐上限 = workers / S。"""
    return workers / S


# ------------------------------------------------------------- 事件驱动仿真
def simulate_mm1(lam: float, mu: float, t_end: float, seed: int = 20240924):
    """M/M/1 事件驱动仿真，返回 (L_hat, lambda_hat, W_hat, n_dep)。

    L_hat 是时间平均（面积/时长），W_hat 是**已完成**请求的平均逗留时间，
    lambda_hat 用**离开**计数（稳态下等于到达率，且保证 L 与 W 统计的是同一批人）。
    """
    rng = random.Random(seed)
    t = 0.0
    last = 0.0
    n = 0
    area = 0.0
    waiting = []            # 已到达尚未离开的到达时刻（队列 + 服务中）
    n_dep = 0
    sum_w = 0.0
    next_arr = rng.expovariate(lam)
    next_dep = math.inf

    while True:
        t = min(next_arr, next_dep)
        if t >= t_end:
            break
        area += n * (t - last)
        last = t
        if next_arr <= next_dep:
            n += 1
            waiting.append(t)
            next_arr = t + rng.expovariate(lam)
            if n == 1:
                next_dep = t + rng.expovariate(mu)
        else:
            n -= 1
            n_dep += 1
            sum_w += t - waiting.pop(0)
            if n > 0:
                next_dep = t + rng.expovariate(mu)
            else:
                next_dep = math.inf
    area += n * (t_end - last)

    L_hat = area / t_end
    lam_hat = n_dep / t_end
    W_hat = sum_w / n_dep
    return L_hat, lam_hat, W_hat, n_dep
