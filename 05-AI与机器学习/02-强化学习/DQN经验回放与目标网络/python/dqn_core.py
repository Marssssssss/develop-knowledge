"""DQN 的最小核心组件:纯标准库实现的小型 MLP、网格世界环境、经验回放缓冲。

只依赖 Python 标准库(random / math),不引入 numpy / pytorch。目的是让
"经验回放(replay)"与"目标网络(target network)"这两个机制本身保持可读、可断言,
而不是被框架的 .fit() 封装掉。

参考文献(见 README「参考资料」):
  - Mnih et al., Playing Atari with Deep Reinforcement Learning (arXiv:1312.5602) §4/§4.1
  - van Hasselt et al., Deep RL with Double Q-learning (arXiv:1509.06461) §"Double DQN"
  - Sutton & Barto, RL: An Introduction (1st ed. 在线版) §8.5 Off-Policy Bootstrapping
"""

import math
import random

# --------------------------------------------------------------------------
# 1. 环境:带随机滑移的网格世界
# --------------------------------------------------------------------------


class GridWorld:
    """n×n 网格:走到 (n-1,n-1) 终止。

    动作 0/1/2/3 = 上/下/左/右。以 slip 概率滑到垂直方向(使环境成为随机 MDP)。
    到达目标奖励 +1,其余每步奖励 0;超过 max_steps 强制截断。
    回合从随机非目标格出发(DQN 的一个关键工程细节:固定起点会让"Q 与状态无关"
    成为自洽的不动点——见 README「注意事项」)。
    """

    N_ACTIONS = 4

    def __init__(self, n=5, slip=0.1, max_steps=40, rng=None,
                 goal_reward=1.0, step_reward=0.0, random_start=True):
        self.n = n
        self.slip = slip
        self.max_steps = max_steps
        self.rng = rng or random.Random(0)
        self.n_states = n * n
        self.goal = self.n_states - 1
        self.goal_reward = goal_reward
        self.step_reward = step_reward
        self.random_start = random_start
        self.steps = 0
        self.state = 0

    def reset(self):
        """随机非目标格出发(可关掉以便复现固定起点)。"""
        if self.random_start:
            s = self.rng.randrange(self.n_states - 1)
            self.state = s
        else:
            self.state = 0
        self.steps = 0
        return self.state

    def _move(self, s, a):
        r, c = divmod(s, self.n)
        if a == 0:
            r = max(0, r - 1)
        elif a == 1:
            r = min(self.n - 1, r + 1)
        elif a == 2:
            c = max(0, c - 1)
        else:
            c = min(self.n - 1, c + 1)
        return r * self.n + c

    def _slip_action(self, a):
        """滑移:在(上,下)与(左,右)两组之间随机换一个动作。"""
        if a in (0, 1):
            return 2 if self.rng.random() < 0.5 else 3
        return 0 if self.rng.random() < 0.5 else 1

    def step(self, a):
        """返回 (next_state, reward, done)。目标格是吸收态:再走一步仍终止、奖励 0。"""
        if self.state == self.goal:
            return self.goal, 0.0, True
        if self.rng.random() < self.slip:
            a = self._slip_action(a)
        self.state = self._move(self.state, a)
        self.steps += 1
        if self.state == self.goal:
            return self.state, self.goal_reward, True
        done = self.steps >= self.max_steps
        return self.state, self.step_reward, done


# --------------------------------------------------------------------------
# 2. 经验回放缓冲
# --------------------------------------------------------------------------


class ReplayBuffer:
    """容量固定的环形缓冲,均匀随机采样 minibatch。

    arXiv:1312.5602 §4:「we store the agent's experiences at each time-step ...
    pooled over many episodes into a replay memory」,并「samples uniformly at
    random from D when performing updates」;超出容量后覆盖最旧的转移。
    """

    def __init__(self, capacity, rng=None):
        self.capacity = capacity
        self.rng = rng or random.Random(0)
        self.data = []
        self.pos = 0

    def __len__(self):
        return len(self.data)

    def push(self, s, a, r, s2, done):
        if len(self.data) < self.capacity:
            self.data.append((s, a, r, s2, done))
        else:
            self.data[self.pos] = (s, a, r, s2, done)
            self.pos = (self.pos + 1) % self.capacity

    def sample(self, batch):
        return [self.data[self.rng.randrange(len(self.data))] for _ in range(batch)]


# --------------------------------------------------------------------------
# 3. 两层 MLP(手写前向 + 反向),semi-gradient 只更新被选中动作的 Q
# --------------------------------------------------------------------------


class MLP:
    """n_in -> n_hidden(relu / tanh) -> n_out(线性)。

    损失 = 0.5 * (Q(s,a;θ) - y)²,只对被选中的动作 a 求梯度(Q-learning 的
    semi-gradient 形式),等价于把其余输出维的误差置零。

    hid_bias=False 时隐藏层不带偏置(对 one-hot 输入而言,共享偏置会把所有状态的
    隐藏单元"一起推死",这是本 demo 开发期实测到的坑,见 README「注意事项」)。
    """

    def __init__(self, n_in, n_hidden, n_out, lr=0.05, rng=None,
                 act="relu", hid_bias=False):
        rng = rng or random.Random(0)
        self.lr = lr
        self.act = act
        self.hid_bias = hid_bias
        k1 = math.sqrt(1.0 / n_in)
        k2 = math.sqrt(1.0 / n_hidden)
        self.w1 = [[rng.uniform(-k1, k1) for _ in range(n_in)] for _ in range(n_hidden)]
        self.b1 = [0.0] * n_hidden
        self.w2 = [[rng.uniform(-k2, k2) for _ in range(n_hidden)] for _ in range(n_out)]
        self.b2 = [0.0] * n_out

    def _act(self, v):
        return math.tanh(v) if self.act == "tanh" else (v if v > 0.0 else 0.0)

    def _dact(self, pre, h):
        return (1.0 - h * h) if self.act == "tanh" else (1.0 if pre > 0.0 else 0.0)

    # ---- 通用(稠密)前向 ----

    def forward(self, x):
        pre = []
        h = []
        for j, row in enumerate(self.w1):
            acc = self.b1[j] if self.hid_bias else 0.0
            for i, xi in enumerate(x):
                if xi:
                    acc += row[i] * xi
            pre.append(acc)
            h.append(self._act(acc))
        q = []
        for j, row in enumerate(self.w2):
            acc = self.b2[j]
            for i, hi in enumerate(h):
                if hi:
                    acc += row[i] * hi
            q.append(acc)
        return pre, h, q

    def predict(self, x):
        return self.forward(x)[2]

    def argmax(self, x):
        q = self.predict(x)
        best = 0
        for i in range(1, len(q)):
            if q[i] > q[best]:
                best = i
        return best

    # ---- one-hot 输入的快速路径(网格世界的状态编码是 one-hot) ----

    def forward_onehot(self, s):
        pre = []
        h = []
        for j, row in enumerate(self.w1):
            acc = row[s] + (self.b1[j] if self.hid_bias else 0.0)
            pre.append(acc)
            h.append(self._act(acc))
        q = []
        for j, row in enumerate(self.w2):
            acc = self.b2[j]
            for i, hi in enumerate(h):
                acc += row[i] * hi
            q.append(acc)
        return pre, h, q

    def argmax_onehot(self, s):
        q = self.forward_onehot(s)[2]
        best = 0
        for i in range(1, len(q)):
            if q[i] > q[best]:
                best = i
        return best

    # ---- 反向 ----

    def sgd_step(self, x, pre, h, dq, lr=None):
        """稠密输入的 SGD 一步。pre / h 必须来自同一次前向(见 sgd_step_onehot 说明)。"""
        lr = self.lr if lr is None else lr
        dh = self._hidden_grad(pre, h, dq, lr)
        for j in range(len(self.w1)):
            gj = dh[j]
            if self.hid_bias:
                self.b1[j] -= lr * gj
            if not gj:
                continue
            for i, xi in enumerate(x):
                if xi:
                    self.w1[j][i] -= lr * gj * xi

    def sgd_step_onehot(self, s, pre, h, dq, lr=None):
        """one-hot 输入(第 s 位为 1)的 SGD 一步。

        注意:pre / h 必须来自**同一次**前向——对同一网络的其它前向会覆盖调用方
        手里的变量,所以调用方要在前向后立刻快照(本 demo 中的做法)。
        """
        lr = self.lr if lr is None else lr
        dh = self._hidden_grad(pre, h, dq, lr)
        for j in range(len(self.w1)):
            gj = dh[j]
            if self.hid_bias:
                self.b1[j] -= lr * gj
            if gj:
                self.w1[j][s] -= lr * gj

    def _hidden_grad(self, pre, h, dq, lr):
        """反向传播到隐藏层,并就地完成输出层(w2/b2)的更新。"""
        dh = [0.0] * len(h)
        for j, row in enumerate(self.w2):
            g = dq[j]
            if g:
                for i in range(len(h)):
                    dh[i] += row[i] * g
            self.b2[j] -= lr * g
            if g:
                for i in range(len(h)):
                    row[i] -= lr * g * h[i]
        for i in range(len(h)):
            dh[i] *= self._dact(pre[i], h[i])
        return dh

    def copy_from(self, other):
        self.w1 = [row[:] for row in other.w1]
        self.b1 = other.b1[:]
        self.w2 = [row[:] for row in other.w2]
        self.b2 = other.b2[:]

    def max_abs_weight(self):
        m = 0.0
        for row in self.w1 + self.w2:
            for v in row:
                m = max(m, abs(v))
        for v in self.b1 + self.b2:
            m = max(m, abs(v))
        return m
