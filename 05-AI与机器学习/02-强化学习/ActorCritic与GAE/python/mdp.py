"""精确可解的走廊 MDP:用于给 GAE 的偏差/方差提供"真值"。

11 个内部状态(1..n),左端 0 为陷阱(终止,-1),右端 n+1 为目标(终止,+1)。
动作 0=左、1=右,以 slip 概率被换成另一个动作(随机 MDP)。
因为状态数很少,π 的值函数、Q 函数、优势函数都能用高斯消元**精确**求出,
这样 GAE 估计量的偏差就不是"和真值比不了"的口径问题,而是可测的数字。

纯标准库,无第三方依赖。
"""


class CorridorMDP:
    """n 个内部状态 + 两个吸收终止态的走廊。"""

    N_ACTIONS = 2

    def __init__(self, n=11, slip=0.15, gamma=0.95, noise_sd=0.0):
        self.n = n
        self.slip = slip
        self.gamma = gamma
        self.noise_sd = noise_sd
        self.absorb_left = 0
        self.absorb_right = n + 1
        self.states = list(range(1, n + 1))
        self.terminals = [self.absorb_left, self.absorb_right]

    # ---- 动力学(枚举模型:纯规划用) ----

    def outcomes(self, s, a):
        """返回 [(s', p, r)]:在 s 执行 a 后可能到达的状态、概率与奖励。

        终止态为吸收态(自环、奖励 0);进入终止态的那一步才给 ±1。
        """
        if s in self.terminals:
            return [(s, 1.0, 0.0)]
        nxt = []
        for act, p in ((a, 1.0 - self.slip), (1 - a, self.slip)):
            target = s - 1 if act == 0 else s + 1
            if target == self.absorb_left:
                nxt.append((target, p, -1.0))
            elif target == self.absorb_right:
                nxt.append((target, p, 1.0))
            else:
                nxt.append((target, p, 0.0))
        merged = {}
        for st, p, r in nxt:
            key = (st, r)
            merged[key] = merged.get(key, 0.0) + p
        return [(st, p, r) for (st, r), p in merged.items()]

    def sample_step(self, s, a, rng):
        """采样一步转移。noise_sd > 0 时给每步奖励叠加零均值高斯噪声(期望值不变)。"""
        u, acc = rng.random(), 0.0
        noise = rng.gauss(0.0, self.noise_sd) if self.noise_sd > 0 else 0.0
        for st, p, r in self.outcomes(s, a):
            acc += p
            if u <= acc:
                return st, r + noise
        st, p, r = self.outcomes(s, a)[-1]
        return st, r + noise

    # ---- 精确策略评估 ----

    def policy_eval(self, pi):
        """解 (I - γP^π)V = r^π,返回 {s: V^π(s)}(含两个终止态,值为 0)。"""
        idx = {s: i for i, s in enumerate(self.terminals + self.states)}
        m = len(idx)
        A = [[0.0] * m for _ in range(m)]
        b = [0.0] * m
        for s in self.terminals + self.states:
            A[idx[s]][idx[s]] += 1.0
            if s in self.terminals:
                continue
            for a in range(self.N_ACTIONS):
                pa = pi(s, a)
                if pa == 0.0:
                    continue
                for s2, p, r in self.outcomes(s, a):
                    A[idx[s]][idx[s2]] -= self.gamma * pa * p
                    b[idx[s]] += pa * p * r
        sol = gauss_solve(A, b)
        return {s: sol[idx[s]] for s in idx}

    def q_values(self, pi, V):
        Q = {}
        for s in self.states:
            for a in range(self.N_ACTIONS):
                Q[(s, a)] = sum(p * (r + self.gamma * V[s2])
                                for s2, p, r in self.outcomes(s, a))
        return Q

    def advantage(self, pi, V, Q=None):
        Q = Q or self.q_values(pi, V)
        return {(s, a): Q[(s, a)] - V[s] for s in self.states for a in range(self.N_ACTIONS)}

    # ---- 轨迹采样 ----

    def rollout(self, pi, rng, start, horizon=40):
        """从 start 出发采样一条轨迹,返回 (states, actions, rewards, dones)。

        states/actions/rewards 等长;done[t] 表示第 t 步之后进入终止态。
        """
        s = start
        S, A, R, D = [], [], [], []
        for _ in range(horizon):
            a = 0 if rng.random() < pi(s, 0) else 1
            s2, r = self.sample_step(s, a, rng)
            S.append(s)
            A.append(a)
            R.append(r)
            done = s2 in self.terminals
            D.append(done)
            s = s2
            if done:
                break
        return S, A, R, D


def gauss_solve(A, b):
    """部分选主元的高斯消元(小规模稠密线性方程组)。"""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-14:
            raise ValueError("矩阵奇异")
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(n):
            if r == col or M[r][col] == 0.0:
                continue
            f = M[r][col] / pv
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def lstsq_linear_fit(xs, ys):
    """最小二乘拟合 y ≈ a + b x(2×2 正规方程,闭式解)。"""
    m = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    det = m * sxx - sx * sx
    b = (m * sxy - sx * sy) / det
    a = (sy - b * sx) / m
    return a, b
