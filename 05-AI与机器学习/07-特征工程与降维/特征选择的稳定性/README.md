# 特征选择的稳定性

同一份数据、同一个选择器,换一个随机种子或换一批样本,选出来的特征集合会不会变?**稳定性**
就是把这件事量化。它回答的是「这个特征集合是数据里的真结构,还是抽样噪声」——在 p≫n 的
组学、文本、风控场景里,一次选择结果的重现性往往比它的预测分数更值得怀疑。

- **Φ̂(Nogueira 估计量)**:用「每列入选频率的方差」除以零模型下的期望方差再取补,零模型下期望恰为 0。
- **Kuncheva 指标**:逐对计算、做了机会校正的相似度,只在「每次选固定 k 个」时有定义。
- **Jaccard / Dice**:最朴素的集合相似度,**没有**机会校正,H₀ 下的期望依赖 k 与 d。
- **稳定性选择(Stability Selection)**:不选一个模型,而是**按入选频率**筛变量,并给出误选数上界。
- **重抽样口径**:bootstrap(有放回,n 个)或 subsample(无放回,⌊n/2⌋ 个)。两篇原文用的口径不同。

## 一、原理详解

### 1.1 为什么不能用「跑两次看看重不重合」

设 d 个特征里每次选 k 个。**即便完全是随机选**,两次的平均重合数也是 k²/d——在 d=60、k=5 时
Jaccard 的读数就已经有 0.047,看起来「挺像」但毫无信息。所以度量必须满足两条:①零模型下
期望为常数(通常是 0),叫**机会校正**;②值域有界,才谈得上横向比较。

### 1.2 三种度量与它们的零模型期望

设 M 个特征集合 Z = {s₁…s_M},k_i = |s_i|,k̄ = (1/M)Σk_i,p̂_f = 第 f 列入选频率,
r_ij = |s_i ∩ s_j|。零模型 H₀ 下每个 s_i 是从 d 列里均匀无放回抽 k_i 个,
于是 r_ij 服从**超几何分布**,E[r_ij | H₀] = k_i·k_j/d。

| 度量 | 定义 | H₀ 下的期望(常量 k 时) | 机会校正 |
| --- | --- | --- | --- |
| Jaccard | r/(k_i+k_j−r) | Σₙ n·C(k,n)C(d−k,k−n)/((2k−n)C(d,k)) **依赖 k,d** | ✗ |
| Dice | 2r/(k_i+k_j) | k/d **依赖 k,d** | ✗ |
| Kuncheva IC | (r·d − k²)/(k(d−k)) | 0 | ✓ |
| Φ̂(Nogueira) | 见下 | 0 | ✓ |

Φ̂ 的定义(Definition 4)只用每列的边缘统计量:

```
Φ̂(Z) = 1 − [ (1/d)·Σ_f s²_f ] / [ (k̄/d)·(1 − k̄/d) ],   s²_f = M/(M−1)·p̂_f(1−p̂_f)
```

复杂度 O(Md),而所有逐对度量都是 O(M²d)。三条可验证的性质:
**全同 ⇒ Φ̂ = 1**;**下界为 −1/(M−1)**(渐近 0);**H₀ 下严格为 0**。

### 1.3 Theorem 5:常量 k 时 Φ̂ 就是「逐对 Kuncheva 的平均」

原文把 Kuncheva(2007)、Wald(2013)、nPOG(Zhang 2009)都归到 Φ̂ 上。可以自己推一遍:
把逐对交集 r̄ 展开——Σ_{i≠j} r_ij = Σ_f (M²p̂_f² − M p̂_f),于是

```
r̄ = (M·Σp̂² − k)/(M−1)  ⇒  Σp̂(1−p̂) = (M−1)(k − r̄)/M  ⇒  (1/d)Σs²_f = (k − r̄)/d
Φ̂ = 1 − d(k − r̄)/(k(d−k)) = (d·r̄ − k²)/(k(d−k))      ← 正好是 Kuncheva IC 的逐对平均
```

本仓库的自检把这条恒等式当断言跑(实测差 ~1e-17),所以「逐对量」与「O(Md) 边缘量」可互换。

### 1.4 稳定性选择:用入选频率换误差控制

取 λ 的一个集合 Λ,记 Ŝ^λ 为在 λ 下的选择结果。**入选频率** Π̂^λ_k = P(k ∈ Ŝ^λ(I)),
其中 I 是从 {1…n} 里**无放回**抽 ⌊n/2⌋ 个样本得到的子样本。给定阈值 π_thr:

```
Ŝ^stable = { k : max_{λ∈Λ} Π̂^λ_k ≥ π_thr }
```

**Theorem 1(误差控制)**:在「噪声变量的分布可交换」且「原方法不比随机猜差」两个前提下,
误选个数 V 的期望满足

```
E(V) ≤ q²_Λ / ((2·π_thr − 1)·p),    q_Λ = E|Ŝ^Λ(I)|
```

原文给的配方是 π_thr = 0.9 时取 q_Λ = √(0.8p) ⇒ 上界恰好 1(本仓库自检里断言了这个等式);
q_Λ = √(0.8αp) 则控制 FWER ≤ α。注意 q_Λ **不是** k:它是在 Λ 上取的并集规模,
所以「多试几个 λ」会把上界推大——这正是原文强调要收窄 Λ 的原因。

### 1.5 选择器侧:ANOVA F 与 SelectKBest 的两条硬规则

`SelectKBest(k).fit` = 对每列算 `f_classif` 的 F 值,再取分数最高的 k 列。读源码有两个反直觉点:

1. **常量列给 NaN,不是 0**。`f_oneway` 里 `f = msb/msw`,常量列 msb = msw = 0 → 0/0 = nan,
   源码只发一条 `UserWarning` 就原样返回;(组间方差为正、组内为零时是 `inf`,即 msb/0。)
2. **并列时留下的是下标更大的那个**。`_get_support_mask` 写的是
   `mask[np.argsort(scores, kind="mergesort")[-k:]] = 1`:稳定排序**取尾部**,
   而 `_clean_nans` 先把 NaN 换成 **float64 最小有限值**(源码注释:−inf 不可靠),于是常量列永不入选。

## 二、对比 / 选型

| 场景 | 建议 |
| --- | --- |
| 快速体检「选择结果稳不稳」 | Φ̂(一行公式、O(Md)),并同时报 k̄ 与 M |
| 要和已有文献的 IC 对齐 | 按逐对 Kuncheva 报,但**必须**说明是常量 k |
| 高维筛选 + 要控制误选 | 稳定性选择:报 π_thr、q_Λ 与上界,别只报「选了哪些」 |
| 变长选择(如 Lasso 路径) | 用 Φ̂ 或 nPOG;Jaccard/Dice 不可跨 k 比较 |

## 三、环境准备

- 操作系统:任意(Python 3.9+ / Go 1.21+)
- 依赖:**无第三方库**,只用标准库(便于与 Go 逐值对拍)
- 开发期另用 scikit-learn 1.9.1 做对拍(见第六节),运行本 demo 不需要

## 四、运行方式

```bash
# Python:5 组实验
cd python && python main.py
# Python:自检(52 条断言)
cd python && python selfcheck_stability.py
# Go:与 Python 同题、同随机源
cd go && go run .
```

## 五、关键代码片段

```python
def phi_stability(sets, d):                     # Definition 4
    m = len(sets)
    p_hat = [sum(1 for s in sets if f in s) / m for f in range(d)]
    k_bar = sum(len(s) for s in sets) / m
    var_sum = sum(m / (m - 1) * p * (1 - p) for p in p_hat) / d
    return 1.0 - var_sum / ((k_bar / d) * (1 - k_bar / d))

def select_kbest(scores, k):                     # 对齐 SelectKBest._get_support_mask
    clean = [NAN_REPLACEMENT if s != s else s for s in scores]   # NaN → 最小有限值
    order = sorted(range(len(clean)), key=lambda i: (clean[i], i))  # 稳定:并列留下标大的
    return set(order[len(order) - k:])

def pfer_bound(q, pi_thr, p):                    # Theorem 1
    return q * q / ((2 * pi_thr - 1) * p)
```

## 六、实测结果(见 `python/main.py` 输出)

| 实验 | 读数 |
| --- | --- |
| 零模型 d=60 k=5 M=60 | Jaccard 0.047 / Dice 0.081 / Kuncheva −0.002 / Φ̂ −0.002 |
| 同上但 k/d = 0.0833 | Dice 的 H₀ 期望 ≈ k/d ✓;Jaccard **不等于** k/d ✗(故无机会校正) |
| 真实数据(60 列 4 列有信号) | Jaccard 0.362 / Φ̂ +0.468,入选频率 ≥ 0.6 的只有 2 列 |
| 同一数据 k = 3/10/30 | Φ̂ 0.586 → 0.413 → 0.173 单调;Jaccard 0.464/0.351/0.418 **非单调** |
| Theorem 5 数值核对 | 逐对 Kuncheva 平均 − Φ̂ = 2.1e-17 |
| 纯噪声 d=60 k=8 M=80 ×12 次 | π_thr=0.6/0.75/0.9 时实测 V = 1.25/0.42/0.08,上界 5.33/2.13/1.33 |
| 副本列(4 列同一个信号) | Φ̂ = **1.000000**,每次选出的都是同两个副本列 —— 稳定但零新增信息 |

## 七、注意事项与常见坑

1. **别用单次结果下结论**:单次选择在纯噪声上照样会返回 k 个特征(实测 V = 8,全是误选)。
2. **Jaccard 不能跨 k 比较**:它的 H₀ 期望本身就依赖 k/d;Dice 的 H₀ 期望是 k/d;
   只有 Kuncheva / Φ̂ 在 H₀ 下为 0。
3. **Φ̂ = 1 不代表有用**:4 列互为副本时选择器每次返回同两列,Φ̂ = 1.000000,
   而这 4 列只携带 1 列的信息。稳定性必须与「有效独立列数 / 下游效果」一起报。
4. **π_thr 与 q_Λ 要一起报**:只报「筛出 3 个特征」无法判断误差控制水平;上界随 q_Λ 平方增长,
   「多试几个 λ 再取并集」会把上界迅速推大。
5. **重抽样口径要写清楚**:bootstrap 与 ⌊n/2⌋ 无放回给的是不同的 Π̂,不能混着比。
6. **常量列**:sklearn 给的是 NaN,而 `SelectKBest` 内部把它换成最小有限值再排序,
   所以「分数是 nan」与「不会被选中」是两件事,别在 nan 上做 `>` 比较。
7. **并列**:分数并列时选择结果由排序稳定性决定;要复现别人的结果,必须连同版本与
   并列口径一起说明(本仓库对齐的是 mergesort 取尾部)。

## 八、参考资料(实际阅读过的来源)

- [scikit-learn — Feature selection(2.1 Univariate feature selection)](https://scikit-learn.org/stable/modules/feature_selection.html)
  —— ANOVA F 与 SelectKBest 的官方说明、`SelectFpr/SelectFdr` 的口径差异
- [scikit-learn `SelectKBest` API](https://scikit-learn.org/stable/modules/generated/sklearn.feature_selection.SelectKBest.html)
  —— `score_func` 默认 `f_classif`、`k='all'` 的语义、`scores_`/`pvalues_` 属性
- [scikit-learn `f_classif` API](https://scikit-learn.org/stable/modules/generated/sklearn.feature_selection.f_classif.html)
  —— 返回 (F, p) 且与 `scipy.stats.f_oneway` 等价
- [scikit-learn `mutual_info_classif` API](https://scikit-learn.org/stable/modules/generated/sklearn.feature_selection.mutual_info_classif.html)
  —— k-NN 熵估计,`n_neighbors=3`「更大降方差但引入偏差」(对比:非参数选择器自带随机性)
- [scikit-learn 源码 `sklearn/feature_selection/_univariate_selection.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/feature_selection/_univariate_selection.py)
  —— `f_oneway` 的 sstot/ssbn/sswn 与常量列只发警告、`_clean_nans` 换最小有限值、
  `SelectKBest._get_support_mask` 的 mergesort 取尾部(本节所有实现细节的出处)
- [Nogueira, Sechidis, Brown — *On the Stability of Feature Selection Algorithms*, JMLR 18(174):1−54, 2018](https://www.jmlr.org/papers/v18/17-514.html)
  —— Φ̂ 的 Definition 4、5 条性质与 −1/(M−1) 下界、Theorem 5(与 Kuncheva/Wald/nPOG 等价)、
  附录 C.5 对 Jaccard/Dice/Ochiai 无机会校正的逐项推导、Appendix B 的 H₀ 下 p_f = k̄/d
- [Meinshausen & Bühlmann — *Stability Selection*, arXiv:0809.2932](https://arxiv.org/abs/0809.2932)
  —— Definition 1/2/3、Theorem 1 的 E(V) ≤ q²/((2π_thr−1)p)、π_thr = 0.9 配 q = √(0.8p) ⇒ E(V) ≤ 1、
  「子样本无放回抽 ⌊n/2⌋」的口径与 PFER/FWER 的用法
