# 特征工程与降维

> 2026-09-14 类目自动拓展新增(S2)。补齐 `05-AI与机器学习` 里"喂给模型的数据长什么样"这一环。
> 与 [05-经典机器学习/](../05-经典机器学习/) 的分工:那边讲**模型怎么学**,这里讲**输入怎么造**——
> 同一批数据换个编码方式,树模型与线性模型的差距可以差出十几个点。

## 核心研究主题

- **线性降维**:PCA(协方差特征分解 / SVD 两条路线)、TruncatedSVD(不中心化,可直接吃稀疏矩阵)、
  IncrementalPCA(分块 `partial_fit`,外存)、随机化 SVD(Halko et al. 2009)
- **非线性降维**:KPCA / t-SNE / UMAP、LLE、Isomap(流形学习)
- **特征提取与分解**:FA(因子分析)、ICA、NMF(非负矩阵分解)、LDA(作为有监督降维)
- **特征选择**:Filter(方差/互信息/卡方)/ Wrapper(RFE)/ Embedded(L1、树的重要性)
- **编码与缩放**:One-Hot、Target/Mean Encoding 与其泄漏风险、StandardScaler / MinMax / Robust /
  分位数变换、Box-Cox 与 Yeo-Johnson 幂变换、分箱与样条
- **泄漏与一致性**:`Pipeline` 内做拟合、时间序列上的因果特征、train/test 统计量隔离

## 原理详解(PCA 的两种解法)

PCA 的目标是"用 `k` 个正交方向尽可能多地解释方差"。两条等价路线,复杂度与数值性质都不同:

**路线一:协方差矩阵的特征分解。** 中心化后求 `C = XᵀX/(n−1)`,取前 `k` 个特征向量。
sklearn 的 `covariance_eigh` solver 走这条路,`explained_variance_` 直接就是 `C` 的前 `k` 个
特征值(文档原文:equal to `n_components` largest eigenvalues of the covariance matrix of X)。
代价是**要显式物化 `p×p` 的协方差矩阵**,`p` 大时内存吃不消,且文档明确提醒该 solver 相比
`full` 会把条件数**翻倍**、数值稳定性更差。

**路线二:对数据矩阵直接做 SVD。** `X = UΣVᵀ`,`components_` 就是 `Vᵀ` 的前 `k` 行
(文档:the right singular vectors of the centered input data),`singular_values_` 即 `Σ` 的对角元。
不需要 `p×p` 矩阵,是 `full` solver(LAPACK)的做法。

**路线三:随机化截断 SVD(Halko et al. 2009)。** 当我们**只要前 `k` 个**方向(典型如
4096 维人脸图降到 200 维)时,完整 SVD 是浪费。做法是用随机矩阵先把 `X` 投影到一个
`k + n_oversamples` 维的子空间(`n_oversamples` 默认 10,文档解释为"额外的随机向量数,
用来保证子空间条件数良好"),再做 `iterated_power` 次幂迭代增强主方向,最后在小矩阵上做 SVD。
文档给出的复杂度对比很直观(记 `nmax = max(n_samples, n_features)`、`nmin` 为较小者):

```
随机化 PCA : O(nmax² · n_components)        内存 ≈ 2 · nmax · n_components
精确  PCA  : O(nmax² · nmin)                内存 ≈ nmax · nmin
```

**两个容易忽略的点**:

1. **PCA 只中心化、不缩放**(文档原文:PCA centers but does not scale the input data for each
   feature)。单位不一致的特征会直接决定谁是主成分 —— 所以实践中几乎总是先 `StandardScaler`。
   需要"不中心化"的场景要用 `TruncatedSVD`。
2. **`inverse_transform` 在 `randomized` 下不是精确逆**(文档明确声明:not the exact inverse
   transform of `transform` even when `whiten=False`)。压缩再重建会丢信息,别拿重建误差当无损校验。

`whiten=True` 会把各分量除以奇异值、变成单位方差。文档指出它**会去掉分量间的相对方差信息**,
但在下游模型对信号各向同性有强假设时反而提点 —— 原文举的例子正是 **RBF 核 SVM 与 K-Means**。

**如何选 `k`**:`n_components='mle'` 配 `svd_solver='full'` 会用 Minka 的 MLE 自动猜维数
(文档:"Use of `n_components == 'mle'` will interpret `svd_solver == 'auto'` as `svd_solver == 'full'`");
也可以给 `0 < n_components < 1` 表示"保留这么多比例的方差";或者直接看累计
`explained_variance_ratio_` 到 95% 的拐点。

## 已完成 demo

| # | demo | 知识点 | 语言 |
| --- | --- | --- | --- |
| 1 | [PCA的SVD与协方差路线/](./PCA的SVD与协方差路线/) | PCA 两条路线的数值对比:条件数翻倍(κ vs κ²/2)、大 `p` 内存、只中心化不缩放 | Python / Go / C |
| 2 | [随机化SVD/](./随机化SVD/) | `n_oversamples` / `n_iter` / `power_iteration_normalizer` 对近似误差与子空间 `sinθ` 的影响 | Python / Go |
| 3 | [t-SNE与UMAP/](./t-SNE与UMAP/) | 自写 t-SNE(困惑度二分)与 mini-UMAP:簇大小无意义、噪声成团、簇间距可信度、早停形态 | Python / Go |
| 4 | [Target编码与泄漏/](./Target编码与泄漏/) | 泄漏三层次、平滑、K 折交叉拟合、CatBoost 有序统计;高基数噪声特征上量化泄漏 | Python / Go |
| 5 | [Pipeline与ColumnTransformer防泄漏/](./Pipeline与ColumnTransformer防泄漏/) | 特征选择/缩放泄漏、`remainder` 三种语义、嵌套 CV | Python / Go |

**这一批的共同主线**:*任何用 `y` 学参数的步骤——选择、编码、缩放、超参搜索——都必须关进
`Pipeline` 或内层 CV。* 5 个 demo 分别量化了这条规则在 5 个环节上的违反代价。

## 待研究

- [x] PCA 的 SVD 与协方差两条路线的数值对比(条件数、大 `p` 下的内存实测)→ 见 demo 1
- [x] 随机化 SVD 的 `n_oversamples` / `iterated_power` 对近似误差的影响 → 见 demo 2
- [x] t-SNE 与 UMAP 的对比:哪些结论是可信的、哪些是"可视化幻觉" → 见 demo 3
- [x] Target Encoding 的泄漏机理与 K 折内编码 / 平滑 → 见 demo 4
- [x] `ColumnTransformer` + `Pipeline` 防止交叉验证泄漏的完整范式 → 见 demo 5
- [ ] 特征选择的稳定性:不同随机种子选出的特征集合能重叠多少
- [ ] 幂变换(Box-Cox / Yeo-Johnson)与分位数变换在偏态特征上的实际收益
- [ ] 分箱与样条:等频 vs 等宽 vs 有监督分箱的偏差/方差权衡
- [ ] 稀疏矩阵路线:`TruncatedSVD` 不中心化的代价,以及 `IncrementalPCA` 的 `partial_fit` 一致性
- [ ] 时间序列的因果特征:滚动统计量在 CV 里的对齐陷阱

## 参考资料(实际阅读过的来源)

- [scikit-learn 1.9 §2.5 Decomposing signals in components](https://scikit-learn.org/stable/modules/decomposition.html)
  —— PCA / IncrementalPCA / Randomized PCA 三节原文:`nmax²·n_components` vs `nmax²·nmin` 的
  复杂度对比、`n_oversamples = 10` 的含义、`whiten=True` 的适用场景、`inverse_transform`
  在 randomized solver 下不精确的声明
- [scikit-learn `sklearn.decomposition.PCA` API](https://scikit-learn.org/stable/modules/generated/sklearn.decomposition.PCA.html)
  —— 参数与属性的权威定义:`svd_solver` 四个取值(full / covariance_eigh / arpack / randomized)、
  "centers but does not scale"、`explained_variance_` 是 `n_components` 个最大特征值、
  `covariance_eigh` 条件数翻倍的警告、`n_components='mle'` 与 `auto` 的交互、文档中的
  `explained_variance_ratio_ = [0.9924…, 0.0075…]` 数值例子
- [scikit-learn `randomized_svd` API](https://scikit-learn.org/stable/modules/generated/sklearn.utils.extmath.randomized_svd.html)
  —— `n_oversamples` / `n_iter` / `power_iteration_normalizer` 的官方语义与取值建议
  ("Smaller number can improve speed but can negatively impact the quality of approximation"、
  `n_iter=0 or 1 should even work fine in theory`、`auto` 的 none/LU 切换规则)
- [scikit-learn §12 Common pitfalls — Data leakage](https://scikit-learn.org/stable/common_pitfalls.html)
  —— §12.1 两条纪律、§12.2 特征选择泄漏的原始实验设定
- [scikit-learn §8.1 Pipelines and composite estimators](https://scikit-learn.org/stable/modules/compose.html)
  —— `Pipeline` 三大用途(含 Safety 原文)、`ColumnTransformer` 与 `remainder` 语义
- [scikit-learn `sklearn.preprocessing.TargetEncoder` API](https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.TargetEncoder.html)
  —— 本仓库 demo 4 对齐的数值锚点(`target_mean_=44.3`、`encodings_` 示例、`smooth='auto'`)
- [Distill, *How to Use t-SNE Effectively*](https://distill.pub/2016/misread-tsne/)
  —— 簇大小/簇间距不可读、低困惑度凭空成团、"没有固定的步数能得到稳定结果"
- [van der Maaten & Hinton, *Visualizing Data using t-SNE*, JMLR 2008](https://jmlr.org/papers/v9/vandermaaten08a.html)
  —— t-SNE 原始论文(困惑度、KL 目标、早期夸张)
- [McInnes, Healy, Melville, *UMAP*, arXiv:1802.03426](https://arxiv.org/abs/1802.03426)
  —— UMAP 的模糊单纯集与交叉熵目标
- [UMAP 官方文档 *How UMAP Works*](https://umap-learn.readthedocs.io/en/latest/how_umap_works.html)
  —— 局部/全局结构权衡的官方表述
- [Halko, Martinsson, Tropp, *Finding Structure with Randomness*, 2009](https://arxiv.org/abs/0909.4061)
  —— 随机化 SVD 的方法学出处(由上列 sklearn 文档引用)
- [Minka, *Automatic choice of dimensionality for PCA*, NIPS 2000](https://proceedings.neurips.cc/paper/2000)
  —— `n_components='mle'` 的 MLE 依据
- [Tipping & Bishop, *Probabilistic PCA*, JRSS-B 1999](http://www.miketipping.com/papers/met-mppca.pdf)
  —— sklearn `score`/`score_samples` 所实现的概率 PCA 模型与 `noise_variance_`
- [CatBoost 官方文档 — Transforming categorical features to numerical features](https://catboost.ai/docs/concepts/algorithm-main-stages_cat-to-number.html)
  —— 有序目标统计(经检索确认存在,未逐页通读)
