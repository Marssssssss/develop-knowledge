"""SAC 的数学内核:软贝尔曼方程、软策略迭代、稳定 log-sum-exp、精确策略评估。

表格版,不引入神经网络 —— 这样「最大熵目标到底改变了什么」可以逐状态精确核对。
温度自动调节与实验断言在 `main.py`。

论文依据:
  - Haarnoja et al., Soft Actor-Critic(arXiv:1801.01290)§4.1 软策略迭代
  - Haarnoja et al., SAC with a Stochastic Actor(arXiv:1812.05905)§5 温度自动调节
  - Spinning Up: 熵正则化值函数定义、clipped double-Q、目标网络 polyak 平均
"""

import math
import random

GAMMA = 0.95
GRID = 5
GOAL_REWARD = 20.0   # 目标奖励要与"熵预算"同量级:α·ln|A|/(1-γ) 否则熵项会压倒回报
GOAL = GRID * GRID - 1
N = GRID * GRID
N_ACTIONS = 4

# --------------------------------------------------------------------------
# 网格世界(确定性转移:动作 0/1/2/3 = 上/下/右/左;越界原地不动)
# --------------------------------------------------------------------------


def move(s, a, n=GRID):
    """动作 0=上(row+1) 1=下(row-1) 2=右(col+1) 3=左(col-1),越界则贴边。"""
    r, c = divmod(s, n)
    if a == 0:
        r = min(n - 1, r + 1)
    elif a == 1:
        r = max(0, r - 1)
    elif a == 2:
        c = min(n - 1, c + 1)
    else:
        c = max(0, c - 1)
    return r * n + c


def reward(s2, goal=GOAL):
    """只有进入终点才有奖励;终点是终止态,之后不再产生奖励。"""
    return GOAL_REWARD if s2 == goal else 0.0


# --------------------------------------------------------------------------
# log-sum-exp:稳定版 vs 朴素版
# --------------------------------------------------------------------------


def logsumexp(vals):
    """log Σ exp(x):先减最大值,避免 exp 上溢。"""
    m = max(vals)
    if m == float("-inf"):
        return m
    return m + math.log(sum(math.exp(x - m) for x in vals))


def naive_logsumexp(vals):
    """教科书定义式的直接翻译 —— α 很小时 Q/α 可达数百,这里必然 OverflowError。"""
    return math.log(sum(math.exp(x) for x in vals))


# --------------------------------------------------------------------------
# 软贝尔曼方程:软值迭代 / 软 Q / 软策略
# --------------------------------------------------------------------------


def _soft_q_of(s, a, V, gamma):
    """Q(s,a) = r + γV(s'),进入终止态时不自举。"""
    s2 = move(s, a)
    if s2 == GOAL:
        return GOAL_REWARD
    return reward(s2) + gamma * V[s2]


def soft_value_iteration(alpha, gamma=GAMMA, iters=6000, tol=1e-13):
    """V(s) = α·logΣ_a exp(Q(s,a)/α);α=0 时退化为 max_a Q(s,a)。终点终端 V=0。"""
    V = [0.0] * N
    for _ in range(iters):
        d = 0.0
        for s in range(N):
            if s == GOAL:
                continue
            qs = [_soft_q_of(s, a, V, gamma) for a in range(N_ACTIONS)]
            if alpha > 0.0:
                v = alpha * logsumexp([q / alpha for q in qs])
            else:
                v = max(qs)
            d = max(d, abs(v - V[s]))
            V[s] = v
        if d < tol:
            break
    return V


def soft_q_from(V, alpha, gamma=GAMMA):
    """由软值函数回填软 Q。"""
    return [[_soft_q_of(s, a, V, gamma) for a in range(N_ACTIONS)] for s in range(N)]


def soft_policy(Q, alpha):
    """π(a|s) = exp((Q(s,a) − V(s))/α) = softmax(Q/α);α=0 时取 argmax(并列取最小动作号)。"""
    pol = []
    for row in Q:
        if alpha <= 0.0:
            best = max(row)
            k = row.index(best)
            p = [0.0] * len(row)
            p[k] = 1.0
        else:
            z = logsumexp([q / alpha for q in row])
            p = [math.exp(q / alpha - z) for q in row]
        pol.append(p)
    return pol


def policy_entropy(pol):
    """所有状态上策略熵的**平均**(状态等权)。"""
    tot = 0.0
    for row in pol:
        for p in row:
            if p > 1e-15:
                tot -= p * math.log(p)
    return tot / len(pol)


# --------------------------------------------------------------------------
# 精确策略评估(高斯消元解 Bellman 期望方程)
# --------------------------------------------------------------------------


def gauss_solve(A, b):
    """列主元高斯消元解 A x = b(带部分主元选取)。"""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-14:
            raise ValueError("singular matrix")
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(col + 1, n):
            f = M[r][col] / pv
            if f:
                for c in range(col, n + 1):
                    M[r][c] -= f * M[col][c]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        acc = M[i][n] - sum(M[i][j] * x[j] for j in range(i + 1, n))
        x[i] = acc / M[i][i]
    return x


def exact_eval(pol, gamma=GAMMA):
    """精确 V^π:解 V = r^π + γ P^π V(终点为终止态,V=0)。"""
    A = [[0.0] * N for _ in range(N)]
    b = [0.0] * N
    for s in range(N):
        A[s][s] = 1.0
        if s == GOAL:
            continue
        for a in range(N_ACTIONS):
            p = pol[s][a]
            if p == 0.0:
                continue
            s2 = move(s, a)
            if s2 == GOAL:
                b[s] += p * GOAL_REWARD
            else:
                A[s][s2] -= gamma * p
    return gauss_solve(A, b)


# --------------------------------------------------------------------------
# 表格软 Q 学习(off-policy + 经验回放 + polyak 目标网络)
# --------------------------------------------------------------------------


def soft_q_learning(alpha, episodes=4000, lr=0.5, gamma=GAMMA, batch=8, seed=0,
                    tau=0.05, eps=0.1, capacity=5000, warmup=200):
    """期望形式目标的软 Q 学习:y = r + γ·α·logΣ_{a'}exp(Q_t(s',a')/α)。

    - off-policy:经验回放里的转移由 ε-greedy 行为策略产生,目标却用软最优备份;
    - 目标网络 Q_t 用 **polyak** 更新(Q_t ← τQ + (1−τ)Q_t),硬拷贝会留下滞后偏差;
    - α=0 时 V 退化为 max,算法即普通 Q-learning。
    """
    rng = random.Random(seed)
    Q = [[0.0] * N_ACTIONS for _ in range(N)]
    Qt = [[0.0] * N_ACTIONS for _ in range(N)]
    buf = []
    for _ in range(episodes):
        s = rng.randrange(N - 1)
        for _ in range(30):
            if rng.random() < eps:
                a = rng.randrange(N_ACTIONS)
            else:
                a = max(range(N_ACTIONS), key=lambda i: Q[s][i])
            s2 = move(s, a)
            buf.append((s, a, reward(s2), s2, s2 == GOAL))
            if len(buf) > capacity:
                buf.pop(0)
            if len(buf) >= warmup:
                for _ in range(batch):
                    bs, ba, br, bs2, bd = buf[rng.randrange(len(buf))]
                    if bd:
                        y = br
                    elif alpha > 0.0:
                        y = br + gamma * alpha * logsumexp(
                            [q / alpha for q in Qt[bs2]])
                    else:
                        y = br + gamma * max(Qt[bs2])
                    Q[bs][ba] += lr * (y - Q[bs][ba])
            s = s2
            if s2 == GOAL:
                break
        for i in range(N):
            for j in range(N_ACTIONS):
                Qt[i][j] += tau * (Q[i][j] - Qt[i][j])
    return Q
