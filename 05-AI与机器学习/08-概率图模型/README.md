# 概率图模型

> 2026-09-14 类目自动拓展新增(S2)。补齐"能把**结构**写进模型"的这一支:前面的模型都假设
> 样本独立同分布,概率图模型显式地把变量之间的依赖关系(时序、因果、空间邻接)画成图,
> 因而成为 HMM / CRF / LDA / 变分自编码器的共同母语。
> 与 [05-经典机器学习/](../05-经典机器学习/) 的分工:那边是判别式模型(P(x)→y),这里是
> **生成式 + 表示理论**,关心联合分布 `P(X, Y)` 怎么被图结构分解。

## 核心研究主题

- **表示**:贝叶斯网(有向无环图,DAG)、马尔可夫网 / 马尔可夫随机场(无向图)、因子图(二部图统一两者)
- **分解与独立性**:D-分离、局部/成对/全局马尔可夫性、I-map 与等价类(CPDAG)
- **精确推断**:变量消除、信念传播(belief propagation / sum-product)、连接树(junction tree)、
  半环与 max-product(做 MAP)
- **近似推断**:蒙特卡洛采样(Gibbs / Metropolis-Hastings)、变分推断(ELBO、平均场)、
  期望传播(EP)、循环信念传播
- **参数与结构学习**:最大似然 / 贝叶斯估计、EM 算法、BIC / BDeu 评分 + 贪婪搜索、
  稀疏正则化的结构学习
- **经典实例**:HMM(前向-后向、Viterbi)、CRF、LDA / 狄利克雷过程混合、VAE 里的变分下界

## 原理详解

### 1. 两类图与联合分布的分解

**贝叶斯网(有向无环图)**:每个结点只依赖它的父结点,联合分布按有向边**链式分解**:

```
P(X₁, …, Xₙ) = ∏ᵢ P(Xᵢ | parents(Xᵢ))
```

**马尔可夫随机场(无向图)**:无向图没法给方向,改用**团(clique)**上的非负势函数乘积。
团 = 图中任意两点都有边相连的结点子集;极大团 = 再加任何一个结点就不再构成团的团。
联合分布按极大团分解:

```
P(X) = (1/Z) ∏_{C ∈ maximal cliques} ψ_C(X_C)          Z = Σ_X ∏_C ψ_C(X_C)
```

`Z` 称为**规范化因子(配分函数)**,把非负的势函数乘积归一成概率。势函数通常写成
`ψ_C(X_C) = exp(−E(X_C))`,指数形式天然保证非负。

**因子图**是统一两种模型的二部图:一类是变量结点、另一类是函数(因子)结点,同类之间无边。
任意的贝叶斯网或马尔可夫网都能无歧义地转成因子图 —— 有向边 `X→Y` 直接变成因子
`P(Y | parents(Y))`,于是"有向/无向"的差别只体现在因子的形状上,消息传递算法自然统一。

### 2. 条件独立是图模型的核心资产

马尔可夫随机场上有三层等价的马尔可夫性(由强到弱):

| 性质 | 陈述 |
| --- | --- |
| **全局** | 若结点集 `A`、`B` 被 `S` 分离(任一路径必经 `S`),则 `A ⊥ B \| S` |
| **局部** | 给定某结点的全部邻接结点,该结点与其余所有结点条件独立 |
| **成对** | 给定所有其它变量,两个**非邻接**的变量条件独立 |

在多元正态分布假设下,无向图模型的独立性有干净的代数对应:**边缺失 ⟺ 精度矩阵
(协方差矩阵的逆)对应位置为 0**。现代统计里大量无向图模型的理论结果都是在多元正态下取得的。

图上省下的计算量是实打实的:若 100 个二值变量全连接,枚举求边缘分布要累加 `2¹⁰⁰` 项;
而利用图结构(如树)做信念传播是**线性**的。

### 3. 变量消除为什么是"重复计算"的解药

以 `P(A | B=1)` 为例。朴素做法是把联合分布表里所有 `B=1` 的行加起来做分母,再把
`A=0, B=1` / `A=1, B=1` 各加一次做分子 —— 那些行被加了**两遍**。

变量消除的思想就是:**把分母用分子的中间结果算出来**(`p(B=1) = p(A=0,B=1) + p(A=1,B=1)`),
并且一次只对**一个**变量求和、把它消掉,用动态规划复用已算出的中间因子。
复杂度取决于图结构与**消元顺序**:顺序选得好是多项式,最坏情况仍是指数级。

### 4. 信念传播:一次算出所有边缘分布

变量消除一次只能得到一个最终分布。要拿**所有**变量的边缘分布,重复跑变量消除太贵。
信念传播(BP)换个思路:让每个结点检查它的邻居,邻居之间互发**消息**(内含各自的局部
分布),结点把自己的先验与收到的消息聚合起来更新自己的分布,迭代若干轮直到稳定。
对所有结点而言,这等价于"把整张图的信息汇聚到每个点上"。BP 由 Pearl 于 1982 年提出,
最初用于树 / polytree —— **树上 BP 是精确的**;图上有环时只能算近似(循环 BP),是否收敛没有一般保证。

### 5. HMM:结构最简单的动态贝叶斯网

HMM 的变量分两组 —— 隐藏的**状态变量**序列与可观测的**观测变量**序列,依赖关系就两条:

- 观测变量只依赖同时刻的状态变量;
- 状态变量只依赖上一时刻的状态(**马尔可夫链**),与更早的状态无关。

联合分布因此是 `P(x₁)∏P(xₜ | xₜ₋₁) · ∏P(yₜ | xₜ)`,由三组参数完全确定:
初始状态概率 `π`、状态转移概率矩阵 `A`、输出观测概率矩阵 `B`。
它对应的三类经典问题分别对应三个算法:评估(前向-后向)、解码(Viterbi)、学习(Baum-Welch,
即 EM 的特例)。

## 已完成 demo

| 目录 | demo |
| --- | --- |
| [01-变量消除与消元顺序/](./01-变量消除与消元顺序/) | 566 变量消除与消元顺序(min-fill / min-neighbors / Kjærulff H1–H6、induced width，Py 41 断言) |
| [02-连接树与三角化/](./02-连接树与三角化/) | 567 连接树与三角化(MCS-M 最小三角化、极大团与树宽、RIP、Lauritzen-Spiegelhalter 校准，Py 40 断言) |
| [03-循环信念传播/](./03-循环信念传播/) | 568 循环信念传播与收敛判据(dynamic range / Theorem 8 压缩映射 / Simon 条件 / 单环必收敛，Py 54 断言) |
| [04-HMM三算法/](./04-HMM三算法/) | 569 HMM 三算法(前向-后向 + 缩放、Viterbi、Baum-Welch，Py 63 断言) |
| [05-变分推断与平均场/](./05-变分推断与平均场/) | 570 变分推断与平均场(ELBO 分解 Eq 14、CAVI、指数族捷径 Eq 40、平均场低估方差，Py 66 断言) |

> 2026-09-22 首批 5 demo（累计 Py 264 断言全绿）。五个 demo 一律把「近似/启发式」的代价
> 转成可测断言：消元顺序的 fill-in 数、树宽、KL 闭式 `−½ln(1−ρ²)`、Viterbi 与后验解码的
> 路径概率大小关系，而不是停在定性描述。

## 待研究

- [x] 变量消除的消元顺序启发式(min-fill / min-degree)与 induced width 实测(→ [01-变量消除与消元顺序/](./01-变量消除与消元顺序/)，2026-09-22，ID 566)
- [x] 连接树(junction tree)的构造:三角化、弦图、separator 与 message passing 两趟(→ [02-连接树与三角化/](./02-连接树与三角化/)，2026-09-22，ID 567)
- [x] 循环 BP 在含环图上何时收敛、何时振荡(→ [03-循环信念传播/](./03-循环信念传播/)，2026-09-22，ID 568)
- [x] 变分推断的 ELBO 推导与平均场假设的代价(近似后验的独立性假设)(→ [05-变分推断与平均场/](./05-变分推断与平均场/)，2026-09-22，ID 570)
- [x] HMM 三个算法(前向-后向 / Viterbi / Baum-Welch)从零实现(→ [04-HMM三算法/](./04-HMM三算法/)，2026-09-22，ID 569)
- [ ] 结构学习:DAG 搜索的评分等价类、PC / GES 算法
- [ ] 图模型与深度学习的接合:VAE 的 ELBO、normalizing flow 的行列式项
- [ ] 采样路线:Gibbs / Metropolis-Hastings 的混合时间与收敛诊断
- [ ] 期望传播(EP)与变分推断在同一模型上的精度/代价对比

## 参考资料(实际阅读过的来源)

- 周志华《机器学习》第 14 章「概率图模型」(14.1 隐马尔可夫模型 / 14.2 马尔可夫随机场)
  —— 有向图 vs 无向图的分类、HMM 的三组参数 `π/A/B` 与生成过程、团与极大团的定义、
  按极大团分解 `P(X) = (1/Z)∏ψ_C(X_C)` 与规范化因子、分离集与三层马尔可夫性、
  `ψ_C = exp(−E(X_C))` 的指数形式
- [概率图模型 — 百度百科](https://baike.baidu.com/item/%E6%A6%82%E7%8E%87%E5%9C%96%E6%A8%A1%E5%9E%8B/2120179)
  —— Pearl 提出并发展的表示/推理/学习三部分体系;精确推理(变量消除、信念传播)与近似推理
  (蒙特卡洛采样、变分推断)的分野;HMM / CRF / LDA 作为衍生模型;因子图统一有向与无向
- [图模式(概率图模型)— 百度百科](https://baike.baidu.com/item/%E5%9B%BE%E6%A8%A1%E5%BC%8F/23148270)
  —— 无向图模型的条件独立定义、多元正态下精度矩阵与边的缺失对应关系、稀疏结构学习
- [读懂概率图模型:从基本概念和参数估计开始](https://sohu.com/a/207319466_465975)
  —— 变量消除的入门推导:把分母用分子的中间结果算、一次消一个变量的动态规划视角;
  信念传播"每个结点与邻居互发消息并聚合"的直觉解释
- [置信传播 — 搜狗百科](https://baike.sogou.com/v69204990.htm)
  —— BP 又名和-积(sum-product)算法、因子图作为二部图的定义、Pearl 1982 的出处、
  树上收敛到最优解与环上近似性的边界
- Jordan, *Learning in Graphical Models*、Koller & Friedman, *Probabilistic Graphical Models:
  Principles and Techniques*(2009)—— 表示/推断/学习三大块的权威教材(由上列条目索引)
- Blei, Kucukelbir & McAuliffe, [Variational Inference: A Review for Statisticians](https://arxiv.org/abs/1601.00670)
  (2017)—— ELBO 分解 Eq (13)(14)、CAVI Algorithm 1、指数族完全条件的 Eq (36)–(40)、
  §2.5 局部最优与收敛判据(→ 05-变分推断与平均场)
- Ihler, Fisher & Willsky, [Loopy Belief Propagation: Convergence and Effects of Message Errors](https://www.jmlr.org/papers/v6/ihler05a.html)
  (JMLR 2005)—— dynamic range 度量、`d(ψ)²` 势强度、Theorem 8 压缩映射、
  Theorem 10(Simon)与 Theorem 11 的导数判据、单环图唯一不动点(→ 03-循环信念传播)
- pgmpy [`EliminationOrder.py`](https://raw.githubusercontent.com/pgmpy/pgmpy/dev/pgmpy/inference/EliminationOrder.py)
  与 [`ExactInference.py`](https://raw.githubusercontent.com/pgmpy/pgmpy/dev/pgmpy/inference/ExactInference.py)
  —— `fill_in_edges`、MinFill/MinNeighbors/MinWeight/WeightedMinFill、`_Kjaerulff` H1–H6、
  `induced_width`(最大团 − 1)、`BeliefPropagation._update_beliefs` 的 `β *= σ/μ; μ ← σ`
  与 `_calibrate_junction_tree` 两趟(→ 01 / 02)
- networkx [`algorithms/chordal.py`](https://raw.githubusercontent.com/networkx/networkx/main/networkx/algorithms/chordal.py)
  —— `is_chordal`(MCS 判定)、`chordal_graph_cliques`、`chordal_graph_treewidth`
  (doctest:`barbell_graph(4,6)` 树宽 3)、`complete_to_chordal_graph` 的 MCS-M 最小三角化(→ 02)
- Jurafsky & Martin, *Speech and Language Processing* (3rd ed.)
  [附录 A: Hidden Markov Models](https://web.stanford.edu/~jurafsky/slp3/A.pdf)
  —— Eq A.7 `P(3 1 3｜hot hot cold) = .4×.2×.1 = 0.008`、Fig A.2 冰激凌 HMM、
  前向 / Viterbi 递推(→ 04-HMM三算法)
- hmmlearn [`src/hmmlearn/base.py`](https://raw.githubusercontent.com/hmmlearn/hmmlearn/main/src/hmmlearn/base.py)
  —— `_compute_posteriors_scaling`(`fwd*bwd` 后逐行归一化)、`_decode_map`(`argmax` 后验，
  与 Viterbi 是两件事)、`_accumulate_sufficient_statistics_scaling`(长度为 1 的样本不更新 `A`)(→ 04)
- scikit-learn [`sklearn/decomposition/_lda.py`](https://raw.githubusercontent.com/scikit-learn/scikit-learn/main/sklearn/decomposition/_lda.py)
  —— `_update_doc_distribution` 的 `norm_phi = exp(E[log θ])·exp(E[log β]) + eps`
  与「先验加到自然参数上」、`_approx_bound` 里的 `logsumexp`(→ 05)
