"""M/M/c、Erlang C 与容量拐点的最小可验证实现。

公式口径全部来自实读资料：
  * Wikipedia《Erlang (unit)》：Erlang B 的两种写法与稳定递推
        B(E,0) = 1
        B(E,j) = E*B(E,j-1) / (E*B(E,j-1) + j)
        1/B(E,j) = 1 + (j/E) * (1/B(E,j-1))
    Erlang C（= 到达需要排队的概率）
        P_w = [ (E^m/m!) * m/(m-E) ] / [ Σ_{i=0}^{m-1} E^i/i! + (E^m/m!) * m/(m-E) ]
  * Wikipedia《M/M/c queue》：
        rho = lambda/(c*mu) < 1
        pi_0 = [ Σ_{k=0}^{c-1} (c*rho)^k/k! + (c*rho)^c/c! * 1/(1-rho) ]^{-1}
        W  = C(c, lambda/mu)/(c*mu - lambda) + 1/mu
        Wq = C(c, lambda/mu)/(c*mu - lambda)
        L  = rho/(1-rho) * C(c, lambda/mu) + c*rho
        Lq = lambda * Wq
  * Wikipedia《Pollaczek–Khinchine formula》（M/G/1）：
        L  = rho + (rho^2 + lambda^2 Var(S)) / (2(1-rho))
        W' = (rho + lambda*mu*Var(S)) / (2(mu-lambda))      # 排队等待
        W  = W' + 1/mu
  * Wikipedia《M/M/c queue》有限容量段（此处取 c=1 的 M/M/1/K）：
        pi_0 = [ Σ_{k=0}^{K} rho^k ]^{-1}
        lambda_a = lambda * (1 - p_K)
"""

from __future__ import annotations

import math


# -------------------------------------------------------------------- Erlang B
def erlang_b(E: float, m: int) -> float:
    """Erlang B（阻塞概率）：直接求和式。"""
    if E < 0:
        raise ValueError("E 必须非负")
    if m < 0:
        raise ValueError("m 必须非负")
    num = E ** m / math.factorial(m)
    den = sum(E ** i / math.factorial(i) for i in range(m + 1))
    return num / den


def erlang_b_recursive(E: float, m: int) -> float:
    """Erlang B：维基给出的稳定递推 B(E,j) = E*B(E,j-1)/(E*B(E,j-1)+j)。"""
    if E < 0 or m < 0:
        raise ValueError("E、m 必须非负")
    b = 1.0                       # B(E, 0) = 1
    for j in range(1, m + 1):
        b = E * b / (E * b + j)
    return b


def erlang_b_inverse_recursive(E: float, m: int) -> float:
    """Erlang B：倒数递推 1/B(E,j) = 1 + (j/E)*(1/B(E,j-1))，E=0 时退化为 0。"""
    if E == 0:
        return 0.0 if m > 0 else 1.0
    inv = 1.0                     # 1/B(E,0) = 1
    for j in range(1, m + 1):
        inv = 1.0 + (j / E) * inv
    return 1.0 / inv


# -------------------------------------------------------------------- Erlang C
def erlang_c(E: float, m: int) -> float:
    """Erlang C（等待概率）：维基的闭式。要求 E < m（否则 ρ≥1 不稳定）。"""
    if E < 0 or m <= 0:
        raise ValueError("E 必须非负且 m 为正")
    if E >= m:
        raise ValueError(f"不稳定：E={E} 必须小于 m={m}")
    tail = E ** m / math.factorial(m) * (m / (m - E))
    head = sum(E ** i / math.factorial(i) for i in range(m))
    return tail / (head + tail)


def erlang_c_via_b(E: float, m: int) -> float:
    """Erlang C 与 Erlang B 的关系式：C = B / (1 − ρ(1 − B))，ρ = E/m。"""
    b = erlang_b_recursive(E, m)
    rho = E / m
    return b / (1.0 - rho * (1.0 - b))


# ----------------------------------------------------------------------- M/M/c
class MMC:
    def __init__(self, lam: float, mu: float, c: int):
        if lam <= 0 or mu <= 0 or c <= 0:
            raise ValueError("lam、mu 必须为正，c 必须为正整数")
        if lam >= c * mu:
            raise ValueError(f"不稳定：lam={lam} 必须小于 c*mu={c*mu}")
        self.lam = lam
        self.mu = mu
        self.c = c

    @property
    def offered_load(self) -> float:
        """以 erlang 计的 offered traffic E = lambda/mu。"""
        return self.lam / self.mu

    @property
    def rho(self) -> float:
        return self.lam / (self.c * self.mu)

    @property
    def C(self) -> float:
        """到达时需要排队的概率（Erlang C）。"""
        return erlang_c(self.offered_load, self.c)

    @property
    def pi0(self) -> float:
        a = self.c * self.rho
        head = sum(a ** k / math.factorial(k) for k in range(self.c))
        tail = a ** self.c / math.factorial(self.c) / (1.0 - self.rho)
        return 1.0 / (head + tail)

    @property
    def Wq(self) -> float:
        return self.C / (self.c * self.mu - self.lam)

    @property
    def W(self) -> float:
        return self.Wq + 1.0 / self.mu

    @property
    def Lq(self) -> float:
        return self.lam * self.Wq

    @property
    def L(self) -> float:
        return self.lam * self.W

    @property
    def busy_servers(self) -> float:
        """平均忙碌的服务台数 = E = lambda/mu（也等于 L − Lq）。"""
        return self.offered_load


# ---------------------------------------------------------------- M/G/1 (P-K)
def mg1(lam: float, mu: float, var_s: float):
    """M/G/1 的 Pollaczek–Khinchine 结果，返回 (Wq, W, L)。"""
    if lam <= 0 or mu <= 0 or var_s < 0:
        raise ValueError("参数非法")
    rho = lam / mu
    if rho >= 1:
        raise ValueError(f"不稳定：rho={rho}")
    Wq = (rho + lam * mu * var_s) / (2 * (mu - lam))
    W = Wq + 1.0 / mu
    return Wq, W, lam * W


def mg1_L_via_pk(lam: float, mu: float, var_s: float) -> float:
    """维基的 L 式：L = rho + (rho^2 + lambda^2 Var(S))/(2(1-rho))。"""
    rho = lam / mu
    return rho + (rho * rho + lam * lam * var_s) / (2 * (1 - rho))


def exp_service_var(mu: float) -> float:
    return 1.0 / (mu * mu)


# -------------------------------------------------------------------- M/M/1/K
class MM1K:
    """有限容量单服务台：客满即丢弃。"""

    def __init__(self, lam: float, mu: float, K: int):
        if lam <= 0 or mu <= 0 or K <= 0:
            raise ValueError("参数非法")
        self.lam = lam
        self.mu = mu
        self.K = K
        self.rho = lam / mu

    @property
    def pi0(self) -> float:
        if abs(self.rho - 1.0) < 1e-15:
            return 1.0 / (self.K + 1)
        return (1.0 - self.rho) / (1.0 - self.rho ** (self.K + 1))

    def pi(self, k: int) -> float:
        if k > self.K:
            return 0.0
        return self.pi0 * self.rho ** k

    @property
    def p_block(self) -> float:
        return self.pi(self.K)

    @property
    def lambda_a(self) -> float:
        """有效到达率 = lambda*(1 - p_K)。"""
        return self.lam * (1.0 - self.p_block)

    @property
    def throughput(self) -> float:
        """离开率 = mu*(1 - pi_0)。"""
        return self.mu * (1.0 - self.pi0)

    @property
    def L(self) -> float:
        return sum(k * self.pi(k) for k in range(self.K + 1))

    @property
    def W(self) -> float:
        return self.L / self.lambda_a


# ------------------------------------------------------------- 服务台配置对比
def compare_configurations(lam: float, mu: float, c: int):
    """比较「c 台各 μ」与「1 台 cμ」：返回 (W_mmc, W_fast_single)。"""
    mmc = MMC(lam, mu, c)
    fast = MMC(lam, c * mu, 1)
    return mmc.W, fast.W
