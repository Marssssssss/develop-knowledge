# SAC:最大熵目标与温度自动调节

## 简介

SAC(Soft Actor-Critic)把 RL 的目标从"最大化期望回报"换成"**最大化期望回报 + 策略熵**":
既要把任务做好,又要在能保持随机的地方尽量随机。这样做的直接收益是探索更充分、对超参更鲁棒;
代价是引入了一个新的超参 —— **温度 $α$**。SAC 的第二个关键贡献是把这个温度也变成**可学的**:
用一条梯度更新把访问到的状态上的平均熵**拉到目标熵** $H̄$。

关键概念:

| 概念 | 一句话解释 |
| --- | --- |
| 最大熵目标 | $J(π) = \sum_t E[r_t + α\,H(π(\cdot|s_t))]$,$α$ 是"熵相对回报的权重" |
| 软值 $V$ / 软 $Q$ | 在 Bellman 备份里加一项 $α\logπ$,使 backup 变成 log-sum-exp(软最大) |
| 温度 $α$ | $α→0$ 退化成普通(硬)RL;$α$ 越大越随机 |
| 约束形式 | 把熵当成**约束**(平均熵 ≥ $H̄$),对偶变量就是 $α$,于是 $α$ 有梯度可更新 |
| 目标熵 $H̄$ | 论文取 $H̄ = −\dim(\mathcal{A})$(如 HalfCheetah 为 −6),一个与动作维度挂钩的常数 |
| 重参数化 | $a = \tanh(μ_θ(s)+σ_θ(s)\odot ξ)$,$ξ\sim N(0,I)$,让策略梯度可以穿过采样 |

历史:最大熵 RL 与软最优性的框架由 Ziebart 等人提出;SAC 由 Haarnoja 等人于 2018 年给出
([arXiv:1801.01290](https://arxiv.org/abs/1801.01290)),同年稍晚的期刊版
([arXiv:1812.05905](https://arxiv.org/abs/1812.05905))加入**温度自动调节**与双 Q 网络等改进,是最常被引用的版本。

## 原理详解

### 1. 目标:回报 + 熵

$$
J(π) = \sum_{t} E_{(s_t,a_t)\simρ_π}\big[\,r(s_t,a_t) + α\,H(π(\cdot|s_t))\,\big],
\qquad H(π(\cdot|s)) = −\sum_a π(a|s)\logπ(a|s)
$$

论文原话:$α$ "determines the relative importance of the entropy term versus the reward",
而"the conventional objective can be recovered in the limit as $α→0$"。
把熵看成**每步的额外奖励** $r_t + αH$:"最大熵"不是"多加噪声",而是把随机性写进了最优性定义。

### 2. 软 Bellman backup(为什么出现 log-sum-exp)

$$
V(s) = α\log\sum_a \exp\!\Big(\frac{Q(s,a)}{α}\Big),
\qquad
Q(s,a) = r(s,a) + γ\,E_{s'}[V(s')]
$$

$α\log\sum_a\exp(Q/α)$ 就是**软最大**:$α→0$ 时退化为 $\max_a Q$,$α$ 大时趋近 $\frac{1}{|A|}\sum_aQ$ 加常数。
由 $V(s) = E_{a\simπ}[Q(s,a) − α\logπ(a|s)]$ 对 $π$ 求导置零,得最优策略是 Boltzmann 形式
$π(a|s) = \exp\big((Q(s,a) − V(s))/α\big)$。本 demo 直接断言了这一条
($π$ 与 $\exp((Q−V)/α)$ 最大差 **0.00e+00**),并断言 $α=0$ 时策略熵为 0(退化为确定性贪心)。

### 3. $α$ 的作用(精确数值,本 demo 实测)

在 5×5 网格(目标奖励 20)上做**精确**软策略迭代,$α$ 从 0 扫到 2.0:

| α | 0.0 | 0.01 | 0.1 | 0.3 | 0.5 | 0.7 | 1.0 | 2.0 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 软值 $V$(起点) | 13.9667 | 14.0039 | 14.3385 | 15.2007 | 16.6886 | 19.6613 | 27.6147 | 55.2288 |
| 平均策略熵 | 0.0000 | 0.4393 | 0.4410 | 0.6494 | 1.0604 | **1.3617** | 1.3322 | 1.3306 |
| 任务回报 | 16.3958 | 16.3958 | 16.3955 | 16.2360 | 15.1001 | 8.7338 | 0.0133 | 0.0000 |

三条可读的结论:

1. **软值 ≥ 硬最优值**($α\log\sum\exp \ge \max$),且随 $α$ 单调上升;
2. **任务回报随 $α$ 单调不增** —— 这就是最大熵的代价;
3. **$α→0$ 时误差线性趋零**:$\max|V_{soft}−V_{hard}| = 0.37179/0.037156/0.0037156$
   ($α=0.1/0.01/0.001$),相邻比值恰好 **10.000** —— 误差 $O(α)$,不是"大概收敛"而是**线性**。

注意第 2 行**非单调**:熵在 $α≈0.7$ 见顶(1.3617)后回落到 1.3306 并**饱和**,而不是单调涨到
$\ln 4 = 1.3863$——因为靠近墙或目标的格子存在并列/差异极大的动作,$α→∞$ 时全状态平均熵到不了
$\ln|A|$。所以"$α$ 越大熵越大"在这类有结构的状态上是**错**的(完整表见 [NOTES.md](./NOTES.md) §1)。

### 4. 温度自动调节

手动调 $α$ 有个本质困难:奖励的**量级**随任务变、也随策略变好而变,而最优熵依赖这个量级。
原论文的解法是把熵写成**约束**(平均熵 ≥ $H̄$)再取对偶,对偶变量就是温度,因此可以像普通参数
一样做梯度下降。论文的 Algorithm 2 里就是一行 `α ← α − λ ∇̂_α J(α)  ⊳ Adjust temperature`。

本 demo 用的更新是**负反馈**形式 $α \leftarrow \max\big(10^{-6},\ α − lr\,(H − H̄)\big)$:
当前熵 $H$ 高于目标 $H̄$ → 括号为正 → $α$ 减小(更贪心);低于目标 → $α$ 增大。
实测三个相差两个数量级的起点($α_0 = 0.05 / 1.0 / 4.0$)**全部收敛到同一个** $α^* = 0.39009$,
最终熵 $H = 0.83178$,$|H − H̄| = 0.00000$。

目标熵本 demo 取 $H̄ = 0.8318$;原论文在连续控制任务里取 $H̄ = −\dim(\mathcal{A})$
(例如 HalfCheetah 的 6 维动作对应 $H̄ = −6$)。**两者的数值口径不同**,见"注意事项"。

### 5. 本 demo 的 E4:数值边界

$α\log\sum_a\exp(Q/α)$ 里的 $Q/α$ 在 $α$ 小时会到几百 —— 朴素写法直接
`OverflowError: math range error`,减去最大值稳定化以后返回 **1001.3863**(= $1000 + \log 4$ ✔)。
这不是理论问题,是**实现时必须处理**的问题(详见 [NOTES.md](./NOTES.md) §5)。

## 对比 / 选型

| 算法 | 目标 | 策略 | 温度 | 稳定性 |
| --- | --- | --- | --- | --- |
| DDPG | 期望回报 | 确定性 + 外部噪声 | — | 对超参敏感 |
| TD3 | 期望回报 | 确定性 + 延迟策略更新 + 双 Q | — | 比 DDPG 稳 |
| PPO | 期望回报 + KL 约束 | 随机 | — | on-policy,样本效率低 |
| SAC(v1) | 最大熵 | 随机 | **手动调** | 稳,但要调 $α$ |
| **SAC(v2)** | 最大熵(约束形式) | 随机 | **自动** | 论文称"eliminates the need for per-task hyperparameter tuning" |

最大熵目标的另一个收益:探索不再依赖手工噪声(不像 DDPG 的 OU/高斯噪声),策略可以
"在不确定的地方随机、在明确的地方确定",且**逐状态**自动分配 —— 原论文强调约束形式允许
"the entropy at different states can vary",而不是强行让每状态熵等于常数。

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
def logsumexp(xs):
    """稳定版 log Σ exp:x 里含大数时朴素写法会 OverflowError,所以先减最大值。"""
    m = max(xs)
    return m + math.log(sum(math.exp(x - m) for x in xs))


def soft_value_iteration(alpha):
    """V(s) = α·logΣ_a exp(Q(s,a)/α) 的精确不动点迭代(α→0 时退化成硬值迭代)。"""
    V = [0.0] * N
    for _ in range(20000):
        d = 0.0
        for s in range(N):
            if s == GOAL:
                continue
            qs = [Q_OF(s, a, V) for a in range(N_ACTIONS)]
            v = logsumexp([q / alpha for q in qs]) * alpha if alpha > 0 else max(qs)
            d = max(d, abs(v - V[s]))
            V[s] = v
        if d < 1e-13:
            break
    return V


def auto_temperature(target_entropy, alpha0, lr, iters, policy_alpha):
    """约束形式下的对偶变量更新 —— 负反馈把平均熵推向目标熵:熵偏高就降 α。"""
    alpha = alpha0
    for _ in range(iters):
        V = soft_value_iteration(alpha)          # ① 当前 α 下的最优软策略
        pi = soft_policy(V, alpha)               # ② Boltzmann 策略
        H = policy_entropy(pi)                   # ③ 当前平均熵
        alpha = max(1e-6, alpha - lr * (H - target_entropy))  # ④ H 偏高则降 α
    return alpha, H
```

## 性能与边界

实测(`python main.py`,5×5 网格 / 目标奖励 20 / γ=0.95,**精确**解而非采样近似;PASS=14 FAIL=0):

| 实验 | 结论 | 实测值 |
| --- | --- | --- |
| E1 | 软值与硬最优之差 $O(α)$;软值 ≥ 硬最优;熵 ≤ $\ln\|A\|$;回报随 $α$ 不增 | gap 比值 = **10.000**;max H = 1.3617 < $\ln 4$ = 1.3863 |
| E2 | 表格软 Q 学习(off-policy + 回放 + 目标网络)收敛到软策略迭代不动点 | max gap = **0.00000**(期望形式与采样 $a'$ 形式都是 0) |
| E3 | 三个相差两个数量级的起点收敛到同一 $α^*$,最终熵命中目标熵 | $α^* = 0.39009$,$\|H−H̄\| = 0.00000$ |
| E4 | $α=0$ 退化为硬 Q-learning;$\log\sum\exp$ 必须稳定化;$\pi = \exp((Q−V)/α)$ | 朴素实现 `OverflowError`,稳定实现 1001.39;$\pi$ 差 0.00e+00 |

完整表格、量纲分析、两个目标变体的对比与复杂度表见 [NOTES.md](./NOTES.md)。

## 注意事项与常见坑

1. **熵不是"$α$ 越大越单调上升"**。实测熵在 $α≈0.7$ 见顶后**回落并饱和**于 1.3306;
   所以"熵随 $α$ 单调递增"这条断言是**错的**,正确的分层断言是:(a) 熵 ≤ $\ln|A|$;
   (b) 任务回报随 $α$ 单调不增;(c) $α$ 大时熵项压倒回报;(d) 在 $α$ 较小的区间上熵单调上升。
2. **温度更新的符号写反不会报错,只会"收敛到另一个值"**。目标是最小化 $−α(\logπ + H̄)$,
   梯度下降给出 $α ← α − lr\,(H−H̄)$ —— **熵高于目标时 $α$ 要减小**;写成 $+(H−H̄)$ 会正反馈发散
   (判据只能是"三个不同起点是否收敛到同一点",单起点看不出方向错)。
3. **奖励量级与熵预算必须可比(量纲问题,不是超参问题)**。本 demo 早期用 `goal_reward = 1.0` 时,
   $αH̄$ 与任务回报尺度差一个数量级,温度自动调节一路推到 `alpha = 1e-6` 下界;把目标奖励提到 20 后
   两者同量级,更新才稳定收敛(详见 [NOTES.md](./NOTES.md) §4)。
4. **目标熵的口径要写清**。原论文连续控制取 $H̄ = −\dim(\mathcal{A})$(可为负,那里用**微分熵**);
   离散动作 + 香农熵时目标熵必须 ≥ 0。本 demo 取 $H̄ = 0.8318$,$\ln|A| = 1.3863$,**与论文数值不可直接比较**。
5. **`log-sum-exp` 必须减最大值**(否则 `OverflowError`);**$α=0$ 要单独分支**
   (`logsumexp(q/0)` 除零,正确做法是退回 `max`)。
6. **不要把"熵随机"和"探索噪声"混为一谈**:SAC 的随机性来自目标函数本身,探索是策略优化的
   **结果**而不是外加的,这也是它不需要手工噪声调度的原因。

## 参考资料(实际阅读过的权威来源)

- [Haarnoja, Zhou, Abbeel, Levine, *Soft Actor-Critic: Off-Policy Maximum Entropy Deep RL with a Stochastic Actor*(arXiv:1801.01290)](https://arxiv.org/abs/1801.01290)
  —— 最大熵目标、软 Bellman backup、软策略迭代的收敛性证明(初版,SAC v1)。
- [Haarnoja 等, *Soft Actor-Critic Algorithms and Applications*(arXiv:1812.05905)](https://arxiv.org/abs/1812.05905)
  —— **本 demo 主要依据**。温度自动调节的动机原话("a sub-optimal temperature can drastically
  degrade performance")、约束形式与对偶变量、Algorithm 2 的 `Adjust temperature` 一行、
  目标熵 $H̄=−\dim(\mathcal{A})$(HalfCheetah 为 −6)、"the entropy at different states can vary";
  全文经 [ar5iv 渲染版](https://ar5iv.labs.arxiv.org/html/1812.05905)精读。
- [OpenAI Spinning Up, *Soft Actor-Critic*](https://spinningup.openai.com/en/latest/algorithms/sac.html)
  —— 熵正则化 RL 的值函数定义(各文献对"熵奖励放在哪一步"的口径差异)、$α$ 在实践中的选择。
- [OpenAI Spinning Up: *TD3*](https://spinningup.openai.com/en/latest/algorithms/td3.html) /
  [*DDPG*](https://spinningup.openai.com/en/latest/algorithms/ddpg.html) —— 对照基线 DDPG(确定性策略
  梯度)与 TD3(延迟策略更新 + 双 Q + 目标策略平滑),说明 SAC 的"随机策略 + 最大熵"路线替代了什么。
