# Q-Learning 表格方法

## 简介

Q-Learning 是强化学习中最基础的无模型（model-free）算法之一，由 Watkins 于 1989 年提出，并在 Watkins & Dayan (1992) 中证明了收敛性。它的核心思想是：**不需要知道环境的转移函数 P(s'|s,a)，通过与环境交互收集的经验直接学习最优动作值函数 Q*(s,a)**。

- **Q 表**：以状态-动作对 (s,a) 为索引的表格，存储每个 (s,a) 的期望累积回报
- **Bellman 更新**：用采样到的下一状态 s' 替代对 s' 的期望求和
- **epsilon-greedy 探索**：以概率 1-epsilon 选当前最优动作，以概率 epsilon 随机探索
- **自纠正性**：被高估的动作会被探索后自动降低 Q 值

历史背景：Watkins 在其 1989 年博士论文中提出 Q-Learning，Watkins & Dayan (1992) 在 *Machine Learning* 期刊上给出收敛性证明。这是深度强化学习复兴的基础——DQN (Mnih et al., 2013) 即为 Q-Learning + 深度神经网络的组合。

## 原理详解

### 1. Bellman 最优方程（值迭代形式）

值迭代需要知道完整 MDP（转移函数 T(s,a,s')=P(s'|s,a)）：

```
Q_{k+1}(s,a) = r(s,a) + γ * Σ_{s'} P(s'|s,a) * max_{a'} Q_k(s', a')
```

Q-Learning 的关键替换：**用实际采样的 s' 替代 Σ_{s'} P(s'|s,a) 的期望**。

### 2. Q-Learning 更新规则

```
Q(s_t, a_t) ← Q(s_t, a_t) + α * [r_t + γ * max_{a'} Q(s_{t+1}, a') - Q(s_t, a_t)]
```

- `α`：学习率（步长），控制每次更新的幅度
- `γ`：折扣因子，未来奖励的折扣程度
- `max_{a'} Q(s_{t+1}, a')`：下一状态的最大 Q 值（off-policy 特征——用 max 而非实际采取的动作）
- 终止状态：`target = r_t`（不再有未来回报）

展开形式（d2l.ai §17.3 公式 17.3.4）：

```
Q(s_t, a_t) ← (1-α) * Q(s_t, a_t) + α * [r_t + γ * (1 - 1[terminal]) * max_{a'} Q(s_{t+1}, a')]
```

### 3. epsilon-greedy 探索策略

```
π_e(a|s) = argmax_{a'} Q(s, a')   with prob (1-ε)
          = uniform(A)              with prob ε
```

- `ε` 太小 → 探索不足，可能陷入局部最优
- `ε` 太大 → 接近随机策略，收敛慢
- 也可用 softmax 探索：`π(a|s) = exp(Q(s,a)/T) / Σ exp(Q(s,a')/T)`

### 4. 数据流

```
┌──────────────────────────────────────────────────────┐
│  每个 episode:                                        │
│    s_0 → ε-greedy 选 a_0 → 环境 → (s_1, r, done)      │
│    Q[s_0, a_0] += α * (r + γ*max Q[s_1] - Q[s_0,a_0])│
│    s_1 → ε-greedy 选 a_1 → 环境 → (s_2, r, done)      │
│    Q[s_1, a_1] += α * (r + γ*max Q[s_2] - Q[s_1,a_1])│
│    ...                                               │
│    直到 done (到达终点或掉入洞)                         │
│                                                      │
│  提取策略: π*(s) = argmax_a Q(s,a)                     │
└──────────────────────────────────────────────────────┘
```

### 5. 自纠正性（Self-correcting Property）

如果某个 (s,a) 的 Q 值被高估：
1. epsilon-greedy 会更频繁地选择 a
2. 实际执行后发现未来奖励不高
3. Q(s,a) 被下调（`r + γ*max Q(s') < Q(s,a)`）
4. 下次访问 s 时 a 被选中的概率降低

这种"探索 → 纠错 → 再探索"的循环使 Q-Learning 即使从随机策略起步也能收敛到最优策略。

## 对比 / 选型

| 算法 | 需要知道 MDP? | on/off-policy | 收敛条件 | 适用场景 |
| --- | --- | --- | --- | --- |
| 值迭代 | ✅ 需要 P(s'|s,a) | N/A | 已知 MDP 即收敛 | 棋类、规划 |
| Q-Learning | ❌ 不需要 | off-policy | 所有 (s,a) 被无限次访问 | 表格型 RL |
| SARSA | ❌ 不需要 | on-policy | 同上 | 在线学习 |
| DQN | ❌ 不需要 | off-policy | 近似收敛 | 高维状态空间 |

Q-Learning 是 off-policy 算法：更新中使用 `max_{a'} Q(s',a')`（最优策略的假设），而非实际执行的动作。

## 环境准备

- 操作系统：跨平台
- 语言版本：Python 3.8+
- 依赖：无（纯标准库实现）

## 运行方式

```bash
python3 q_learning.py
```

## 关键代码片段

```python
# Q-Learning 核心更新
def q_learning_update(Q, state, action, reward, next_state, done, alpha, gamma):
    if done:
        target = reward
    else:
        target = reward + gamma * max(Q[next_state])
    Q[state][action] += alpha * (target - Q[state][action])

# epsilon-greedy 探索
def epsilon_greedy(Q, state, epsilon):
    if random.random() < epsilon:
        return random.randint(0, N_ACTIONS - 1)
    return max(range(N_ACTIONS), key=lambda a: Q[state][a])
```

## 性能与边界

- **时间复杂度**：O(|S| × |A| × episodes × steps)，表格方法中每个 (s,a) 更新 O(1)
- **收敛条件**（Watkins & Dayan 1992）：所有 (s,a) 对被无限次访问 + 学习率递减
- **表格规模上限**：|S| × |A|，对 4×4 GridWorld = 16×4 = 64 个条目
- **折扣因子 γ=0** 时：Q 值退化为即时奖励 r(s,a)，无法传播目标奖励

## 注意事项与常见坑

1. **epsilon 过大导致不收敛**：ε=0.9 时几乎随机探索，1000 episode 仍可能 success rate < 5%。实践中常用 ε 衰减策略（从 1.0 线性降至 0.01）
2. **gamma=0 时无法学习**：折扣因子为 0 意味着只关心即时奖励，目标奖励无法从终点传播回来。GridWorld 中只有终点有 +1 奖励，gamma=0 时所有 Q 值保持 0
3. **off-policy vs on-policy**：Q-Learning 是 off-policy（用 max 更新），SARSA 是 on-policy（用实际采取的 a' 更新）。在随机环境中两者可能学到不同策略
4. **学习率 α 的选择**：α 过大 → Q 值震荡；α 过小 → 收敛极慢。实践中 α=0.1 是常用起始值
5. **终端状态处理**：到达终端状态时 target = r（不加 γ*max Q），否则 Q 值无法收敛

## 参考资料（实际阅读过的权威来源）

- [17.3. Q-Learning — Dive into Deep Learning](https://d2l.ai/chapter_reinforcement-learning/qlearning.html) — d2l.ai §17.3 全文，含 Q-Learning 更新规则公式 17.3.2-17.3.6、优化问题推导、探索策略、自纠正性证明、FrozenLake 完整实现
- [17.1. Markov Decision Process — Dive into Deep Learning](https://d2l.ai/chapter_reinforcement-learning/mdp.html) — d2l.ai §17.1 MDP 定义、折扣回报 R(τ)=Σγ^t·r_t、轨迹与转移函数
- [17.2. Value Iteration — Dive into Deep Learning](https://d2l.ai/chapter_reinforcement-learning/value-iter.html) — d2l.ai §17.2 值迭代公式 17.2.10-17.2.12（Q-Learning 的前身，需完整 MDP）
- Watkins, C.J.C.H. & Dayan, P. (1992). Q-learning. *Machine Learning*, 8(3-4), 279-292. — 收敛性证明原文引用（经 d2l.ai 引用确认）
