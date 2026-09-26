# 强化学习

## 子领域

| 子目录 | 说明 |
| --- | --- |
| [Q-Learning/](./Q-Learning/) | 表格 Q-Learning（Watkins 1989, off-policy, epsilon-greedy 探索） |
| [REINFORCE/](./REINFORCE/) | 策略梯度 REINFORCE（Williams 1992, on-policy, baseline 方差缩减） |
| [PPO/](./PPO/) | 近端策略优化 PPO-Clip（Schulman 2017, 多 epoch 复用, 裁剪策略比率） |
| [DQN经验回放与目标网络/](./DQN经验回放与目标网络/) | 深度 Q 网络（Mnih 2013/2015, 经验回放打破样本相关 + 目标网络稳定自举） |
| [DoubleQLearning与最大化偏差/](./DoubleQLearning与最大化偏差/) | 双估计量消除最大化偏差（van Hasselt 2010/2015, Double Q-learning / Double DQN） |
| [资格迹TDLambda/](./资格迹TDLambda/) | 资格迹 TD(λ)（Sutton 1988, n 步回报 + 前向/后向视图等价） |
| [ActorCritic与GAE/](./ActorCritic与GAE/) | Actor-Critic 与广义优势估计（Schulman 2015, 基线降方差 + λ 连续插值） |
| [SAC最大熵与温度自调节/](./SAC最大熵与温度自调节/) | 软 Actor-Critic（Haarnoja 2018, 最大熵目标 + 温度自动调节） |

## 已完成 demo

| 目录 | demo |
| --- | --- |
| [Q-Learning/](./Q-Learning/) | 036 Q-Learning 表格方法（Bellman 更新 + epsilon-greedy + 自纠正性） |
| [REINFORCE/](./REINFORCE/) | 037 REINFORCE 策略梯度（策略梯度定理 + 对数导数技巧 + baseline 方差缩减） |
| [PPO/](./PPO/) | 038 PPO 近端策略优化（裁剪替代目标 + 策略比率 + GAE 优势估计 + 多 epoch 复用） |
| [DQN经验回放与目标网络/](./DQN经验回放与目标网络/) | 287 DQN 经验回放与目标网络（回放打破时序相关 + 目标网络冻结自举 + 半梯度 + 终止态不自举；消融实测同号率 0.732→0.525） |
| [DoubleQLearning与最大化偏差/](./DoubleQLearning与最大化偏差/) | 288 Double Q-learning 与最大化偏差（双估计量选择/评估分离 + Jensen 不等式来源 + Lemma 1 充要条件 + Theorem 1 下界 √(C/(m−1))） |
| [资格迹TDLambda/](./资格迹TDLambda/) | 289 资格迹 TD(λ)（n 步回报 + λ-回报前向视图与迹后向视图离线等价 + 累积迹/替换迹 + λ 与 γ 的分工） |
| [ActorCritic与GAE/](./ActorCritic与GAE/) | 290 Actor-Critic 与 GAE（策略梯度基线合法性 EGLP 引理 + k 步优势 + GAE 两种等价形式 + Critic 误差变成 Actor 偏差） |
| [SAC最大熵与温度自调节/](./SAC最大熵与温度自调节/) | 291 SAC 最大熵与温度自调节（软 Bellman backup 的 log-sum-exp + Boltzmann 策略 + 熵约束对偶导出温度梯度 + α→0 退化误差 O(α)） |
| [TRPO信赖域与共轭梯度/](./TRPO信赖域与共轭梯度/) | 736 TRPO 信赖域与共轭梯度（Theorem 1 单调改进界 + MM 视角 + 惩罚→硬约束/max-KL→均值/二阶→CG 三替换 + β=√(2δ/sᵀAs) 步长 + 线搜索双条件 + 与 PPO 谱系） |

## 待研究

- [x] DQN（Deep Q Network）（→ [DQN经验回放与目标网络/](./DQN经验回放与目标网络/)，2026-09-17，ID 287）
- [x] Actor-Critic（→ [ActorCritic与GAE/](./ActorCritic与GAE/)，2026-09-17，ID 290）
- [ ] A2C / A3C（同步/异步并行 actor-learner）
- [x] SAC（Soft Actor-Critic）（→ [SAC最大熵与温度自调节/](./SAC最大熵与温度自调节/)，2026-09-17，ID 291）
- [x] TRPO（信赖域约束的单调改进保证 + 共轭梯度求方向）→ demo 736
- [ ] 离线 RL / CQL（保守 Q 学习，分布外动作的过估计抑制）
- [ ] 多智能体 RL（MADDPG / QMIX 的信用分配）
- [ ] 基于模型的 RL（Dyna 框架 + MuZero 的隐式模型学习）
- [ ] 分层强化学习（options 框架与半马尔可夫决策过程）
- [ ] 探索策略（count-based / RND 内在奖励 / 后验采样）
