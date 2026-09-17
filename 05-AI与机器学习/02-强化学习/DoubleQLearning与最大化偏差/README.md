# Double Q-learning 与最大化偏差

## 简介

`max_a Q(s,a)` 里的 `max` 作用在**带噪声的估计**上时,得到的是 `max_a E[Q(s,a)]` 的**上偏**
估计 —— 这就是最大化偏差(maximization bias),也叫 RL 版的 winner's curse。Double Q-learning
用**两组独立估计**把"选动作"和"打分"拆开,消掉这个偏差:一组选 `argmax`,另一组给这个动作打分。

关键概念:

| 概念 | 一句话解释 |
| --- | --- |
| 单估计量 | 用同一组估计既选 `argmax` 又取它的值 → 系统性高估 |
| 双估计量 | 用 `μ^A` 选 `a*`,再用**独立**的 `μ^B` 给 `a*` 打分 → 无偏(或轻微低估) |
| `Q_A` / `Q_B` | Double Q-learning 里的两张表;每步随机选一张更新,用另一张算自举目标 |
| 最大化偏差的后果 | 高估不均匀 → 相对动作优劣被扭曲 → 学到的策略变差 |
| Double DQN | 把双估计量搬到 DQN:`θ` 选动作、`θ⁻` 打分,零额外参数 |

历史:偏差现象与算法由 van Hasselt 在 NIPS 2010 提出([NeurIPS 2010 论文](https://proceedings.neurips.cc/paper_files/paper/2010/file/091d584fced301b442654dd8c23b3fc9-Paper.pdf));
2015 年同一作者证明 **DQN 在 Atari 上也在系统性高估**,并给出 Double DQN
([arXiv:1509.06461](https://arxiv.org/abs/1509.06461))。

## 原理详解

### 1. 偏差从哪来

设真实动作值 `Q*(s,a) = V*(s)`(所有动作一样好),估计误差 `ε_a` 均值为 0。则

```
E[ max_a (V* + ε_a) ] = V* + E[max_a ε_a] > V*          (只要 m ≥ 2 且 ε 非常数)
```

`max` 是凸函数,和期望不可交换(Jensen 不等式),所以**即使每个动作的估计都是无偏的**,
取 max 之后仍然偏高。误差上限为 `[−1,1]` 上独立均匀时可解析算出 `E[max_a ε_a] = (m−1)/(m+1)`
(m=2: 1/3,m=5: 2/3,m=10: 9/11,m→∞: 1);标准正态误差时按 `√(2 ln m)` 增长 ——
原文 Figure 1 的橙色柱就是这个:**动作数越多,高估越严重**,而蓝色柱(双估计量)几乎为零。

### 2. 双估计量为什么无偏(以及它什么时候会低估)

Lemma 1(NIPS 2010):`μ^A`、`μ^B` 两组独立无偏估计,`a* = argmax_a μ^A_a`,则

```
E[ μ^B_{a*} ] = E[X_{a*}] ≤ max_i E[X_i]
```

**等号成立当且仅当 `P(a* ∉ M) = 0`**,`M = { j | E[X_j] = max_i E[X_i] }`。
直觉:`μ^B` 与"谁被选中"独立,所以不存在"挑到最大值"这一步;但若 `μ^A` 会**选错**真正的最优动作,
`μ^B` 就诚实地报告了一个次优动作的值 → 低估。本 demo 的 E4 扫噪声强度 σ 测这个充要条件
(σ=0.2 时 `P(a*∉M)=0.000` 不低估;σ=2.0 时 0.616 严格低估,完整表见 [NOTES.md](./NOTES.md) §1)。
这也是为什么 Double Q-learning **不是高估问题的完整解**:它把高估换成了另一种误差。

### 3. 高估的规模有下界,而且这个下界是紧的(Theorem 1)

Double DQN 论文 Theorem 1:若 `Σ_a (Q_t(s,a) − V*(s)) = 0`(整体无偏)且
`(1/m)Σ_a (Q_t(s,a) − V*(s))² = C`,则 `max_a Q_t(s,a) ≥ V*(s) + √( C / (m−1) )`。

本 demo 用两条互补路子核它(4000 个随机可行点逐个验算,残差 **2.84e-14**):

1. **代数分解**:令 `t = max_a ε_a`、`δ_a = t − ε_a ≥ 0`,得 `Σδ_a = m·t`、`Σδ_a² = mC + mt²`,
   再由 `Σδ_a² ≤ (Σδ_a)²` 合并即得 `t ≥ √(C/(m−1))`;
2. **紧性**:该不等式在 `δ` 只有一个非零分量时取等,`m=5, C=1` 时 `ε = (0.5,0.5,0.5,0.5,−2.0)`
   恰好取到 `max ε = 0.5 = √(1/4)`。

注意 Theorem 1 只给**下界**,且下界随 m **减小**,而典型高估随 m **增大**(Figure 1);
两者不矛盾 —— 下界要求"恰好达到极端的 ε 分布",随机误差通常达不到。推导全文见 [NOTES.md](./NOTES.md) §2。

### 4. Double Q-learning(原文 Algorithm 1)

```
repeat
   用 Q_A 与 Q_B 的均值做 ε-greedy 选动作 a,观测 r, s'
   以 1/2 概率选 UPDATE(A) 或 UPDATE(B)
   if UPDATE(A):
       a* = argmax_a Q_A(s', a)
       Q_A(s,a) ← Q_A(s,a) + α [ r + γ Q_B(s', a*) − Q_A(s,a) ]
   else:
       b* = argmax_a Q_B(s', a)
       Q_B(s,a) ← Q_B(s,a) + α [ r + γ Q_A(s', b*) − Q_B(s,a) ]
   s ← s'
```

三个要点:① `Q_A` 用 `argmax Q_A` 选动作、却用 `Q_B` 的**值**打分 —— 选择与评估分离;
② 每次更新**只动一张表**,两张表各自积累独立样本;
③ 选动作时用两表**均值**(原文:we calculated the average of the two Q values for each action and
then performed ε-greedy exploration with the resulting average Q values),数据效率不比 Q-learning 差。

### 5. Double DQN(把双估计量搬到函数逼近)

```
Q-learning:  Y_t = R_{t+1} + γ · Q( S_{t+1}, argmax_a Q(S_{t+1},a;θ_t) ; θ_t )
Double DQN:  Y_t = R_{t+1} + γ · Q( S_{t+1}, argmax_a Q(S_{t+1},a;θ_t) ; θ⁻_t )
                                                  ↑用 θ 选            ↑用 θ⁻ 打分
```

原文 Eq.(4) 即 `Y_t^{DoubleQ} ≡ R_{t+1} + γ Q(S_{t+1}, argmax_a Q(S_{t+1},a;θ_t); θ′_t)`;
区别在于第二组估计 `θ′_t` 在 Double DQN 里直接复用目标网络 `θ⁻_t`(原文 p.4 明确说明)。
DQN 的完整机制见同级 [DQN经验回放与目标网络/](../DQN经验回放与目标网络/)。

### 6. 网格世界实验(原文 §4.2)

起点左下、终点右上,出界原地不动;**非终止步奖励是 −12 或 +10,等概率**(期望 −1);
终点每个动作都给 +5 并结束;`γ = 0.95`;`ε(s) = 1/√n(s)`;`α = 1/n(s,a)` 或 `1/n(s,a)^0.8`。
最优策略 5 步结束 → 最优平均每步回报 `+0.2`、起点最优值 `5γ⁴ − Σ_{k=0}^{3}γ^k ≈ 0.3626`。

## 对比 / 选型

| 算法 | 高估/低估 | 额外成本 | 备注 |
| --- | --- | --- | --- |
| Q-learning | 系统**高估** | — | 表格版在高噪声、多动作时最明显 |
| Double Q-learning | 无偏或轻微**低估** | 一倍内存,每步只更一组 | 原文 Theorem 1 保证收敛到 `Q*` |
| Double DQN | 显著减小高估 | 0(DQN 已有目标网络) | DQN 的默认替代,原文实测在多个 Atari 游戏上更好 |
| 乐观初始化 / 乐观探索 | 故意高估 | — | "face of uncertainty" 是正交概念:它高估**未试过**的动作,最大化偏差高估的是**试过很多次**的动作 |

## 环境准备

- 操作系统:任意(本 demo 在 Windows + Git Bash 下运行)
- Python:3.13(仅标准库 `math` / `random` / `sys`)
- 依赖:无

## 运行方式

```bash
cd python
python main.py     # 退出码 0 = 全部断言通过(PASS=21 FAIL=0)
```

## 关键代码片段

```python
# double_q_core.py —— Algorithm 1 的核心 12 行
class DoubleQLearning:
    def update(self, s, a, r, s2, done):
        if self.rng.random() < 0.5:                       # ① 抛硬币选更新哪张表
            self.nA[s][a] += 1
            q = self.QA[s][a]
            # ② 用 QA 选动作、用 QB 打分;终止转移不自举
            bootstrap = 0.0 if done else self.QB[s2][self._argmax(self.QA[s2])]
            target = r + self.gamma * bootstrap
            self.QA[s][a] = q + self.alpha_fn(self.nA[s][a]) * (target - q)
        else:
            self.nB[s][a] += 1
            q = self.QB[s][a]
            bootstrap = 0.0 if done else self.QA[s2][self._argmax(self.QB[s2])]
            target = r + self.gamma * bootstrap
            self.QB[s][a] = q + self.alpha_fn(self.nB[s][a]) * (target - q)

    def act(self, s, eps):                                # ③ 用两表均值做 ε-greedy
        if self.rng.random() < eps:
            return self.rng.randrange(len(self.QA[s]))
        return max(range(len(self.QA[s])),
                   key=lambda i: 0.5 * (self.QA[s][i] + self.QB[s][i]))
```

Theorem 1 的代数自查函数 `bound_decomposition(C, m, eps)`(核对 `Σδ = m·t`、`Σδ² = mC+mt²`
与 `mC+mt² ≤ m²t²` 三条恒等式)见 [NOTES.md](./NOTES.md) §2。

## 性能与边界

实测(`python main.py`,全部断言 PASS=21 FAIL=0,总耗时约 40 秒):

- **E1 单估计量偏差**(σ=1 均匀误差):MC 与闭式 `(m−1)/(m+1)` 最大差 **0.0024**;
  双估计量在所有 m 上都在 0 附近(量级 1e-3)。
- **E2 Theorem 1**:4000 个随机可行点的代数恒等式残差 **2.84e-14**;紧性构造 `m=5,C=1` 恰好取到 0.5。
- **E3 网格世界**(真值 `V*(S) = 0.3627`,200 运行 × 10000 环境步):Q-learning 起点 `max Q` 被抬到
  **+7.74**(`1/n^0.8`)/ **+14.45**(`1/n`),即真值的 20~40 倍;Double Q-learning 策略回报
  **+0.188 ≈ 最优的 +0.2**,而 Q-learning 只有 −1.02(低于随机);但 Double Q 的**数值**并不更准(−5.47),
  因为它估计的是带 ε 探索的行为策略的 Q。
- **E4 Lemma 1**:`P(a*∉M)` 与"严格低估"**逐行一致**(σ=0.2 时 0.000 不低估,σ=2.0 时 0.616 低估)。

完整表格(E1/E3/E4)、Theorem 1 推导全文、复杂度分析与开发期踩坑见 [NOTES.md](./NOTES.md)。

## 注意事项与常见坑

1. **"学习步"≠"回合"**。第一版把原文 "10,000 learning steps" 实现成 10,000 个**回合**,而
   `ε(s)=1/√n(s)` 早期接近 1、单回合能拖到 200 步 → 实算约 200 万环境步,一次实验 7 分半没跑完。
   改成累计**环境步**后同一实验 **7 秒**结束。**写实验前先估量级:回合数 × 每回合步数 × 运行数。**
2. **数值积分求 `E[max]` 时漏掉负半轴项**。正确形式 `∫₀^∞[1 − Φ(x)^m − (1−Φ(x))^m]dx`,第二项
   m=2 时占 `E[max]` 的 1/6,漏掉会把 `1/√π = 0.5642` 算成 `0.6810`(与 MC 差 0.1204)——
   这是本 demo 唯一一处**断言失败其实是断言自己的 bug**。
3. **`α = 1/n` 会让高估随时间收缩**。E3 里 Q-learning 3000 步时高估 +12.4、10000 步时 +7.4;
   报告数字必须写清步数,否则不同实现之间不可比。
4. **不要用"高估更小"当作 Double Q-learning 更好的论据**。它的 Q 值可能**大幅低估**(本 demo −5.5);
   真正的判据是策略质量(平均回报),不是值估计的绝对误差。
5. **Lemma 1 的等号条件要看住**。`P(a*∉M)=0` 时它退化成与单估计量一样无偏,E4 里 σ=0.2 就是这种情况。
6. **口径差异**:E1/E2 是纯估计问题(直接对 ε 做 Monte Carlo),E3 才回到完整 RL 循环;E3 网格是 3×4,
   原文配图的具体形状未在正文给出,本 demo 以"最优值 ≈0.36"与"最优每步回报 = +0.2"两个正文数字对齐口径。

## 参考资料(实际阅读过的权威来源)

- [van Hasselt, *Double Q-learning*, NIPS 2010(NeurIPS 会议论文集 PDF)](https://proceedings.neurips.cc/paper_files/paper/2010/file/091d584fced301b442654dd8c23b3fc9-Paper.pdf)
  —— Lemma 1(低估方向与充要条件)、Algorithm 1、Theorem 1、§4.1 轮盘赌与 §4.2 网格世界的完整设置。
- [van Hasselt, Guez, Silver, *Deep Reinforcement Learning with Double Q-learning*(arXiv:1509.06461)](https://arxiv.org/abs/1509.06461)
  —— Theorem 1 下界 `√(C/(m−1))`、"均匀误差时高估为 `(m−1)/(m+1)`"(Figure 1 闭式)、
  Double DQN 目标式与 Eq.(4) 的关系。全文经 [ar5iv 渲染版](https://ar5iv.labs.arxiv.org/html/1509.06461)精读。
- [Sutton & Barto, *Reinforcement Learning: An Introduction*(1st ed., 在线版)§8.5 Off-Policy Bootstrapping](http://incompleteideas.net/book/first/ebook/node90.html)
  —— off-policy 自举 + 函数逼近的发散反例(Baird 6 状态例):与最大化偏差互为"两种不同的病"
  (前者是稳定性问题,后者是**偏差**问题)。
- [PyTorch, *Reinforcement Learning (DQN) Tutorial*](https://docs.pytorch.org/tutorials/intermediate/reinforcement_q_learning.html)
  —— 工业实现里 `done` 掩码与目标网络的写法,用于交叉核对本 demo 的终止态处理。
