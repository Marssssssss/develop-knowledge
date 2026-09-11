# 强化学习

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [Q-Learning/](./Q-Learning/) | 表格 Q-Learning（Watkins 1989, off-policy, epsilon-greedy 探索） |
| [REINFORCE/](./REINFORCE/) | 策略梯度 REINFORCE（Williams 1992, on-policy, baseline 方差缩减） |
| [PPO/](./PPO/) | 近端策略优化 PPO-Clip（Schulman 2017, 多 epoch 复用, 裁剪策略比率） |

## 已完成 demo

| 目录 | demo |
| --- | --- |
| [Q-Learning/](./Q-Learning/) | 036 Q-Learning 表格方法（Bellman 更新 + epsilon-greedy + 自纠正性） |
| [REINFORCE/](./REINFORCE/) | 037 REINFORCE 策略梯度（策略梯度定理 + 对数导数技巧 + baseline 方差缩减） |
| [PPO/](./PPO/) | 038 PPO 近端策略优化（裁剪替代目标 + 策略比率 + GAE 优势估计 + 多 epoch 复用） |

## 待研究

- [ ] DQN（Deep Q Network）
- [ ] Actor-Critic
- [ ] A2C / A3C
- [ ] SAC（Soft Actor-Critic）
