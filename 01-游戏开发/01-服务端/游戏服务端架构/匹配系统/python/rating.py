#!/usr/bin/env python3
"""评分模型: Elo 与 TrueSkill 的最小实现(被 matchmaking.py 引用).

权威依据(见 README 参考资料):
  * Elo: 单一评分 + 400 分标尺 + K 因子; 期望胜率 E = 1/(1+10^((Rb-Ra)/400))。
  * TrueSkill(微软研究院): 技能用**两个**数刻画 —— 均值 mu 与不确定性 sigma;
    新玩家 mu=25, sigma=8.333, 展示分 = mu - 3*sigma = 0("技能可能在 0~50 之间");
    每局比赛提供信息 -> sigma 通常下降, 但赛前会微增, 因此 sigma **永不归零**;
    匹配质量 = (虚拟)平局概率, 取值 0~1; "即使 mu 相同, sigma 大的一方也会让质量明显小于 1"。
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- 常量
MU0, SIGMA0 = 25.0, 25.0 / 3.0        # 新玩家: mu=25, sigma=8.333
BETA = SIGMA0 / 2.0                   # 表现围绕技能波动的方差参数 beta
TAU = SIGMA0 / 100.0                  # 赛前 sigma 微增量("动量", 保证 sigma 不归零)
K_DISPLAY = 3.0                       # 保守估计 mu - 3*sigma
ELO_K = 32.0


def phi(x: float) -> float:
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


def Phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class Rng:
    """线性同余发生器: 三语言实现同一序列, 保证跨语言结果可比。"""

    def __init__(self, seed: int) -> None:
        self.s = seed & 0xFFFFFFFF

    def u32(self) -> int:
        self.s = (1664525 * self.s + 1013904223) & 0xFFFFFFFF
        return self.s

    def u(self) -> float:
        return self.u32() / 4294967296.0

    def normal(self) -> float:
        """Irwin-Hall: 4 个均匀分布之和近似正态(均值 0, 标准差 1)。"""
        return (self.u() + self.u() + self.u() + self.u() - 2.0) * math.sqrt(3.0)


# =====================================================================
# 一、Elo: 一个数字 + 400 分标尺
# =====================================================================
def elo_expect(ra: float, rb: float) -> float:
    """A 对 B 的期望得分(胜率 + 平局的一半)。"""
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


def elo_update(ra: float, rb: float, score_a: float, k: float = ELO_K):
    ea = elo_expect(ra, rb)
    return ra + k * (score_a - ea), rb + k * ((1.0 - score_a) - (1.0 - ea))


# =====================================================================
# 二、TrueSkill: (mu, sigma) 与保守估计
# =====================================================================
def display(mu: float, sigma: float) -> float:
    return mu - K_DISPLAY * sigma


def ts_1v1(win, lose, beta: float = BETA, tau: float = TAU):
    """双人 1v1 贝叶斯更新(TrueSkill 标准两步: 先加动量再按结果更新)。"""
    mw, sw = win[0], math.sqrt(win[1] ** 2 + tau ** 2)
    ml, sl = lose[0], math.sqrt(lose[1] ** 2 + tau ** 2)
    c2 = 2.0 * beta * beta + sw * sw + sl * sl     # 总方差
    c = math.sqrt(c2)
    t = (mw - ml) / c
    v = phi(t) / Phi(t)                            # 截断正态的一阶矩比
    w = v * (v + t)                                # 二阶矩比
    mu_w = mw + sw * sw / c * v
    mu_l = ml - sl * sl / c * v
    sig_w = math.sqrt(sw * sw * (1.0 - sw * sw / c2 * w))
    sig_l = math.sqrt(sl * sl * (1.0 - sl * sl / c2 * w))
    return (mu_w, sig_w), (mu_l, sig_l)


def match_quality(a, b, beta: float = BETA) -> float:
    """匹配质量 = (虚拟)平局概率, 0(最差)~1(最好)。"""
    ma, sa = a
    mb, sb = b
    denom = 2.0 * beta * beta + sa * sa + sb * sb
    return math.sqrt(2.0 * beta * beta / denom) * math.exp(-(ma - mb) ** 2 / (2.0 * denom))

