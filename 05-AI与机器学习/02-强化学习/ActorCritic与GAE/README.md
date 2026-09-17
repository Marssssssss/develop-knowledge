# Actor-Critic 与 GAE(广义优势估计)

## 简介

Actor-Critic = **Actor**(策略)+ **Critic**(值函数)。策略梯度定理给出的估计量含一个零均值的多余项,
减去一个**只依赖状态**的基线就能在不引入偏差的前提下**降方差**;GAE 则是把"用多少步的 TD 残差
来估计优势"这件事参数化,用 $λ$ 在**偏差**与**方差**之间连续插值。

关键概念:

| 概念 | 一句话解释 |
| --- | --- |
| 策略梯度定理 | $∇J = E[Σ_t ∇\logπ(a_t|s_t)\,A^{π}(s_t,a_t)]$,用优势代替回报 |
| 基线 | 任何只依赖 $s$ 的函数 $b(s)$;由 EGLP 引理,$E[∇\logπ·b(s)]=0$,减去它不改变期望 |
| 优势 $A^{π}$ | $Q^{π}−V^{π}$,即"比该状态的平均水平好多少" |
| $k$ 步优势 $Â^{(k)}$ | $Σ_{l=0}^{k-1}γ^lδ^V_{t+l}$,$k$ 控制偏差-方差 |
| GAE | $Â^{GAE(γ,λ)}_t = Σ_{l=0}^{∞}(γλ)^lδ^V_{t+l}$,所有 $k$ 步优势的指数加权和 |
| Critic 的误差 | 用欠拟合的 $\hat V$ 当基线,优势估计会出现**系统性偏差**,且被策略梯度继承 |

历史:actor-critic 的原始形式见 Sutton & Barto 1st ed. §6.6;GAE 由 Schulman 等人 2015 年提出
([arXiv:1506.02438](https://arxiv.org/abs/1506.02438)),随后成为 TRPO / PPO / A2C 的标准组件。
它与 TD(λ) 是同一件事的两面(见同级 [资格迹TDLambda/](../资格迹TDLambda/)):GAE 的 $λ$ 就是迹衰减参数。

## 原理详解

### 1. 策略梯度的两个降方差手段

原始形式 $\nabla J = E[\sum_t \nabla\log\pi(a_t|s_t)\sum_{t'=0}^{T}\gamma^{t'}r_{t'}]$ 里,
**动作之前的奖励**那些项均值为 0 但方差非 0 —— 只是给估计量加噪声。
Spinning Up 的原话:*"they would just add noise to sample estimates of the policy gradient.
By removing them, we reduce the number of sample trajectories needed."*
于是先把求和上界改成 $t$ 以后(reward-to-go),再减去基线:

$$
E\big[\nabla_\theta\log\pi_\theta(a_t|s_t)\,b(s_t)\big] = 0 \quad \text{(EGLP 引理)}
$$

所以**减去任意只依赖状态的 $b(s)$ 都不改变期望**,而 $b = V^{π}$ 是最常用的选择 —— 剩下的正好是优势 $A^{π}$。

### 2. 优势的 $k$ 步估计

$$
δ^V_t = r_t + γV(s_{t+1}) − V(s_t), \qquad
Â^{(k)}_t = \sum_{l=0}^{k-1}γ^l δ^V_{t+l}
$$

展开即可看出 $Â^{(k)}_t = −V(s_t) + r_t + γr_{t+1} + \dots + γ^{k-1}r_{t+k-1} + γ^kV(s_{t+k})$:
前 $k$ 步用真实奖励,第 $k$ 步用值函数自举。两个端点:

- $k=1$:$Â^{(1)}_t = δ^V_t$,引入 Critic 的偏差但方差最小
- $k→∞$:$Â^{(∞)}_t = \sum_l γ^lr_{t+l} − V(s_t)$,无 Critic 偏差但方差最大(reward-to-go 减基线)

### 3. GAE:$k$ 的指数加权平均

$$
Â^{GAE(γ,λ)}_t = (1−λ)\big(Â^{(1)}_t + λÂ^{(2)}_t + λ^2Â^{(3)}_t + \dots\big)
= \sum_{l=0}^{∞}(γλ)^l δ^V_{t+l}
$$

原论文显式做了这个推导(把每个 $Â^{(k)}$ 展开、按 $δ$ 归并,几何级数求和后剩下
$\sum_l(γλ)^lδ^V_{t+l}$)。它等价于一个**后向递推**:

$$
Â^{GAE}_t = δ^V_t + γλ\,Â^{GAE}_{t+1}
$$

本 demo 用两条独立路径核对这个等价性:折扣求和形式 vs 指数加权 $k$ 步形式,
最大差 **3.47e-17**(轨迹长 12);$λ=0$ 时 $Â$ 与 $δ$ 的差 **0.00e+00**(精确)。

$λ$ 的作用(原论文的核心论断):$λ=0$ 时 $Â=δ$(TD(0) 式),完全继承 $\hat V$ 的偏差、方差最低;
$λ=1$ 时退化成 reward-to-go 减基线,**无偏差**但方差最高;$λ \in (0,1)$ 时 $\hat V$ 的误差被 $γλ$ 逐步衰减。

### 4. Critic 的误差会变成 Actor 的偏差

这是最容易被忽略的一点:$Â$ 里含 $\hat V$,而 $\hat V ≠ V^{π}$ 时 $E[Â] ≠ A^{π}$,于是策略梯度**有偏**。
$λ$ 越小,$\hat V$ 的误差被引入得越多;但 $λ$ 越大方差越大。本 demo 用同一个策略、同一个真值 $V^{π}$,
分别拿 **真实 $V^{π}$** 与**欠拟合的线性 $\hat V$** 当基线量出这件事(E2 / E3)。

### 5. 本 demo 的 MDP:可精确求解的走廊

$n$ 状态走廊,两端是终止态(左端奖励 −1、右端 +1),`slip=0.15` 会滑向相邻状态,
$γ$ 可调,可加零均值高斯奖励噪声。`policy_eval` 用高斯消元解 Bellman 方程得到**精确**的
$V^{π}$ 与 $A^{π}$,因此优势估计量的期望偏差可以被**精确**度量,而不用靠大样本近似
(完整定义见 [NOTES.md](./NOTES.md) §1)。

## 对比 / 选型

| 优势估计 | 形式 | 偏差来源 | 方差 | 备注 |
| --- | --- | --- | --- | --- |
| $Â^{(1)}=δ$ | TD(0) | $\hat V$ 的误差 | 最低 | 需要 Critic 足够准 |
| $n$ 步 | 固定 $n$ | 部分自举 | 中 | 要等 $n$ 步 |
| **GAE(γ,λ)** | 指数加权全部 $k$ | $λ→0$ 时继承 $\hat V$ 误差 | 可调 | 只需一次反向扫描,**O(1)** 额外内存 |
| reward-to-go − $V$ | $λ=1$ | 无 | 最高 | 等价于 $λ=1$ 的 GAE |

工程上 GAE 几乎是 PPO / TRPO / A2C 的默认选择:$λ=0.95$、$γ=0.99$ 是很常见的组合。

## 环境准备

- 操作系统:任意(本 demo 在 Windows + Git Bash 下运行)
- Python:3.13(仅标准库 `math` / `random` / `sys`)
- 依赖:无

## 运行方式

```bash
cd python
python main.py     # 退出码 0 = 全部断言通过
```

## 关键代码片段

```python
# main.py —— GAE 的后向递推(≈ 原文 Â_t = δ_t + γλÂ_{t+1})
def gae_discounted(rewards, values, gamma, lam):
    """返回 (优势, 回报)。values 比 rewards 长 1(含终止态的 V=0)。"""
    T = len(rewards)
    adv = [0.0] * T
    gae = 0.0
    for t in range(T - 1, -1, -1):
        delta = rewards[t] + gamma * values[t + 1] - values[t]
        gae = delta + gamma * lam * gae          # ① 只需一个标量运行和
        adv[t] = gae
    return adv


def weighted_k_step(rewards, values, gamma, lam, k_max):
    """(1−λ)Σ_{k=1}^{m−1}λ^{k−1}Â^{(k)} + λ^{m−1}Â^{(m)} —— 与递推式独立实现"""
    T = len(rewards)
    out = []
    for t in range(T):
        acc = 0.0
        for k in range(1, k_max):
            acc += (lam ** (k - 1)) * k_step_advantages(rewards, values, gamma, t, k)
        acc *= (1.0 - lam)
        acc += (lam ** (k_max - 1)) * k_step_advantages(rewards, values, gamma, t, k_max)
        out.append(acc)
    return out
```

```python
# mdp.py —— 用精确策略评估做"无噪声的靶子"
def policy_eval(self, pi):
    """解 Bellman 期望方程 V = r^π + γP^πV(高斯消元),得到精确 V^π。"""
    A = [[0.0] * self.n_states for _ in range(self.n_states)]
    b = [0.0] * self.n_states
    for s in range(self.n_states):
        A[s][s] = 1.0
        for a in range(self.N_ACTIONS):
            for prob, s2, r in self.outcomes(s, a):
                A[s][s2] -= self.gamma * pi[s][a] * prob   # ① 减去 γP^π
                b[s] += pi[s][a] * prob * r                # ② 右侧 r^π
    return gauss_solve(A, b)
```

## 性能与边界

实测(`python main.py`,走廊 MDP,每格 $(s_0,a_0)$ ≥40 样本,分层统计):

- **E1 两种形式等价**:折扣求和 vs 指数加权 $k$ 步,最大差 **3.47e-17**;
  $\lambda=0$ / $\lambda=1$ 两端**精确为零**($Â=δ$ / 纯 reward-to-go 减基线)。
- **E2 真实 $V^{π}$ 作基线**:$\lambda:0\to1$ 时平均标准差 **0.0988 → 0.2779**,
  偏差/标准误全程 ≤ **0.90**(**无偏**);MSE **0.01850 → 0.09317**,完全由方差决定($\lambda=1$ 是 5.0 倍)。
- **E3 欠拟合 $\hat V$ 作基线**:偏差/标准误 **8.23 → 3.86**(全程显著,真实基线下是 ≤0.90),
  偏差量级放大约 **4.3 倍**;趋势**反直觉** —— $\hat V$ 有误差时反而小 $\lambda$ 偏差更小
  (常值偏移被 $δ$ 里的 $-V(s_t)+γV(s_{t+1})$ 抵消),说明"$\lambda$ 小 → 偏差大"取决于误差**结构**。
- **E4 策略梯度批间方差**(12 轨迹 × 300 批):稀疏回报下 full_return 3.260 → return−V **2.824**(仅 −13%);
  稠密噪声奖励下 td_residual **1.979e+01** « full_return 1.210e+02(约 **6 倍**),GAE(0.95) 居中 5.403e+01。
  即 **"减基线一定降方差"是错的**,要看噪声来自哪里。

完整表格、推导的中间步骤、复杂度分析与开发期踩坑见 [NOTES.md](./NOTES.md)。

## 注意事项与常见坑

1. **朴素地按"分组均值"统计偏差会造出假偏差**(本 demo 第一版真实失误)。
   $E[Â_t\mid s_t,a_t]=A^{π}(s_t,a_t)$ 只在**固定 $(s_0,a_0)$** 时成立,混着统计量的是"平均优势"
   而不是"优势的偏差";改成按 $(s_0,a_0)$ 分层后才得到干净结论。**先固定估计量所针对的条件量,再谈偏差。**
2. **尾项不能重复计入**。截断形式必须写成
   `(1−λ)Σ_{k=1}^{m−1}λ^{k−1}Â^{(k)} + λ^{m−1}Â^{(m)}`,最后一项**不再乘** $(1−λ)$;
   写错时误差是 9.20e-02 级别,不会崩,只会静默偏掉(靠 E1 两路交叉验证暴露)。
3. **`values` 要比 `rewards` 长 1**。终止态的值必须补 0,否则最后一步的 $δ$ 用了垃圾值。
4. **GAE 的递推只需要一个标量**。`gae = δ + γλ·gae` 从后往前扫一遍即可($O(T)$);
   不要真的去算所有 $k$ 步优势($O(T²)$)——本 demo 的 $O(T²)$ 版本只用来**交叉验证**递推式。
5. **断言崩溃常常是真实 bug 的探测器**,别急着改断言绕过:第 2 条就是这样被找出来的;
   但也要能分清"是断言错还是实现错"。
6. **口径差异**:走廊 MDP 的 `slip`/终止奖励是本 demo 自定的教学参数,不对应任何论文的具体实验;
   $λ$ / $γ$ 的数值差异也不可与论文表格直接比较。可比较的是**结论的方向**。

## 参考资料(实际阅读过的权威来源)

- [Schulman, Moritz, Levine, Jordan, Abbeel, *High-Dimensional Continuous Control Using Generalized Advantage Estimation*(arXiv:1506.02438)](https://arxiv.org/abs/1506.02438)
  —— $k$ 步优势、$Â^{(∞)}$、GAE 的两种等价形式与 $λ$ 的偏差-方差含义;
  全文经 [ar5iv 渲染版](https://ar5iv.labs.arxiv.org/html/1506.02438)精读。
- [OpenAI Spinning Up, *Intro to Policy Optimization*](https://spinningup.openai.com/en/latest/spinningup/rl_intro3.html)
  —— reward-to-go、"减去过去奖励项只降方差"的原文表述、EGLP 引理与 baseline 的合法性。
- [Sutton & Barto, *Reinforcement Learning: An Introduction*(1st ed., 在线版)§6.6 Actor-Critic Methods](http://incompleteideas.net/book/first/ebook/node66.html)
  —— actor-critic 的原始形式(策略参数用 TD 误差驱动)与它相对 pure critic / pure actor 的定位。
- [Sutton & Barto, 同书 §7.2–7.4](http://incompleteideas.net/book/first/ebook/node74.html)
  —— TD(λ) 前向/后向视图与等价性;GAE 的 $λ$ 与资格迹的 $λ$ 是同一参数。
- [OpenAI Spinning Up, *Vanilla Policy Gradient*](https://spinningup.openai.com/en/latest/algorithms/vpg.html)
  —— VPG 的标准实现结构(Actor + Critic 分别更新、优势归一化等工程细节)。
