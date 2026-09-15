# t-SNE 与 UMAP

## 简介

降维可视化里最容易"看错"的两个算法。它们都拿高维相似度去逼近低维布局,但**都不保证任何
全局几何**,所以"图上两团离得近 ⇒ 数据里也近"这类推论经常不成立。本 demo 用可量化指标
复查 Wattenberg / Viégas / Johnson 在 Distill 上的 *How to Use t-SNE Effectively*(2016)提出的
四条告诫,并用 UMAP 做对照。

关键概念:

| 概念 | 一句话 |
| --- | --- |
| `perplexity` | 相当于"每个点的有效近邻数",由逐点二分搜索高斯带宽得到;原论文说常用 5~50 |
| 重尾核 | 低维相似度用自由度为 1 的 t 分布 `(1+d²)^{-1}`,把中距离的点拉开,解决拥挤问题 |
| 密度均衡 | `P` 按局部密度自适应 ⇒ 稀疏簇被收缩、稠密簇被扩张,**簇大小不可读** |
| 早期夸张 | 前若干步把 `P` 乘 12,先让簇成形再 relax;没收敛就停会得到怪形状 |
| UMAP 两阶段 | ① 构造模糊拓扑表示(kNN + 局部连通性 + 对称化)② 交叉熵 + 负采样 SGD 优化布局 |

历史背景:t-SNE 由 van der Maaten & Hinton 于 2008 年发表在 JMLR(9:2579−2605),是对
Hinton & Roweis 2002 年 SNE 的改良;UMAP 由 McInnes、Healy、Melville 于 2018 年提出
(arXiv:1802.03426),官方称其"competitive with t-SNE for visualization quality, and arguably
preserves more of the global structure with superior run time performance"。

## 原理详解

1. **高维相似度**:对每个点 `i`,`p_{j|i} ∝ exp(-‖x_i-x_j‖²/2σ_i²)`,σ_i 由二分搜索确定,
   使条件分布 `P_i` 的熵等于 `log(perplexity)` —— 即"有效近邻数"。
2. **对称化**:`p_ij = (p_{j|i} + p_{i|j})/(2n)`,保证 `Σ_ij p_ij = 1`。
3. **低维相似度**:`q_ij ∝ (1+‖y_i-y_j‖²)^{-1}`(自由度为 1 的 t 分布)。重尾意味着
   中距离的点之间排斥力弱,点不会被"挤到中心"。
4. **目标函数**:`KL(P‖Q) = Σ p_ij log(p_ij/q_ij)`。注意 KL 的两个性质直接决定了读图规则:
   - `p_ij` 大而 `q_ij` 小 → 罚得重 ⇒ **高维近的点在低维必须近**
   - `p_ij` 小而 `q_ij` 大 → 罚得轻 ⇒ **高维远的点可以在低维很近**(簇间距离因此不可信)
5. **梯度下降**:`∂KL/∂y_i = 4Σ_j (p_ij - q_ij)(1+‖y_i-y_j‖²)^{-1}(y_i-y_j)`,
   带动量;前 100 步用早期夸张 + 动量 0.5,之后动量 0.8。
6. **UMAP 的两阶段(官方 *How UMAP Works*)**:阶段一构造"模糊单纯复形"(kNN 图,边权
   `exp(-(d_ij-ρ_i)/σ_i)`,`ρ_i` 是到最近邻的距离、`σ_i` 由 `Σ_j w_ij = log2(k)` 定出,
   再用概率 t-conorm `a+b-ab` 对称化);阶段二把两张图的 1-单纯形权重当 Bernoulli 概率,
   用**交叉熵**做度量 —— 第一项是吸引力,第二项是排斥力,整体是力导向布局,
   并用**负采样**把排斥项从 O(n²) 降到 O(n·n_neg)。低维核 `1/(1+a·d^{2b})` 里的
   `a`,`b` 由 `min_dist`/`spread` 最小二乘拟合得到。

## 实测结果(本机,纯 Python,120~200 点)

**实验 1:簇的大小不可读。** 真实点数 40 vs 160(4 倍),

```
嵌入 RMS 半径 10.34 vs 18.10 → 比值 1.75
```

4 倍的规模差在图上只剩 1.75 倍。原因是 t-SNE 会"按局部密度自适应":官方 Distill 原文
"it naturally expands dense clusters, and contracts sparse ones, evening out cluster sizes …
you cannot see relative sizes of clusters in a t-SNE plot"。

**实验 2:纯随机噪声也能长出团块。** 100 维标准高斯(毫无结构)150 点:

```
perplexity   抱团点占比   近邻距离 p90/p10   KL(P‖Q)
       2.0       0.107             3.56   3.62
       5.0       0.040             3.17   3.14
      30.0       0.000             1.81   2.45
     100.0       0.000             1.36   1.26
```

`perplexity=2` 时有 10.7% 的点被挤成小团块;`perplexity=100` 时是 0(均匀铺开)。
有意思的是**高 perplexity 下的"太平"才是真相**:Distill 指出高维高斯"very close to uniform
distributions on a sphere",所以看似"均匀得可疑"的图反而比线性投影更准确。

**实验 3:簇间距离 / perplexity / 与 UMAP 对照。** 三簇,真实质心距离
`[22.4, 111.8, 89.4]`(最远/最近 = 4.0 倍):

```
method              嵌入质心距离               与真实距离的相关系数
t-SNE  P=2.0        [52.14, 45.75, 72.97]     +0.055
t-SNE  P=30.0       [49.7, 55.24, 56.11]      +0.933
t-SNE  P=100.0      [0.67, 5.37, 4.72]        +0.994
UMAP a=1.63 b=0.88  [16.84, 17.72, 19.23]     +0.608
```

`perplexity=2` 时,真实里 5 倍的距离差被压成 `52.14 / 45.75 / 72.97`(最远/最近只剩 1.4 倍),
相关系数掉到 0.06 —— 这正是 Distill 说的 "distances between well-separated clusters in a
t-SNE plot may mean nothing"。**但两点必须说清楚**:① 相关系数只有 3 个点,只能看量级趋势;② 本 demo 的
UMAP 是"按官方文档机制实现的教学版"(随机初始化、200 轮、无谱初始化),不是
`umap-learn` 的等价实现,**不能据此判断 UMAP 与 t-SNE 谁更强**。它在这里只用来展示
"低维核 + 交叉熵 + 负采样"这套机制长什么样。

**实验 4:不收敛就别读图。** 同一次运行的不同步数快照(两簇各 60 点):

```
步数   簇A半径  簇A细长   簇B半径  簇B细长   KL(P‖Q)
   10     28.13    1.06     24.66    1.15   2.948
   20      4.63    1.03      5.20    1.34   2.341
   60      5.57    1.28      4.63    1.25   1.828
  120     15.69    1.24     20.27    1.39   1.642
  600      1.59    1.08      1.61    1.15   0.376
```

Distill 描述的现象是"seemingly 1-dimensional and even pointlike images of the clusters"
(细长/点状)。在本实现(lr=200、早期夸张 100 步)里,早停表现为**簇尺度剧烈摆动**
(半径 28→4.6→5.6→15.7→1.6),细长程度反而一直接近 1;而 `KL` 从 2.948 单调降到
0.376、前 120 步都没稳定。两种表现形式不同,但结论一致:**"there's no fixed number of steps
that yields a stable result",必须迭代到构型不再变化再读图**。

## 对比 / 选型

| 维度 | t-SNE | UMAP | PCA |
| --- | --- | --- | --- |
| 保住的几何 | 局部邻域(簇内) | 局部 + 更多全局 | 全局线性结构 |
| 簇间距离 | 不可信(perplexity 全局参数) | 相对好一些,但仍非度量 | 可信(线性) |
| 主要超参 | `perplexity`(5~50) | `n_neighbors`、`min_dist` | `n_components` |
| 新数据 | 无 `transform`(需近似方法) | 有 `transform` | 有 `transform` |
| 该拿来做什么 | 看簇是否分开、找局部结构 | 同左,更大数据 | 看方差结构、做下游特征 |

## 环境准备

- Python 3.8+(**纯标准库**,无 numpy/sklearn);Go 1.21+(只做 t-SNE 主干与三组读图实验)
- 纯 Python 的复杂度是 O(n²·迭代数),所以样例控制在 120~200 点、200~600 步(约 30 s)

## 运行方式

```bash
python3 python/main.py     # 四个实验,约 30 s
cd go && go run .          # t-SNE 主干 + 簇大小/噪声团块/收敛三组实验
```

## 关键代码

perplexity 二分搜索(每条 CPU 指令都在解释"perplexity 是什么"):

```python
entropy = math.log(s) + beta * Σ_j D2[i][j] * row[j] / s   # H(P_i) = logZ + β·Σd²p
if abs(entropy - target) < tol: break                      # target = log(perplexity)
if entropy > target: lo = beta                             # 太平 → 增大 beta
else:                hi = beta                             # 太尖 → 减小 beta
```

与 t-SNE 不同,UMAP 的两个阶段都不含"全局几何"的硬约束,只在**交叉熵**下对齐两张模糊图:

```python
# 吸引项(高维权重大 ⇒ 低维距离要小)
coeff = -2*a*b*d2**(b-1) / (1 + a*d2**b) * w
# 排斥项(负采样抽到的随机点对;分母的 0.001 是数值稳定项)
coeff = 2*b / ((0.001 + d2) * (1 + a*d2**b)) * w
```

## 性能边界

- **别用纯 Python 实现跑真实数据**:t-SNE 每步都是 O(n²)(含高维部分 O(n²·dim)),
  本 demo 150 点 × 200 步约 4 s,乘到 5000 点就是几个数量级的差距。生产实现用
  树/近似近邻 + BLAS。
- **perplexity 必须明显小于点数**:官方注明 perplexity 应当小于点数,否则实现行为不可预期。
- **UMAP 的负采样把排斥项降到 O(n·n_neg)**,这是它能比 t-SNE 快一个量级的直接原因;
  本 demo 的 `n_negative=5`、`n_epochs=200` 是缩水设定。
- **两种算法都不保距离**:低维坐标的绝对尺度由学习率/初始化决定(实验 3 里
  `perplexity=100` 的布局整体缩到 [0.67, 5.37] 就是这个原因),只有**相对**结构有意义。

## 注意事项与常见坑

1. **不要在 t-SNE 图上比簇大小、比簇间距**。这两条是最常见的误读来源。
2. **别调 perplexity 去"找结构"**:低 perplexity 会把随机噪声画成簇(实验 2),看到"惊喜"
   先怀疑超参。
3. **迭代到稳定再读图**;不同数据集需要的步数不同,KL 还在明显下降就说明没收敛。
4. **多个 perplexity 一起看**:Distill 建议对拓扑(如环状结构)要看多张图,因为
   "topology may need more than one plot"。
5. **t-SNE 没有 `transform`**:新数据要用近似方法(如 openTSNE),直接对拼接数据重跑会
   得到完全不同的布局。
6. **实现细节差异极大**:初始化(随机 vs PCA)、早期夸张步数、学习率、平方根/三角
   不等价实现都会给出不同的图;本 demo 的数值不能拿来当"t-SNE 的标准输出"。
7. **UMAP 的 `min_dist` 决定"簇内能贴多紧"**,而 `n_neighbors` 更像 t-SNE 的 perplexity;
   两个参数的作用不要混。

## 参考资料(实际阅读过的来源)

- [Wattenberg, Viégas, Johnson, *How to Use t-SNE Effectively*, Distill, 2016](https://distill.pub/2016/misread-tsne/)
  —— 四条告诫的原文出处:"Cluster sizes in a t-SNE plot mean nothing"、"Distances between
  clusters might not mean anything"、"Random noise doesn't always look random"、
  "You can see some shapes, sometimes"、"For topology, you may need more than one plot";
  以及"iterate until reaching a stable configuration"、"If you see a t-SNE plot with strange
  pinched shapes, chances are the process was stopped too early"、
  "perplexity really should be smaller than the number of points"、
  "very close to uniform distributions on a sphere"
- [van der Maaten & Hinton, *Visualizing Data using t-SNE*, JMLR 9:2579−2605, 2008](https://jmlr.org/papers/v9/vandermaaten08a.html)
  —— t-SNE 原始论文摘要:"a variation of Stochastic Neighbor Embedding … much easier to
  optimize, and produces significantly better visualizations by reducing the tendency to crowd
  points together in the center of the map";多尺度结构与大规模数据的随机游走方案
- [McInnes, Healy, Melville, *UMAP: Uniform Manifold Approximation and Projection*, arXiv:1802.03426](https://arxiv.org/abs/1802.03426)
  —— 摘要:"constructed from a theoretical framework based in Riemannian geometry and
  algebraic topology … competitive with t-SNE for visualization quality, and arguably preserves
  more of the global structure with superior run time performance"
- [UMAP 官方文档 *How UMAP Works*](https://umap-learn.readthedocs.io/en/latest/how_umap_works.html)
  —— 两阶段流程、模糊单纯复形与边权组合、`min_dist` 作为"最近邻距离"的全局接受、以
  Bernoulli 变量解释单纯形权重、**用交叉熵作为度量**、吸引力/排斥力的力导向解释、
  NN-Descent 近似近邻
