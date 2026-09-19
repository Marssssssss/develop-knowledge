# 层次聚类与连接准则

## 简介

层次聚类是一族通过**逐次合并(凝聚,自底向上)或分裂(分裂,自顶向下)**构造嵌套簇的算法,
结果是一棵**树(树状图 dendrogram)**。scikit-learn 的 `AgglomerativeClustering` 走凝聚路线:
每个观测自成一簇,反复合并直到满足停止条件;**linkage criteria 决定用哪个度量挑选合并对象**。

与 k-means / DBSCAN 相比它的独特价值:① 不需要指定簇数就能得到**完整的多尺度结构**
(一次建树、任意切割);② 可以接受**任意成对距离**(非欧氏、预计算相似度矩阵);
③ 可以加 connectivity 约束(如图像上只合并相邻像素)。

| 准则 | 官方定义(sklearn 用户指南) |
| --- | --- |
| **ward** | 最小化所有簇内平方差之和,是**方差最小化**方法,与 k-means 的目标相似,只是用凝聚层次求解 |
| **complete / maximum** | 最小化两簇观测之间的**最大**距离 |
| **average** | 最小化两簇观测之间距离的**平均值** |
| **single** | 最小化两簇**最近**观测对之间的距离 |

本目录用**纯标准库**实现四种准则 + Lance-Williams 递推 + 树状图切割(Python + Go),
自检 Python 侧 28 条、Go 侧 16 条。

```
python/  agglomerative.py(四种准则 / Lance-Williams / ΔSSE / cut_tree / k-means 对照)
         agglomerative_check.py(自检 A~H 八段,含"逐步直接重算"的对照实现)
go/      agglomerative.go(同实现)  main.go(自检)
```

## 原理详解

### 1. 四种准则一句话版本

```
single   d(A,B) = min  dist(a,b)      a∈A, b∈B
complete d(A,B) = max  dist(a,b)
average  d(A,B) = mean dist(a,b)
ward     d(A,B) = ΔSSE = (n_A·n_B/(n_A+n_B))·||c_A − c_B||²
```

Ward 的闭式是自检 B 段直接验证的:合并 5 点簇与 3 点簇时,
闭式算出的 7.580575150 与"合并后簇内平方和 − 合并前两簇簇内平方和"**逐位相同**。

### 2. Lance-Williams 递推:为什么不需要每次重算

朴素做法是每合并一次就把新簇到其余簇的距离重算一遍(簇规模上升后代价爆炸)。
Lance & Williams(1967)证明这族准则都能写成同一个递推:

```
d(ij, k) = α_i·d(i,k) + α_j·d(j,k) + β·d(i,j) + γ·|d(i,k) − d(j,k)|
```

| 准则 | α_i | α_j | β | γ |
| --- | --- | --- | --- | --- |
| single | 1/2 | 1/2 | 0 | **−1/2** |
| complete | 1/2 | 1/2 | 0 | **+1/2** |
| average | n_i/(n_i+n_j) | n_j/(n_i+n_j) | 0 | 0 |
| ward | (n_i+n_k)/N | (n_j+n_k)/N | **−n_k/N** | 0 |

(`N = n_i + n_j + n_k`。single 与 complete **只差 γ 的符号**——一个取最小、一个取最大,
这是最直观的记忆点。)

这四个系数组不是"从文档抄来的":自检 A 段用一份**逐步直接重算**的对照实现跑同一批数据,
四种准则的**合并高度序列与每一步的成员集合**都逐条相同(容差 1e-9)。
只要某个 α/β/γ 记反,高度序列立刻分叉。

### 3. 一个极易踩的口径坑:Ward 的距离矩阵不是距离

Ward 的递推对 D 是**齐次**的(α_i+α_j+β = 1),但它在平方距离上给出的取值是
**ΔSSE 的 2 倍**(两点特例:ΔSSE = d²/2,而递推给出 d²)。本实现的做法是:
把初始距离矩阵取成 **d²/2(半平方距离)**,于是后续每一步的取值**恰好等于合并代价 ΔSSE**,
可以直接和"合并前后簇内平方和之差"对表。自检里两种算法(递推 vs 重算)交叉验证过这一口径。
开发期第一版直接用 d² 初始化,结果 Ward 的高度序列整体是朴素版的 2 倍,就是被这条断言抓出来的。

### 4. "rich get richer"与链式效应

sklearn 用户指南原话:"Agglomerative cluster has a **rich get richer** behavior that leads to
uneven cluster sizes. In this regard, **single linkage is the worst strategy, and Ward gives the
most regular sizes**."

自检 F 段把这句话量化成可执行断言:两团(各 15 点)之间放一串间距 0.4 的"桥"点,
记录**两团第一次落进同一个簇时的合并高度**:

| 准则 | A∪B 合并高度 |
| --- | --- |
| single | **2.18**(顺着桥一路连过去 —— 链式效应) |
| average | 6.45 |
| complete | 8.46 |
| ward | **383.82**(方差最小化,完全不看"最短路") |

同一个 k=2 的切割下,ward 的簇内平方和也是四者中最小的(它优化的就是这个目标)。

### 5. 树状图与切割

合并 n−1 次后得到满树(自检 D1);切割就是"回放前 n−k 次合并",得到恰好 k 个簇
(自检 E 段对 k = 1/2/3/5/9 全部核对)。
single / complete / average 的合并高度**单调非降**(自检 C 段),
所以可以在任意高度横切;**Ward 的高度是 SSE 增量,不保证单调**,
本实现只打印不断言(本数据上恰好单调,但那是数据给的面子)。

## 对比 / 选型

| | ward | complete | average | single |
| --- | --- | --- | --- | --- |
| 目标 | 方差最小化 | 直径最小化 | 平均距离 | 最近邻 |
| 簇规模 | 最均匀 | 较均匀 | 中等 | **最不均(链式)** |
| 抗噪 | 好 | 好 | 中 | **差** |
| 非欧氏距离 | ❌(只能欧氏) | ✅ | ✅ 推荐 | ✅ |
| 非球状簇 | 一般 | 一般 | 一般 | **可以**(能跟着流形走) |
| 规模 | 大 n 也行(有 connectivity 时更好) | 同左 | 同左 | 可算得很快 |

- 需要"先看结构再定 k" → 层次;已知 k 且数据量大 → k-means / DBSCAN。
- 度量是余弦/JS/预计算相似度 → **average**(Ward 用不了)。
- 数据是"链状/流形"且噪声少 → single 反而合适。

## 环境准备

- 操作系统:任意(纯标准库)
- Python ≥ 3.8 / Go ≥ 1.21
- 依赖:**无**

## 运行方式

```bash
cd python && python3 agglomerative_check.py    # 28 条断言
cd go     && go run .                          # 16 条断言
```

## 关键代码片段

```python
def lw_coeffs(linkage, ni, nj, nk):
    if linkage == "single":
        return 0.5, 0.5, 0.0, -0.5      # γ = −1/2 → 取最小
    if linkage == "complete":
        return 0.5, 0.5, 0.0, 0.5       # γ = +1/2 → 取最大
    if linkage == "average":
        return ni/(ni+nj), nj/(ni+nj), 0.0, 0.0
    tot = ni + nj + nk                  # ward:β 为负,把"合并代价"拉向方差增量
    return (ni+nk)/tot, (nj+nk)/tot, -nk/tot, 0.0

# 每次合并后:新簇到 k 的距离不用重算,直接递推
val = ai*dik + aj*djk + beta*dij + gamma*abs(dik - djk)
```

## 性能与边界

- **时间**:朴素实现每步重算全部簇间距离是 **O(n³)**;用 Lance-Williams 递推 + 优先队列
  (或 sklearn 用的 nn-chain)可降到 **O(n²)** 时间。本 demo 为了"可与直接重算对表",
  每步仍线性扫描找最小对,是 **O(n³) 的教学版**,n ≤ 几百才有实用价值。
- **空间**:距离矩阵 O(n²)。n > 数万时内存先于时间成为瓶颈 —— 这也解释了为什么
  `sklearn` 的对比表里 Ward/agglomerative 只标到 "Large n_samples and n_clusters"。
- **距离更新次数**是可数的:Σ(活跃簇数 − 2) = n(n−1)/2 − (n−1),自检 D3 直接核对了这个数
  (n=9 时 28 次)。
- **不可增量**:新来一个点要重建整棵树(与 k-means / DBSCAN 的 `predict` 不同)。

## 注意事项与常见坑

1. **Ward 只能用欧氏距离**(sklearn 明说 "the affinity cannot be varied with Ward")。
   非欧氏度量的场景换 average。这是很多人"Ward 结果不对"的根因。
2. **Ward 的高度不是距离**:它是 SSE 增量,量纲是"平方",且**不保证单调**——
   用"高度阈值"切割 ward 树时要小心。
3. **single 的链式效应**:两个本该分开的簇只要中间有几个稀疏点就会被串成一串。
   自检 F 段是它的量化版本(2.18 vs 8.46)。
4. **`n_clusters` 与 `distance_threshold` 二选一**:两个都设会冲突;
   用后者时得到的簇数事先未知。
5. **"rich get richer" 会被 connectivity 放大**:sklearn 专门警告过——
   用 `kneighbors_graph` 建的连接约束配 single/complete/average 时,
   在簇数很少的极限下会出现"少数几个巨簇 + 大量微簇"。
6. **特征量纲**:Ward/complete/average 都基于距离,未标准化的特征会让量纲大的维度独裁。
7. **别把树状图的高度差当"簇间距"**:不同准则的高度量纲完全不同(见上表 2.18 vs 383.82)。

## 参考资料(实际阅读过的权威来源)

- [scikit-learn《2.3.6 Hierarchical clustering》(聚类用户指南)](https://scikit-learn.org/stable/modules/clustering.html)
  — 自底向上的过程描述、四种 linkage 的官方定义、可视化与树状图切割、
  "rich get richer" 与 single/ward 的对比、connectivity 约束的警告、
  以及 §2.3.1 总览表里 Ward/agglomerative 的适用规模与非欧氏支持。
- [scikit-learn 源码 `sklearn/cluster/_agglomerative.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/cluster/_agglomerative.py)
  — `ward_tree` / `linkage_tree` 的接口与 linkage 取值、ward 走 `compute_ward_dist`
  (moment 增量)而 single/average/complete 走 `linkage_tree` 的实现分野。
  (经 jsDelivr 镜像取到。Lance-Williams 的递推本体在该包的 `.pyx` 里,
  镜像未收录该扩展文件,故系数表改为"数值在自检中与直接重算交叉验证"。)
- Lance, G.N. & Williams, W.T., *A General Theory of Classificatory Sorting Strategies
  1. Hierarchical Systems*, The Computer Journal 9(4), 1967 — Lance-Williams 递推的原始出处
  (文献著录,未取全文;本目录四个系数组由自检 A 段逐步交叉验证)。
