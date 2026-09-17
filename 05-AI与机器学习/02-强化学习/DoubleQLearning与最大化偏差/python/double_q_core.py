"""Double Q-learning 的可复现实验环境:随机奖励网格世界 + 表格算法。

只依赖标准库。本文件提供「学习」那一层:

  1. 可精确求解的随机奖励网格世界(原论文 §4.2 的设置);
  2. 表格 Q-learning 与 Double Q-learning(原论文 Algorithm 1);
  3. 按状态访问次数驱动的探索率 ε(s)=1/√n(s) 与两种学习率 α=1/n、1/n^0.8。

估计论那部分(偏差的闭式解、Theorem 1 的下界与紧性)在 `bias_theory.py`。

参考文献(见 README「参考资料」):
  - van Hasselt, Double Q-learning, NIPS 2010 §3 Algorithm 1 / §4.2 网格世界
"""

import math
import random

# ==========================================================================
# 2. 随机奖励网格世界(原文 §4.2)
# ==========================================================================


class StochasticGridWorld:
    """rows×cols 网格,起点左下、终点右上;出界则原地不动。

    非终止步奖励:−12 或 +10 等概率(期望 −1)。到达终点:每个动作都给 +5 并结束回合。
    最优策略 5 步结束(曼哈顿距离 5),故最优平均每步回报 = (4×(−1) + 5)/5 = +0.2,
    起点最优折扣值 = 5γ⁴ − Σ_{k=0}^{3} γ^k。
    """

    N_ACTIONS = 4
    # 0:上(row+1) 1:下(row−1) 2:右(col+1) 3:左(col−1)
    MOVES = ((1, 0), (-1, 0), (0, 1), (0, -1))

    def __init__(self, rows=3, cols=4, gamma=0.95, rng=None):
        self.rows = rows
        self.cols = cols
        self.gamma = gamma
        self.rng = rng or random.Random(0)
        self.n_states = rows * cols
        self.start = 0
        self.goal = rows * cols - 1
        self.lo, self.hi = -12.0, 10.0

    def idx(self, r, c):
        return r * self.cols + c

    def rc(self, s):
        return divmod(s, self.cols)

    def step(self, s, a):
        """返回 (s2, reward, done)。"""
        if s == self.goal:
            return s, 5.0, True
        r, c = self.rc(s)
        dr, dc = self.MOVES[a]
        r = min(self.rows - 1, max(0, r + dr))
        c = min(self.cols - 1, max(0, c + dc))
        s2 = self.idx(r, c)
        if s2 == self.goal:
            return s2, 5.0, True
        return s2, (self.lo if self.rng.random() < 0.5 else self.hi), False

    def true_start_value(self):
        """γ 折扣下起点最优值(精确值迭代,奖励用期望)。"""
        V = [0.0] * self.n_states
        for _ in range(20000):
            d = 0.0
            for s in range(self.n_states):
                if s == self.goal:
                    continue
                best = -1e18
                for a in range(self.N_ACTIONS):
                    r, c = self.rc(s)
                    dr, dc = self.MOVES[a]
                    r = min(self.rows - 1, max(0, r + dr))
                    c = min(self.cols - 1, max(0, c + dc))
                    s2 = self.idx(r, c)
                    val = 5.0 if s2 == self.goal else -1.0
                    best = max(best, val + self.gamma * V[s2])
                d = max(d, abs(best - V[s]))
                V[s] = best
            if d < 1e-12:
                break
        return V[self.start]


# ==========================================================================
# 3. 表格 Q-learning 与 Double Q-learning(原文 Algorithm 1)
# ==========================================================================


def alpha_linear(n):
    return 1.0 / n


def alpha_poly(n):
    return 1.0 / (n ** 0.8)


class QLearning:
    """单估计量:Q(s,a) ← Q(s,a) + α(r + γ max_a' Q(s',a') − Q(s,a))。"""

    def __init__(self, n_states, n_actions, gamma, alpha_fn, rng):
        self.Q = [[0.0] * n_actions for _ in range(n_states)]
        self.n = [[0] * n_actions for _ in range(n_states)]
        self.gamma = gamma
        self.alpha_fn = alpha_fn
        self.rng = rng

    def act(self, s, eps):
        if self.rng.random() < eps:
            return self.rng.randrange(len(self.Q[s]))
        row = self.Q[s]
        best, ba = -1e18, 0
        for i, v in enumerate(row):
            if v > best:
                best, ba = v, i
        return ba

    def update(self, s, a, r, s2, done):
        self.n[s][a] += 1
        if done:
            target = r
        else:
            target = r + self.gamma * max(self.Q[s2])
        self.Q[s][a] += self.alpha_fn(self.n[s][a]) * (target - self.Q[s][a])

    def max_at(self, s):
        return max(self.Q[s])


class DoubleQLearning:
    """Algorithm 1:两组 Q,一组选动作、另一组打分;每次随机更新其中一组。

      更新 A:a* = argmax_a QA(s',a),QA(s,a) += α(r + γ QB(s',a*) − QA(s,a))
      更新 B:b* = argmax_a QB(s',a),QB(s,a) += α(r + γ QA(s',b*) − QB(s,b))
    选动作时用两组均值的 ε-greedy(原文:"we calculated the average of the two Q values")。
    """

    def __init__(self, n_states, n_actions, gamma, alpha_fn, rng):
        self.QA = [[0.0] * n_actions for _ in range(n_states)]
        self.QB = [[0.0] * n_actions for _ in range(n_states)]
        self.nA = [[0] * n_actions for _ in range(n_states)]
        self.nB = [[0] * n_actions for _ in range(n_states)]
        self.gamma = gamma
        self.alpha_fn = alpha_fn
        self.rng = rng

    def act(self, s, eps):
        if self.rng.random() < eps:
            return self.rng.randrange(len(self.QA[s]))
        best, ba = -1e18, 0
        for i in range(len(self.QA[s])):
            v = 0.5 * (self.QA[s][i] + self.QB[s][i])
            if v > best:
                best, ba = v, i
        return ba

    @staticmethod
    def _argmax(row):
        best, ba = -1e18, 0
        for i, v in enumerate(row):
            if v > best:
                best, ba = v, i
        return ba

    def update(self, s, a, r, s2, done):
        if self.rng.random() < 0.5:
            self.nA[s][a] += 1
            q = self.QA[s][a]
            bootstrap = 0.0 if done else self.QB[s2][self._argmax(self.QA[s2])]
            target = r + self.gamma * bootstrap
            self.QA[s][a] = q + self.alpha_fn(self.nA[s][a]) * (target - q)
        else:
            self.nB[s][a] += 1
            q = self.QB[s][a]
            bootstrap = 0.0 if done else self.QA[s2][self._argmax(self.QB[s2])]
            target = r + self.gamma * bootstrap
            self.QB[s][a] = q + self.alpha_fn(self.nB[s][a]) * (target - q)

    def max_at(self, s):
        return max(self.QA[s])


def run_episode(env, agent, eps_fn, max_steps=200, record=None):
    """跑一个回合;eps_fn(s) 给出该状态的探索率。record 非空时按步追加 (reward, max_q_at_start)。"""
    s = env.start
    for _ in range(max_steps):
        a = agent.act(s, eps_fn(s))
        s2, r, done = env.step(s, a)
        agent.update(s, a, r, s2, done)
        if record is not None:
            record.append(r)
        s = s2
        if done:
            return True
    return False


def count_visits(agent, s):
    if isinstance(agent, DoubleQLearning):
        return [agent.nA[s][i] + agent.nB[s][i] for i in range(len(agent.QA[s]))]
    return agent.n[s]


def eps_by_visits(agent):
    """原文 §4.2:ε(s) = 1/√n(s),n(s) 为状态被访问次数。"""

    def f(s):
        n = sum(count_visits(agent, s))
        return min(1.0, 1.0 / math.sqrt(n + 1.0))

    return f
