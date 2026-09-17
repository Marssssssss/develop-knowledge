# DQN:经验回放与目标网络

## 简介

DQN(Deep Q-Network)把 Q-learning 的 Bellman 最优备份接到一个参数化函数逼近器上,并用两个
工程机制让它**不发散**:经验回放(experience replay)与目标网络(target network)。它是第一个
在 Atari 2600 上用同一套超参、只吃像素就超过人类水平的方法。

关键概念:

| 概念 | 一句话解释 |
| --- | --- |
| 半梯度(semi-gradient) | 只对 `Q(s,a;θ)` 求梯度,把自举目标里的 `θ` 当常数处理 |
| 经验回放 | 把转移 `(s,a,r,s')` 存进固定容量缓冲,更新时**均匀随机**采样 minibatch |
| 目标网络 `θ⁻` | 用一份旧参数算自举目标,每 `C` 步才从 `θ` 拷贝一次,两次同步之间保持不变 |
| 致命三要素 | 自举 + 函数逼近 + off-policy 备份分布,三者同时出现时价值估计可能发散 |
| 行为分布 vs 估计分布 | off-policy 方法的备份分布与它估计的策略所诱导的分布不一致 |

历史:DQN 由 DeepMind 在 2013 年提出([arXiv:1312.5602](https://arxiv.org/abs/1312.5602));
当时"非线性函数逼近 + 自举"被判过死刑(S&B 1st ed. §8.5 给了反例),回放 + 目标网络是绕过它的工程解。

## 原理详解

### 1. 要优化的量是**动态**的(这是不稳定性的总根源)

监督学习的标签在训练前是固定的;DQN 的标签是 `y = r + γ·max_a Q(s',a;θ)`,**依赖 θ 自己**。
原文明确点出这一点:

> Note that the targets depend on the network weights; this is in contrast with the targets
> used for supervised learning, which are fixed before learning begins.

于是损失 `L_i(θ_i) = E[ (y_i − Q(s,a;θ_i))² ]` 里,`y_i` 用的是**上一轮**的参数 `θ_{i-1}`,
论文写为"parameters from the previous iteration `θ_{i-1}` are held fixed when optimising
the loss function `L_i(θ_i)`"。这就是半梯度。

### 2. 为什么不能直接在线更新

原文列出三条:

1. 每条经验只能用于**一次**权重更新 → 数据效率低;
2. 相邻样本之间**强相关**:原文 p.4 说 learning directly from consecutive samples is
   inefficient, due to the strong correlations between the samples;
3. on-policy 时**当前参数决定下一批数据**:极大动作切到哪边、训练分布就跟到哪边 ——
   "unwanted feedback loops may arise and the parameters could ... diverge catastrophically"。

### 3. 经验回放做了什么

```
收集:  每步把 (s, a, r, s', done) 压入环形缓冲 D(容量 N,满了覆盖最旧)
更新:  从 D 里均匀随机抽 batch 条 → 逐条做一次半梯度 Q-learning 更新
```

`(s,a,r,s')` 四元组在后面的文字里被反复引用;采样是 "samples uniformly at random from D"。
回放把行为分布"摊平"到过去许多状态上 —— "averaging over many of its previous states,
smoothing out learning and avoiding oscillations or divergence"。代价:必须 off-policy 学
("it is necessary to learn off-policy ... which motivates the choice of Q-learning")。

### 4. 目标网络做了什么

```
每步:  y = r + γ · max_a Q(s', a; θ⁻)      # 用 θ⁻,不用 θ
每 C 步: θ⁻ ← θ                              # 硬同步(hard update)
```

目标网络把"移动的靶子"变成**分段常值**的靶子:在一个同步周期内 `θ⁻` 不动,这正是监督学习
里"标签固定"的条件,梯度下降的收敛前提才近似成立。本 demo 的断言直接测这件事:
对同一状态做一次梯度步,`max_a Q(s',a;θ)` 变了(`|Δ|=1.45e-02`),而 `max_a Q(s',a;θ⁻)`
一个 bit 都没动(`|Δ|=0.00e+00`)。

### 5. 两者都不能突破的理论障碍:致命三要素

Sutton & Barto 1st ed. §8.5 给了两个反例,说明**自举 + 函数逼近 + off-policy 备份分布**
可以发散,而且发散与"逼近器是否灵活"无关:

- **Baird 反例**(Example 8.3):6 状态、7 个权重的线性逼近,奖励恒 0(真值 `V≡0`),
  特征集线性无关、`V=0` 精确可表示,方法是最普通的同步 DP 备份 —— 唯一"不正常"的地方是
  备份分布取 **均匀**(off-policy),结果参数发散。
- **Tsitsiklis & Van Roy 反例**(Example 8.4):只有 2 个非终止状态、1 个参数,即使每步都取
  **最小二乘最优**逼近,序列照样发散。

原文的结论:**只要备份分布改成 on-policy 分布,收敛性就有保证**;DQN 的回放本质上是一个
off-policy 分布,所以它并不是"消除了发散",而是靠目标网络 + 回放把发散压到实践中不出现。

### 6. 一句话对照本 demo 的实现

| 论文元素 | 本 demo |
| --- | --- |
| Atari 像素 + 卷积 | 4×4 网格世界(one-hot 状态,16 维) |
| RMSProp,batch 32 | 手写 SGD,batch 8 |
| 100 万帧 ε 线性退火 | 350 回合 ε 线性退火(1.0 → 0.1) |
| 回放内存 100 万帧 | 5000 条 |
| 目标网络每 C=10⁴ 帧同步 | 每 50 个环境步同步 |

## 对比 / 选型

| 方案 | 样本效率 | 稳定性 | 适用场景 |
| --- | --- | --- | --- |
| 表格 Q-learning | 低(每状态要反复访问) | 有收敛保证 | 状态空间小、可枚举 |
| 在线 Q + 神经网络(无回放无目标网络) | 极低 | 容易振荡/发散 | 不推荐 |
| DQN(回放 + 目标网络) | 高 | 实践中稳定 | 离散动作、状态可观测 |
| Double DQN | 同 DQN | 同 DQN,且去掉高估 | DQN 的默认替代 |
| 优先回放(PER) | 更高 | 需重要性采样修正 | 奖励稀疏、转移价值差异大 |
| 连续动作 | — | — | DQN 不适用,改用 DDPG/TD3/SAC |

## 环境准备

- 操作系统:任意(本 demo 在 Windows + Git Bash 下运行)
- Python:3.13(仅用标准库 `math` / `random` / `sys`,**无第三方依赖**)
- 依赖:无

## 运行方式

```bash
cd python
python main.py     # 退出码 0 = 全部断言通过
```

## 关键代码片段

```python
# 采样与更新:回放(均匀随机) + 目标网络(θ⁻ 算自举目标) —— dqn_core.py / main.py
if replay:
    buf.push(s, a, r, s2, done)          # ① 存转移
    if len(buf) < warmup:
        continue                          # ② 预热期只收集不学
    samples = buf.sample(batch)           # ③ 均匀随机抽 batch
else:
    samples = [(s, a, r, s2, done)]       # 消融:退化成在线更新
for (bs, ba, br, bs2, bd) in samples:
    pre, h, q = online.forward_onehot(bs) # ④ 前向(for_one_hot 快速路径)
    qa = q[ba]
    if bd:
        target = br                       # ⑤ 终止转移不 bootstrap
    else:
        net = target_net if use_target else online
        target = br + gamma * max(net.forward_onehot(bs2)[2])   # ⑥ θ⁻ 算目标
    td = target - qa
    dq = [0.0] * env.N_ACTIONS
    dq[ba] = qa - target                  # ⑦ ∂(½(q−y)²)/∂q = q − y
    online.sgd_step_onehot(bs, pre, h, dq) # ⑧ 半梯度:只动被选动作那一路
steps += 1
if use_target and steps % sync_every == 0:
    target_net.copy_from(online)          # ⑨ 硬同步 θ⁻ ← θ
```

## 性能与边界

实测(`python main.py`,4×4 网格,γ=0.9,lr=0.1,hidden=32,700 回合,5 个种子,约 60 秒):

| 配置 | 平均成功率 | `max|Q|` | 相邻 TD 误差同号率 |
| --- | --- | --- | --- |
| full(replay + target) | **1.000**(5/5 完美) | 1.04 | **0.525** |
| no_target(无目标网络) | 0.825(1 个种子崩到 0.25) | 0.98 | 0.563 |
| no_replay(在线更新) | 0.800(2 个种子明显退化) | 1.03 | **0.732** |

逐种子明细、超参筛选过程与完整断言表见 [NOTES.md](./NOTES.md)。

- **回放的价值**最干净地体现在同号率上:无回放 0.732、有回放 0.525(≈0.5,均匀采样近似独立)。
  原文说随机化"breaks these correlations and therefore reduces the variance of the updates",
  0.732 → 0.525 就是这句话的量化。
- **目标网络的价值**体现在可靠性上:去掉它以后 5 个种子里 1 个明显崩掉,**两者都不是"锦上添花"**。
- 复杂度:每次更新 `O(hidden × n_in + hidden × n_out)`;本 demo 状态编码是 one-hot,
  `forward_onehot` 把输入层退化成一个查表 + 一次加法(省掉 16 次乘加)。
- 规模上限:纯 Python 双层 MLP,只用于原理演示;Atari 规模需要 GPU 与 RMSProp/Adam。

## 注意事项与常见坑

1. **固定起点 + 每步负奖励 → "Q 与状态无关"是自洽不动点**(本 demo 开发期真的踩到)。
   现象:训练后 11 条断言里 6 条失败,网络学出一个**常数 Q ≈ −15**。
   原因:如果所有状态的最优续演价值都一样,`Q(s,a) ≈ −C/(1−γ)` 对任意状态都自洽,梯度为零。
   规避:起点随机化 + 把目标奖励设为 +1、每步奖励设为 0,让状态之间产生真实的排序差异。
2. **共享隐藏层偏置会把整层"一起推死"**。one-hot 输入下,共享的 `b1` 收到的是所有状态的
   梯度之和,容易被推向负饱和区导致 ReLU 全灭。本 demo 用 `hid_bias=False`。
3. **`pre` / `h` 必须来自同一次前向**。`forward` 返回的中间量会被下一次前向覆盖;调用方要么
   立刻快照,要么按本 demo 的写法先算梯度再更新(顺序不能颠倒:输出层更新会改 `w2`,
   而隐藏层梯度要用**旧**的 `w2`)。
4. **终止转移不能自举**。`done=True` 时目标必须是 `y = r`。本 demo 的断言曾失败,根因是环境
   的终点格没有实现成**吸收态**(从终点再走一步会返回 `done=False`);修的是环境,不是断言。
5. **断言失败先怀疑断言,但更常见的是真 bug**:本 demo 的 12 条断言里有 2 条一开始是红的,
   两条最后都改的是实现(`_hidden_grad` 没有用调用方传入的 `lr`;终点格非吸收态)。
6. **口径差异**:本 demo 的数字来自 4×4 网格,不能与论文的 Atari 分数类比;论文的 Nature 2015
   版本正文需订阅(本次检索时不可达),技术陈述以 2013 年 arXiv 预印本 + S&B 1st ed. §8.5 为准。
7. **可复现性**:随机数走 `random.Random(seed)`、种子固定 1..5;换 Python 版本时 `randrange` 细节变化可能改变序列。

## 参考资料(实际阅读过的权威来源)

- [Mnih et al., *Playing Atari with Deep Reinforcement Learning*(arXiv:1312.5602)](https://arxiv.org/abs/1312.5602)
  —— DQN 原始论文。回放语义(§4)、半梯度与"targets depend on the network weights"(§3)、
  实验设置(RMSProp / batch 32 / ε 从 1 线性退火到 0.1 / 100 万帧内存,§5)均出自此。
  全文经 [ar5iv 渲染版](https://ar5iv.labs.arxiv.org/html/1312.5602)精读。
- [Sutton & Barto, *Reinforcement Learning: An Introduction*(1st ed., 在线版)§8.5 Off-Policy Bootstrapping](http://incompleteideas.net/book/first/ebook/node90.html)
  —— 致命三要素、Baird 反例(6 状态 / 7 权重 / 均匀备份分发散)、Tsitsiklis & Van Roy 反例、
  "averagers 稳定"的结论。
- [van Hasselt et al., *Deep Reinforcement Learning with Double Q-learning*(arXiv:1509.06461)](https://arxiv.org/abs/1509.06461)
  —— DQN 在 Atari 上存在**系统性高估**;Double DQN 的目标规则
  `Y_t = R_{t+1} + γ Q(S_{t+1}, argmax_a Q(S_{t+1},a;θ_t), θ⁻_t)`;Theorem 1 的高估下界。
  (Double DQN 的完整实现见同级的 [DoubleQLearning与最大化偏差/](../DoubleQLearning与最大化偏差/))
- [PyTorch, *Reinforcement Learning (DQN) Tutorial*](https://docs.pytorch.org/tutorials/intermediate/reinforcement_q_learning.html)
  —— 工业界最小可运行实现的结构参照:replay memory 的 `push` / `sample` 两方法、
  目标网络的 `polyak`/硬同步写法、`done` 掩码的用法。
