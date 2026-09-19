# DBSCAN 密度聚类

## 简介

DBSCAN(Density-Based Spatial Clustering of Applications with Noise)把簇定义为
**被低密度区域隔开的高密度区域**,因此能发现**任意形状**的簇、自动识别噪声、
且**不需要预先指定簇数**——这三点正是 Ester 等人在 KDD-96 原论文摘要里给出的动机
(当时的 CLARANS 一类算法要领域知识定参数、只能找球状簇、在大数据上慢)。

关键概念(全部出自原论文 Definition 1~6):

| 概念 | 定义 |
| --- | --- |
| eps-邻域 `NEps(p)` | `{q ∈ D \| dist(p,q) ≤ Eps}` —— **含 p 自身**(p 到自己是 0) |
| 核心点(core point) | `\|NEps(p)\| ≥ MinPts` |
| 边界点(border point) | 不是核心点,但落在某个核心点的 eps-邻域内 |
| 直接密度可达 | `p ∈ NEps(q)` 且 q 是核心点(对两个核心点**对称**,核心-边界之间**不对称**) |
| 密度可达 / 密度相连 | 直接密度可达的传递闭包 / 存在公共起点 o 使 p、q 都从 o 密度可达 |
| 簇 | 满足 Maximality + Connectivity 的非空子集(Definition 5) |
| 噪声 | 不属于任何簇的点(Definition 6) |

本目录用**纯标准库**按论文伪码实现(Python + Go),自检 Python 侧 31 条、Go 侧 20 条。

```
python/  dbscan.py(regionQuery / ExpandCluster / k-dist / k-means 对照)
         dbscan_check.py(自检 A~I 九段)
go/      dbscan.go(同上实现)  main.go(自检)
```

## 原理详解

### 1. 两步走:先找核心点,再取密度可达集

论文 Lemma 2 给了这个二步法的正确性:给定 Eps/MinPts,选任意一个满足核心点条件的点做种子,
**从该种子密度可达的全部点**恰好就是它所在的簇。所以算法只需要:

```
DBSCAN(SetOfPoints, Eps, MinPts)
  ClusterId := nextId(NOISE)
  for Point in SetOfPoints:
      if Point.ClId == UNCLASSIFIED:
          if ExpandCluster(SetOfPoints, Point, ClusterId, Eps, MinPts):
              ClusterId := nextId(ClusterId)

ExpandCluster(SetOfPoints, Point, ClId, Eps, MinPts):
  seeds := regionQuery(Point, Eps)
  if seeds.size < MinPts:            # 不是核心点
      changeClId(Point, NOISE);  return False
  changeCiIds(seeds, ClId)           # 种子整体打簇号
  seeds.delete(Point)
  while seeds != Empty:
      currentP := seeds.first()
      result := regionQuery(currentP, Eps)
      if result.size >= MinPts:      # currentP 是核心点
          for resultP in result:
              if resultP.ClId in (UNCLASSIFIED, NOISE):
                  if resultP.ClId == UNCLASSIFIED: seeds.append(resultP)
                  changeClId(resultP, ClId)        # 原 NOISE 的边界点在此被改写
      seeds.delete(currentP)
  return True
```

三个容易写错的细节(自检里都钉住了):

1. **`seeds.size < MinPts` 与自身比较** —— `regionQuery` 的返回含 Point 自己,
   所以 **MinPts 计自身**。sklearn 的 API 文档也是这么写的
   ("This includes the point itself"),但 sklearn 的用户指南写成了
   "there exist **min_samples other** samples within a distance of eps" ——
   **两处口径差 1**。本实现取"含自身"(与原论文一致),README 与代码注释双处标注。
   自检 A2:两点相距 1、eps=1.5、MinPts=2 时两点**都是核心点**;
   按"其他样本"读法这两点永远成不了核心。
2. **`changeCiIds(seeds, ClId)` 不能覆盖已有簇标签** —— 论文说同时属于两簇的点
   "will be assigned to the cluster **discovered first**"。实现成无脑覆盖时,
   后发现的簇会把先发现的边界点抢走(本仓库开发期就踩了这个坑,由自检 C 段抓出)。
3. **边界点不进 seeds** —— 论文:已知它非核心,加进去只会多做一次没有新答案的 region query。

### 2. 非对称性到底来自哪里

`p ∈ NEps(q)` 这个**成员关系是对称的**(距离对称);不对称的是 Definition 2 的
**核心点条件**:即便 `q ∈ NEps(p)` 且 `p ∈ NEps(q)`,只要 p 不是核心点,
q 就不能"由 p 直接密度可达"。自检 B3/B4 用一维数据 `0, 0.5, 1.0, 1.9`(eps=1.2, MinPts=3)
把这一点钉住:`3 ∈ NEps(2)` 且 2 是核心 → 单向可达成立;反向因 3 非核心而不成立。

### 3. 参数:MinPts 与 Eps

- **MinPts**:主要控制**对噪声的容忍度**(论文:噪声大、数据量大时应该调大)。
- **Eps**:决定"局部邻域"多大,**通常不能留在默认值**。太小 → 几乎全是噪声(自检 F2:
  86/86);太大 → 相邻簇被合并,**连噪声点都会自己抱团成簇**(自检 F3:噪声从 6 降到 2)——
  "低密度"永远是相对 eps 而言的。

论文 §4.2 给了选参启发式:**k-dist 图**。k-dist(p) = p 到第 k 近邻的距离,
那么以它为半径的邻域"几乎总是"含 **k+1** 个点(含自身,除非有并列距离)。
把全部点的 k-dist **降序**画出来,取第一个"谷"的谷底作为 Eps、MinPts = k(论文图示用 4-dist)。
自检 I 段:两簇 + 6 个孤立点的数据上,簇内最大 4-dist 是 0.757,孤立点最小 4-dist 是 15.403;
以簇内最大 4-dist 作 eps 恰好把 6 个噪声全部排除。

### 4. 复杂度与"每点至多一次查询"

论文:用 R*-tree 支持 region query 时,单次查询平均 O(log n),
**"For each of the points of the database, we have at most one region query"**,
故平均运行时间 **O(n·log n)**。本实现的 `regionQuery` 是暴力 O(n),
但"每点至多一次查询"这条**与索引无关**,自检 G1 直接统计调用次数:60 个样本 → 60 次。
(踩坑:开发期为了最后算核心点集合又对每个点查了一次,变成 1.5n——核心点标记必须在
遍历中顺手记下,不能事后重算。)

sklearn 源码 `_dbscan.py` 的 Notes 还提到它的实现是**批量**算邻域,
内存复杂度是 O(n·d)(d 为平均邻居数)而非原论文的 O(n)。

## 对比 / 选型

| | k-means | DBSCAN | 层次(agglomerative) |
| --- | --- | --- | --- |
| 需要簇数 K | 是 | **否** | 是(或距离阈值) |
| 簇形状 | 凸/球状 | **任意** | 取决于 linkage |
| 噪声处理 | 无(全部归类) | **显式 −1 标签** | 无 |
| 密度不均 | 尚可 | **差**(全局 eps) | 尚可 |
| 复杂度 | O(n·K·d·iter) | O(n·log n)(带索引) | O(n²)~O(n³) |
| 主要风险 | 非凸簇失效 | eps/MinPts 难调 | 规模上不去 |

自检 H 段是这张表最直观的证据:两个同心圆上 DBSCAN 的 ARI = **1.0000**,
k-means 的 ARI = **−0.0056**(等价于随机划分)。密度不均的场景应改用 **HDBSCAN**
(sklearn 的对比表里它支持 variable cluster density)。

## 环境准备

- 操作系统:任意(纯标准库)
- Python ≥ 3.8 / Go ≥ 1.21
- 依赖:**无**

## 运行方式

```bash
cd python && python3 dbscan_check.py    # 31 条断言
cd go     && go run .                   # 20 条断言
```

## 关键代码片段

```python
def region_query(X, i, eps, stats=None):
    """Definition 1:dist ≤ eps,**含 i 自身**(p 到自己的距离是 0)。"""
    return [j for j, x in enumerate(X) if dist(X[i], x) <= eps]

def dbscan(X, eps, min_samples, stats=None):
    labels = [UNCLASSIFIED] * len(X)
    for i in range(len(X)):
        if labels[i] != UNCLASSIFIED:
            continue
        seeds = region_query(X, i, eps, stats)
        if len(seeds) < min_samples:      # 不是核心点 → 暂标 NOISE,之后可能被收编
            labels[i] = NOISE
            continue
        for s in seeds:                   # 伪码 changeCiIds(seeds, ClId)
            if labels[s] in (UNCLASSIFIED, NOISE):   # ← 不能覆盖已有簇标签!
                labels[s] = cid
        ...
```

## 性能与边界

- **时间**:带空间索引(R*-tree / KD-Tree / Ball-Tree)平均 O(n·log n);
  本 demo 的暴力实现是 **O(n²)**,只适合 n ≤ 千量级的教学演示。
- **空间**:论文原版 O(n);sklearn 的批量实现是 **O(n·d)**(d = 平均邻居数)。
- **高维**:距离度量在高维下趋于同质(维度灾难),"密度"这个概念本身会失效——
  DBSCAN 一般建议在低维或降维后的数据上用。
- **确定性**:除边界点的"先发现先得"外,结果与访问顺序无关(论文 Lemma 2 的结论)。

## 注意事项与常见坑

1. **MinPts 的口径差 1**:sklearn 用户指南与 API 文档自相矛盾(见上文),
   与论文对表时用 API 文档那一版(含自身)。跨库复现结果时这是最常见的"差一个点"的来源。
2. **别用 `eps=0.5` 之类的默认值**:论文明确说 eps "usually cannot be left at the default
   value"。先用 k-dist 图看一眼量级。
3. **边界点的归属不稳定**:同属两簇的边界点归先发现者,输入顺序变了结果就变。
   需要可复现时固定数据顺序(或接受这点差异)。
4. **噪声不是"脏数据"**:eps 变大时噪声会自己成簇,eps 变小时正常点会变噪声。
   自检 F3 就是"噪声数从 6 掉到 2"的实测。
5. **密度差异大的数据集不要用全局 eps**:这是 DBSCAN 的结构性缺陷,改 HDBSCAN 或 OPTICS。
6. **`labels_` 里 −1 是噪声、不是"第 0 簇"**:统计簇数时要过滤 `>= 0`。

## 参考资料(实际阅读过的权威来源)

- [Ester, Kriegel, Sander, Xu《A Density-Based Algorithm for Discovering Clusters in Large Spatial Databases with Noise》,KDD-96](https://www.aaai.org/Papers/KDD/1996/KDD96-037.pdf)
  — **全文 6 页精读**:Definition 1~6(eps-邻域 / core point condition / 直接密度可达 /
  密度可达 / 密度相连 / 簇 / 噪声)、Lemma 1-2、DBSCAN 与 ExpandCluster 伪码、
  "assigned to the cluster discovered first"、"cluster contains at least MinPts points"、
  R*-tree 下 O(n·log n) 与"每点至多一次 region query"、§4.2 的 k-dist 与 4-dist 启发式。
- [scikit-learn《2.3.7 DBSCAN》(聚类用户指南)](https://scikit-learn.org/stable/modules/clustering.html)
  — core sample / 边界点 / outlier 的官方表述、min_samples 与 eps 的调参建议、
  与 K-Means / HDBSCAN / OPTICS 的对比表、层次聚类的四种 linkage 与"rich get richer"。
- [scikit-learn 源码 `sklearn/cluster/_dbscan.py`](https://cdn.jsdelivr.net/gh/scikit-learn/scikit-learn@main/sklearn/cluster/_dbscan.py)
  — `min_samples` 的 "**This includes the point itself.**" 口径、噪声标签 −1、
  批量邻域计算带来的 O(n·d) 内存复杂度、KDD-96 与 Schubert 2017 两篇参考文献。
  (经 jsDelivr 镜像取到。)
