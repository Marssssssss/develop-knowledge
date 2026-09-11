# REINFORCE 策略梯度算法

## 简介

REINFORCE 是最基础的政策梯度（Policy Gradient）算法，由 Williams (1992) 提出。与 Q-Learning 学习价值函数不同，REINFORCE **直接参数化策略 π_θ(a|s) 并通过梯度上升优化期望回报**。它是所有现代策略梯度方法（PPO、TRPO、A3C 等）的理论基础。

- **策略梯度定理**：∇J(π_θ) = E_{τ~π_θ}[Σ_t ∇_θ log π_θ(a_t|s_t) · A(s_t,a_t)]
- **对数导数技巧**（log-derivative trick）：将期望的梯度转化为对数概率的梯度，避免计算环境动力学
- **回报（returns/rewards-to-go）**：G_t = Σ_{k=t}^{T-1} γ^{k-t} · r_k，从 t 时刻开始的累积折扣回报
- **基线（baseline）方差缩减**：用 V(s) 减去回报，不改变梯度期望但大幅降低方差

历史背景：Williams (1992) 在 *Machine Learning* 期刊发表论文 *Simple statistical gradient-following algorithms for connectionist reinforcement learning*，正式提出 REINFORCE 算法名称和完整数学推导。策略梯度定理的更一般形式见 Sutton et al. (1999)。

## 原理详解

### 1. 策略梯度定理

定义目标函数 J(π_θ) = E_{τ~π_θ}[R(τ)]，其中 R(τ) = Σ_t γ^t r_t 为折扣回报。

```
∇_θ J(π_θ) = E_{τ~π_θ}[ Σ_{t=0}^{T} ∇_θ log π_θ(a_t|s_t) · A^{π_θ}(s_t, a_t) ]
```

关键推导——对数导数技巧：

```
∇_θ E_{τ}[f(τ)] = E_τ[ ∇_θ log p_θ(τ) · f(τ) ]

其中 p_θ(τ) = p(s_0) * Π_t π_θ(a_t|s_t) * P(s_{t+1}|s_t,a_t)

∇_θ log p_θ(τ) = Σ_t ∇_θ log π_θ(a_t|s_t)
```

环境动力学 P(s'|s,a) 不依赖 θ，因此不出现在梯度中——**这是策略梯度不需要知道环境模型的根本原因**。

### 2. REINFORCE 更新规则

```
θ_{k+1} = θ_k + α · ∇_θ J(π_{θ_k})
```

对每条轨迹中的每步 (s_t, a_t)：

```
∇_θ log π_θ(a_t|s_t) * (G_t - b(s_t))
```

其中：
- `G_t` = rewards-to-go = Σ_{k=t}^{T-1} γ^{k-t} · r_k
- `b(s_t)` = 基线（通常用 V(s_t) 的估计），不改变梯度期望但降低方差
- `A(s_t,a_t) = G_t - b(s_t)` = 优势函数估计

### 3. softmax 线性策略的梯度

本 demo 使用 softmax 线性策略：π(a|s) = softmax(θ[s])，即

```
π(a|s) = exp(θ[s][a]) / Σ_{a'} exp(θ[s][a'])
```

对数概率梯度：

```
∂ log π(a|s) / ∂θ[s][a'] = I(a'=a) - π(a'|s)
```

即：对采取的动作 a，θ[s][a] 的梯度为 `1 - π(a|s)`；对其他动作 a'，梯度为 `-π(a'|s)`。

### 4. 基线与方差缩减

不加基线时，梯度估计为 `∇ log π(a_t|s_t) * G_t`。G_t 的方差可能很大（尤其在长轨迹中）。

加入基线 V(s) 后变为 `∇ log π(a_t|s_t) * (G_t - V(s_t))`：

```
E[∇ log π(a|s) * G_t] = E[∇ log π(a|s) * (G_t - b(s))]   （对任何不依赖 a 的 b(s)）
```

因为 `E_{a~π}[∇ log π(a|s)] = Σ_a π(a|s) * ∇ log π(a|s) = Σ_a ∇π(a|s) = ∇ Σ_a π(a|s) = ∇ 1 = 0`，所以减去任何不依赖 a 的项不影响期望。

### 5. 数据流

```
┌──────────────────────────────────────────────────────┐
│  每次迭代:                                            │
│    1. 用当前策略 π_θ 跑完完整 episode → 收集轨迹       │
│       τ = (s_0,a_0,r_0, s_1,a_1,r_1, ..., s_T)      │
│    2. 计算每步的 returns G_t（从后往前累加）           │
│       G_{T-1} = r_{T-1}                               │
│       G_t = r_t + γ * G_{t+1}                         │
│    3. 计算 advantage: A_t = G_t - V(s_t)             │
│    4. 策略梯度: g = Σ_t ∇ log π(a_t|s_t) * A_t       │
│    5. 更新: θ ← θ + α * g                             │
│    6. 拟合 V: V(s) ← V(s) + β * (G_t - V(s))          │
└──────────────────────────────────────────────────────┘
```

## 对比 / 选型

| 特性 | REINFORCE | Q-Learning | PPO |
| --- | --- | --- | --- |
| 学习对象 | 策略 π_θ | Q 表 | 策略 + 价值 |
| on/off-policy | on-policy | off-policy | on-policy |
| 样本效率 | 低（每条轨迹只用一次） | 中（Q 表持续更新） | 高（多 epoch 复用） |
| 方差 | 高（需 baseline 缓解） | 低 | 中（GAE 优势估计） |
| 连续动作 | ✅ 天然支持 | ❌ 需离散化 | ✅ |
| 收敛稳定性 | 较差（大方差） | 好（表格情况） | 好（clip 约束） |

## 环境准备

- 操作系统：跨平台
- 语言版本：Python 3.8+
- 依赖：无（纯标准库实现）

## 运行方式

```bash
python3 reinforce.py
```

## 关键代码片段

```python
# 策略梯度定理的实现核心
def log_prob_grad(theta, state, action):
    """d(log pi(a|s))/d(theta[s]) = I(a'=a) - pi(a'|s)"""
    probs = softmax(theta[state])
    grad = [0.0] * N_ACTIONS
    for a in range(N_ACTIONS):
        grad[a] = (1.0 if a == action else 0.0) - probs[a]
    return grad

# REINFORCE 更新
for t, (s, a) in enumerate(trajectory):
    grad = log_prob_grad(theta, s, a)
    advantage = returns[t] - V[s]  # baseline
    for act in range(N_ACTIONS):
        theta[s][act] += alpha * advantage * grad[act]
```

## 性能与边界

- **时间复杂度**：O(episodes × T × |A|)，T 为轨迹长度
- **方差**：不加基线时方差极高（G_t 的方差随轨迹长度指数增长）
- **样本效率**：on-policy，每条轨迹只能用一次（vs PPO 可多 epoch 复用）
- **策略参数化**：本 demo 使用表格型 θ[s][a]，每个状态独立；实际应用中常用神经网络参数化

## 注意事项与常见坑

1. **必须完成完整 episode**：REINFORCE 是 Monte Carlo 方法，需要跑完整个 episode 才能计算 G_t。不适用于无限 horizon 任务（需改用 actor-critic）
2. **基线不改变期望但降低方差**：实验中不加基线的 REINFORCE 收敛更慢且更不稳定。基线 V(s) 必须不依赖动作 a
3. **rewards-to-go 的计算**：G_t 只取 t 之后的奖励（不含 t 之前的），因为 t 之前的奖励不受 a_t 影响（因果性）
4. **学习率敏感**：α 过大导致策略崩塌（某动作概率→1，其他→0，丧失探索能力）；α 过小收敛极慢
5. **softmax 数值稳定性**：必须减去 max(logits) 再取 exp，否则大 logits 会导致溢出
6. **on-policy 约束**：更新后旧数据即失效，不能像 Q-Learning 那样用 replay buffer

## 参考资料（实际阅读过的权威来源）

- [Vanilla Policy Gradient — OpenAI Spinning Up](https://spinningup.openai.com/en/latest/algorithms/vpg.html) — VPG/REINFORCE 完整伪代码、策略梯度定理公式、优势函数 baseline、GAE-Lambda 优势估计、rewards-to-go 概念
- [Model-Free Reinforcement Learning — Sutton et al.](http://incompleteideas.net/papers/DPS-ACC-12.pdf) — 策略梯度定理扩展到连续动作（公式 2-5）、baseline 不变性证明（公式 3）、compatible features、λ-return 与 eligibility traces 的前向/后向视角
- [Reinforcement Learning: An Introduction — Sutton & Barto](http://incompleteideas.net/book/bookdraft2018jan1.pdf) — Ch.6 Q-Learning (§6.5) 与 Ch.13 Policy Gradient Methods 的完整教材（目录确认 §13 REINFORCE + baseline + actor-critic 章节）
- Williams, R.J. (1992). Simple statistical gradient-following algorithms for connectionist reinforcement learning. *Machine Learning*, 8, 229-256. — REINFORCE 原始论文（经 Spinning Up 引用确认）
