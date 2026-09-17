"""TD(λ) 的算法内核:19 状态随机游走、n 步回报、λ-回报、前向/后向视图、迹。

教材依据:Sutton & Barto《Reinforcement Learning: An Introduction》1st ed. 在线版第 7 章
(node73 §7.1 n-step TD、node74 §7.2 前向视图、node75 §7.3 后向视图、node76 §7.4 等价性、
node80 §7.8 替换迹)。公式编号沿用教材。

本文件只放「算法本身」;实验与断言在 `main.py`。

记号约定:一条轨迹用等长的两个列表表示 —— `states[t]` 是 t 时刻所在的**非终止**状态,
`rewards[t]` 是离开该状态后拿到的奖励。因此 `states[t+1]` 不存在时表示"下一步进了终止态",
此时自举项取 0。
"""

import math
import random


# --------------------------------------------------------------------------
# 19 状态随机游走(教材 Figure 7.2 的经典例子)
# --------------------------------------------------------------------------


class RandomWalk:
    """状态 1..n(0 与 n+1 为终止态),每步等概率左右移动一步。

    左端终止奖励 0、右端终止奖励 1,其余步奖励 0;起点在正中。
    真实值函数 V(s) = s/(n+1)(从 s 出发到达右端的概率)。
    """

    def __init__(self, n=19, rng=None):
        self.n = n
        self.rng = rng or random.Random(0)

    def true_values(self):
        return {s: s / (self.n + 1.0) for s in range(1, self.n + 1)}

    def episode(self):
        """返回 (states, rewards):states[t] 为 t 时刻的非终止状态,rewards[t] 为当步奖励。"""
        s = (self.n + 1) // 2
        states, rewards = [], []
        while True:
            states.append(s)
            s += 1 if self.rng.random() < 0.5 else -1
            if s == 0:
                rewards.append(0.0)
                return states, rewards
            if s == self.n + 1:
                rewards.append(1.0)
                return states, rewards
            rewards.append(0.0)


# --------------------------------------------------------------------------
# n 步回报(§7.1)
# --------------------------------------------------------------------------


def _next_value(V, states, t):
    """states[t+1] 存在则返回 V[states[t+1]],否则说明进了终止态,取 0。"""
    if t + 1 < len(states):
        return V.get(states[t + 1], 0.0)
    return 0.0


def n_step_return(rewards, V, states, gamma, n, t):
    """R_t^{(n)} = Σ_{k=0}^{n-1} γ^k r_{t+k} + γ^n V(s_{t+n})(越界即终止态,V=0)。"""
    acc = 0.0
    for k in range(n):
        j = t + k
        if j >= len(rewards):
            return acc          # 轨迹已结束,剩余奖励与终止值都是 0
        acc += (gamma ** k) * rewards[j]
    j = t + n
    v = V.get(states[j], 0.0) if j < len(states) else 0.0
    return acc + (gamma ** n) * v


def n_step_returns(rewards, V, states, gamma, n):
    return [n_step_return(rewards, V, states, gamma, n, t) for t in range(len(rewards))]


# --------------------------------------------------------------------------
# λ-回报:后向递推与定义式(§7.2 式 7.3)
# --------------------------------------------------------------------------


def lambda_returns(rewards, V, states, gamma, lam):
    """后向递推 gl_t = r_t + γ[(1−λ)V(s_{t+1}) + λ gl_{t+1}],gl_T = 0。"""
    T = len(rewards)
    gl = [0.0] * (T + 1)
    for t in range(T - 1, -1, -1):
        v_next = _next_value(V, states, t)
        gl[t] = rewards[t] + gamma * ((1.0 - lam) * v_next + lam * gl[t + 1])
    return gl[:T]


def lambda_returns_direct(rewards, V, states, gamma, lam, k_max=None):
    """(7.3) 定义式:(1−λ)Σ_{k=1}^{m−1}λ^{k−1}R^{(k)} + λ^{m−1}R^{(m)},m = k_max 或到轨迹末。

    注意最后一项**不再乘** (1−λ) —— 这是最容易写错的地方。有限视界下 m 取到轨迹末尾,
    此时 R^{(m)} 就是完整折扣回报。
    """
    T = len(rewards)
    out = []
    for t in range(T):
        m = T - t if k_max is None else min(k_max, T - t)
        acc = 0.0
        for k in range(1, m):
            acc += (lam ** (k - 1)) * n_step_return(rewards, V, states, gamma, k, t)
        acc *= (1.0 - lam)
        acc += (lam ** (m - 1)) * n_step_return(rewards, V, states, gamma, m, t)
        out.append(acc)
    return out


# --------------------------------------------------------------------------
# 前向视图 / 后向视图(§7.3–7.4)
# --------------------------------------------------------------------------


def forward_lambda_increment(V, states, rewards, alpha, gamma, lam):
    """前向视图:用 λ-回报当目标,把整条轨迹的增量**按状态累加**后返回(V 不被修改)。

    返回 dict{状态: 总增量},便于与后向视图逐状态比较。
    """
    gl = lambda_returns(rewards, V, states, gamma, lam)
    acc = {}
    for t, s in enumerate(states):
        acc[s] = acc.get(s, 0.0) + alpha * (gl[t] - V[s])
    return acc


def td_lambda_offline(V, states, rewards, alpha, gamma, lam, trace="accumulating"):
    """后向视图(离线):跑完整条轨迹,用资格迹把每个 δ 散回最近访问过的状态。

    返回 (dict{状态: 总增量}, 迹的最大值)。V 不被修改,便于与前向视图对照。
    trace ∈ {"accumulating", "replacing"}。
    """
    T = len(rewards)
    e = {s: 0.0 for s in V}
    acc = {}
    mt = 0.0
    for t in range(T):
        s = states[t]
        v_next = _next_value(V, states, t)
        delta = rewards[t] + gamma * v_next - V[s]
        for k in e:
            e[k] *= gamma * lam                 # (7.5) 前半:所有迹衰减
        if trace == "accumulating":
            e[s] = e.get(s, 0.0) + 1.0          # (7.5) 后半:累积迹自增
        else:
            e[s] = 1.0                          # (7.16) 替换迹:置 1
        for k in e:
            if e[k]:
                acc[k] = acc.get(k, 0.0) + alpha * delta * e[k]   # (7.7)
        mt = max(mt, max(e.values()))
    return acc, mt


def td_zero_update(V, states, rewards, alpha, gamma):
    """TD(0) 的对照实现:每个 δ 只更新**当前**状态。"""
    acc = {}
    for t, s in enumerate(states):
        delta = rewards[t] + gamma * _next_value(V, states, t) - V[s]
        acc[s] = acc.get(s, 0.0) + alpha * delta
    return acc


def mc_update(V, states, rewards, alpha, gamma):
    """每次访问蒙特卡洛的对照实现:目标换成完整的折扣回报 G_t。"""
    T = len(rewards)
    G = [0.0] * (T + 1)
    for t in range(T - 1, -1, -1):
        G[t] = rewards[t] + gamma * G[t + 1]
    acc = {}
    for t, s in enumerate(states):
        acc[s] = acc.get(s, 0.0) + alpha * (G[t] - V[s])
    return acc


# --------------------------------------------------------------------------
# 训练循环与误差度量
# --------------------------------------------------------------------------


def rms_error(V, true_v):
    return math.sqrt(sum((V[s] - true_v[s]) ** 2 for s in true_v) / len(true_v))


def train_td_lambda(n=19, episodes=100, alpha=0.05, gamma=1.0, lam=0.9, seed=0,
                    trace="accumulating", predictor="td_lambda", n_step=1):
    """在随机游走上离线训练;predictor ∈ {td_lambda, n_step};返回 (V, 迹最大值)。"""
    rw = RandomWalk(n=n, rng=random.Random(seed))
    V = {s: 0.5 for s in range(1, n + 1)}
    mt = 0.0
    for _ in range(episodes):
        states, rewards = rw.episode()
        if predictor == "n_step":
            gl = n_step_returns(rewards, V, states, gamma, n_step)
            acc = {}
            for t, s in enumerate(states):
                acc[s] = acc.get(s, 0.0) + (gl[t] - V[s])
            upd = {k: alpha * v for k, v in acc.items()}
        else:
            upd, mt_ep = td_lambda_offline(V, states, rewards, alpha, gamma, lam, trace)
            mt = max(mt, mt_ep)
        for k, dv in upd.items():
            V[k] += dv
    return V, mt
