# PPO 近端策略优化

## 简介

PPO (Proximal Policy Optimization) 由 Schulman et al. (2017) 在 OpenAI 提出，是当前最广泛使用的策略梯度算法之一。它在 REINFORCE 的基础上引入了**裁剪替代目标函数（clipped surrogate objective）**，允许对同一批数据进行多轮更新，同时通过裁剪策略比率防止更新过大导致策略崩溃。PPO 因其简单性、样本效率和鲁棒性，被 OpenAI 用于 RLHF（人类反馈强化学习）微调 GPT 等大语言模型。

- **策略比率**：r_t(θ) = π_θ(a_t|s_t) / π_{θ_old}(a_t|s_t)，新旧策略的概率比
- **裁剪替代目标**：L = E[min(r_t·A_t, clip(r_t, 1-ε, 1+ε)·A_t)]，取下界（悲观估计）
- **多 epoch 复用**：与 REINFORCE 每条轨迹只用一次不同，PPO 可对同一 batch 做多轮 SGD
- **GAE 优势估计**：用指数移动平均融合多步 TD 误差，平衡偏差与方差

历史背景：PPO 由 Schulman 等人在 2017 年提出（arXiv:1707.06347），旨在获得 TRPO（Trust Region Policy Optimization）的数据效率和稳定性，同时只用一阶优化（不需要二阶 Hessian 矩阵），实现简单。PPO-Clip 变体因其极简性和有效性成为最常用版本。

## 原理详解

### 1. 策略梯度回顾

标准策略梯度（REINFORCE）的梯度估计器：

```
ĝ = E_t[ ∇_θ log π_θ(a_t|s_t) · Â_t ]    (公式 1, Schulman 2017 §2.1)
```

等价的目标函数（自动微分视角）：

```
L^PG(θ) = E_t[ log π_θ(a_t|s_t) · Â_t ]    (公式 2)
```

**问题**：对 L^PG 做多步优化没有理论保证，实际中会导致破坏性大更新。

### 2. TRPO 的替代约束

TRPO 用 KL 散度硬约束限制策略更新幅度：

```
maximize  E_t[ π_θ(a_t|s_t)/π_{θ_old}(a_t|s_t) · Â_t ]
subject to E_t[ KL(π_{θ_old}(·|s_t), π_θ(·|s_t)) ] ≤ δ
```

TRPO 复杂（需要共轭梯度法 + Fisher 矩阵），PPO 用 clip 替代硬约束。

### 3. PPO-Clip 裁剪替代目标

定义策略比率：

```
r_t(θ) = π_θ(a_t|s_t) / π_{θ_old}(a_t|s_t)
```

PPO-Clip 目标函数（arXiv:1707.06347 §3, arXiv:2307.04964 公式 15）：

```
L^PPO-clip(θ) = E_t[ min( r_t(θ)·Â_t,  clip(r_t(θ), 1-ε, 1+ε)·Â_t ) ]
```

其中：
- `ε`（clip 参数）：通常取 0.1-0.3，控制新策略偏离旧策略的程度
- `Â_t`：优势函数估计（advantage）
- `min`：取两项较小值——**悲观下界**，防止过度乐观的更新

**裁剪效果分析**：

| 优势 A_t > 0（好动作） | 优势 A_t < 0（坏动作） |
| --- | --- |
| 鼓励增大 r_t（提高概率） | 鼓励减小 r_t（降低概率） |
| r_t > 1+ε 时 clip 限制奖励 | r_t < 1-ε 时 clip 限制惩罚 |
| 防止好动作概率过度膨胀 | 防止坏动作概率过度坍缩 |

### 4. 优势估计 GAE（Generalized Advantage Estimation）

TD 误差：`δ_t = r_t + γ·V(s_{t+1}) - V(s_t)`

GAE 定义（arXiv:2307.04964 公式 9）：

```
Â_t^{GAE(γ,λ)} = Σ_{l=0}^∞ (γλ)^l · δ_{t+l}
```

极限情况：
- **λ=0**：Â_t = δ_t（高偏差、低方差，类似 TD(0)）
- **λ=1**：Â_t = Σ γ^l r_{t+l} - V(s_t)（低偏差、高方差，类似 Monte Carlo）

本 demo 简化为 Â_t = G_t - V(s_t)（即 λ=1 的 GAE）。

### 5. 价值函数损失

```
L^critic(ϕ) = E_t[ ||V_ϕ(s_t) - R̂_t||² ]    (arXiv:2307.04964 公式 16)
```

其中 `R̂_t = Σ γ^l r_{t+l}` 为折扣回报。用 MSE 回归拟合 V_ϕ。

### 6. PPO 完整算法

```
Algorithm: PPO
Input: initial policy θ_0, initial value function ϕ_0
for n = 0, 1, 2, ... do
    1. Collect trajectories D_n = {τ_i} using π(θ_n)
    2. Compute rewards-to-go R̂_t
    3. Compute advantage estimates Â_t using V_{ϕ_n}
    4. Update policy by maximizing L^PPO-clip(θ):
       θ_{n+1} = argmax_θ L^PPO-clip(θ)    (multiple epochs of SGD)
    5. Update value function by MSE regression:
       ϕ_{n+1} = argmin_ϕ L^critic(ϕ)
end for
```

### 7. 数据流

```
┌──────────────────────────────────────────────────────┐
│  每次迭代:                                            │
│    1. 用 π_θ 收集 batch（多条轨迹）                   │
│    2. 计算 returns G_t 和 advantages A_t = G_t - V(s) │
│    3. 保存 old_log_prob = log π_{θ_old}(a_t|s_t)      │
│    4. For epoch = 1..K:                                │
│       a. ratio = exp(log π_θ(a_t|s_t) - old_log_prob)│
│       b. obj = min(ratio·A, clip(ratio,1-ε,1+ε)·A)   │
│       c. θ ← θ + lr · ∇obj                            │
│       d. V(s) ← V(s) + lr_v · (G_t - V(s))             │
└──────────────────────────────────────────────────────┘
```

## 对比 / 选型

| 特性 | REINFORCE | PPO | TRPO |
| --- | --- | --- | --- |
| 数据复用 | 1 次 | K 次（多 epoch） | K 次 |
| 更新约束 | 无 | clip(ratio, 1±ε) | KL ≤ δ（硬约束） |
| 优化方法 | 一阶 SGD | 一阶 SGD | 二阶（共轭梯度） |
| 实现复杂度 | 最低 | 低 | 高 |
| 样本效率 | 最低 | 高 | 最高 |
| 超参敏感度 | 高 | 低 | 中 |

## 环境准备

- 操作系统：跨平台
- 语言版本：Python 3.8+
- 依赖：无（纯标准库实现）

## 运行方式

```bash
python3 ppo.py     # 退出码 0 = 五个 demo 全部跑完
```

> 源码分两个文件:`ppo.py`(5 个 demo)+ `ppo_core.py`(GridWorld / softmax 策略 / `ppo_update` / `ppo_train`),拆分只为守住「单源文件 ≤ 300 行」上限(OPTIMIZATION.md §1.1);直接 `python3 ppo.py` 即可。

## 关键代码片段

```python
# PPO-Clip 核心目标函数
ratio = math.exp(new_log_prob - old_log_prob)  # π_new / π_old
clipped_ratio = max(1.0 - clip_eps, min(1.0 + clip_eps, ratio))

# 悲观下界：取两项较小值
obj = min(ratio * advantage, clipped_ratio * advantage)

# 梯度上升更新策略
grad = log_prob_grad(theta, state, action)
for act in range(N_ACTIONS):
    theta[state][act] += lr_policy * advantage * grad[act] * ratio

# 价值函数 MSE 更新
V[state] += lr_value * (returns[t] - V[state])
```

## 性能与边界

- **时间复杂度**：O(iterations × batch_size × epochs × |A|)，多 epoch 复用是 PPO vs REINFORCE 的核心优势
- **clip 参数 ε**：ε=0.2 是原论文默认值；ε 过小 → 更新过于保守，收敛慢；ε 过大 → 接近 vanilla PG，可能策略崩溃
- **实测（`python ppo.py`）**：Demo 1 训练 100 批后成功率 **100.0%**、10 批窗口平均回报 0.353 → **0.972**,
  贪心策略能沿 `S→` 走到 `G`;Demo 2 的 clip fraction 随 ε 增大反而下降(ε=0.1/0.2/0.3 → **0.25% / 0.10% / 0.02%**)。
- **稀疏奖励是硬边界**：均匀随机策略下 200 次采样只有 **2 次**能走到目标（其余全落洞，落洞奖励 0），
  故**零初始化策略采出的单批数据常常整批回报全 0** → 优势全 0 → 梯度为 0,什么也学不到(决定了 Demo 3 的写法)。
- **策略比率均值**：理想值接近 1.0;lr_policy=0.1 下单批 4 epoch 实测区间仅 **0.9719 ~ 1.0093**,clip 从未触发。

## 注意事项与常见坑

1. **old_log_prob 必须在 batch 开始时固定**：PPO 的 ratio 是新旧策略之比，old_log_prob 必须在数据收集后立即计算并锁定，不能在 epoch 内更新
2. **多 epoch 更新是 PPO 的核心优势**：REINFORCE 每条轨迹只用一次，PPO 可复用 K 次（通常 4-10）。但 epoch 过多会导致 ratio 偏离 1.0 太远
3. **clip 只影响梯度方向，不影响梯度大小**：当 ratio 超出 [1-ε, 1+ε] 时，梯度变为 0（clip 区域），阻止进一步更新
4. **优势函数的归一化**：实践中常对 batch 内优势做标准化 (Â - μ) / σ，加速收敛
5. **RLHF 中的 PPO 变体**：在 RLHF 微调 LLM 时，PPO 还会加入 pretraining gradients（L^PPO-ptx = L^PPO-clip + λ·L^pretrain），防止模型遗忘语言能力（arXiv:2307.04964 公式 17）
6. **on-policy 约束**：虽然 PPO 复用数据，但旧数据在 K epoch 后必须丢弃——严格来说 PPO 仍是 on-policy 算法
7. **「比率恒为 1.000」不是收敛,是信号缺失**（开发期实跑抓到）：`ppo_train` 返回 4 个值
   `(theta, batch_rewards, stats_history, V)`,早期版本 Demo 4 按 3 个解包直接 `ValueError` 崩溃;Demo 3 用
   **均匀初始策略**采批时整批回报全 0 → 优势全 0 → `max|Δtheta| == 0.0`,ratio 精确停在 `1.000000`,
   看着"很稳定"实际啥也没发生。**判据:比率全 1.0 + clip fraction 0 + theta 增量为 0,三者同时出现即信号缺失**,
   不是"已收敛"。修法:先 `ppo_train(iterations=20)` 让策略离开均匀分布再采批。
8. **演示脚本也要实跑**:上述两个缺陷（崩溃 + 空转）**光读代码都看不出来** —— 一个错在解包个数,一个错在奖励分布恰好全零。

## 参考资料（实际阅读过的权威来源）

- [Proximal Policy Optimization Algorithms — Schulman et al. (2017)](https://arxiv.org/pdf/1707.06347) — arXiv:1707.06347 原始论文全文，含公式 1-2（策略梯度基础）、§3 PPO-Clip 目标函数定义、TRPO 对比、Atari/Roboschool 实验结果
- [Secrets of RLHF in Large Language Models Part I: PPO — arXiv:2307.04964](https://arxiv.org/html/2307.04964) — PPO-Clip 目标公式 15、价值函数损失公式 16、GAE 公式 7-12（TD 误差/TD-k return/k-step advantage/GAE 主体/λ=0 与 λ=1 极限/策略梯度估计器）、PPO 完整伪代码 Algorithm 1、RLHF 中的 pretraining gradients 混合公式 17
- [Vanilla Policy Gradient — OpenAI Spinning Up](https://spinningup.openai.com/en/latest/algorithms/vpg.html) — 策略梯度定理、优势函数 baseline、rewards-to-go 概念（PPO 的理论基础）
- [Understanding Reinforcement — arXiv:2304.00026](https://arxiv.org/pdf/2304.00026) — PPO Algorithm 6 完整伪代码、clip 替代 log(π) 的直观解释、PPO vs DDPG vs TD3 架构对比
