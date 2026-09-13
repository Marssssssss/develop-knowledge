# K-Means 聚类

## 简介

K-Means 是最经典的**划分式聚类**算法,通过 Lloyd 迭代把 N 个样本分配到 K 个簇,目标最小化簇内平方和 (inertia)。sklearn 用 k-means++ 概率初始化 + 多次重启取最优,鲁棒性远超朴素随机初始化。

- **目标函数**: `inertia = Σᵢ minⱼ ‖xᵢ − μⱼ‖²` (within-cluster sum-of-squares)
- **Lloyd 算法 3 步**: ① init 选 K 中心 ② E-step 样本指派最近中心 ③ M-step 中心取均值
- **k-means++ 初始化**: Arthur & Vassilvitskii 2007,概率 ∝ D(x)²/Σ D(x)² 选远处点,O(log k) 近似最优
- **等价 EM**: K-means 等价于带小等对角协方差矩阵的 EM 算法
- **停止准则**: sklearn 实现看**中心移动距离** < tol(1e-4 默认),不是损失下降
- **多次重启**: `n_init` 次独立拟合取最小 inertia;`n_init='auto'` 默认 random 10 / k-means++ 1

## 原理详解

1. **Lloyd 算法**(经典 EM-style):
   - **初始化**:选 K 个初始中心(随机 / k-means++ / 数组)
   - **E-step**:每个样本 `cᵢ = argminⱼ ‖xᵢ − μⱼ‖²` 归最近中心
   - **M-step**:每簇中心取均值 `μⱼ = (1/|Cⱼ|) Σ xᵢ` (空簇随机补)
   - **迭代**直到中心移动 < tol 或 max_iter
2. **k-means++**(Arthur & Vassilvitskii 2007):
   - 随机选 1 个中心 μ₁
   - 计算 `D(x) = minⱼ ‖x − μⱼ‖²`(已选中心的最近距离平方)
   - 加权采样下一个中心,概率 `D(x) / Σ D(x')`,倾向于远处
   - 重复 K 次,理论 `O(log k)` 近似最优
3. **收敛性**:Lloyd 一定收敛到局部最优(目标函数单调不增);非凸故可能局部最优
4. **Voronoi 图视角**:把当前中心画 Voronoi 图,每段即一个簇;中心更新为各段均值
5. **k 选择**:
   - **肘部法**:画 inertia vs k 曲线,曲率最大点为肘部
   - **轮廓系数 (silhouette)**: `s(i) = (b − a) / max(a, b)`,a 同簇均距,b 异簇最小均距,范围 [−1, 1]
6. **算法选择**:
   - `lloyd`(默认):经典 EM-style
   - `elkan`:用三角不等式减少距离计算,但 O(n·k) 额外内存
7. **复杂度**:`O(k·n·T)`(n 样本数,T 迭代数),最坏 `O(n^(k+2/p))`(`p` = 维度)
8. **算法假设**:
   - 簇是凸形且各向同性(球形 + 等方差)→ 拉长簇 / 环形 / 不同密度都失效
   - 各簇样本量大致相等
   - 高维需先 PCA 降维(`curse of dimensionality`)

## 对比 / 选型

| 算法 | 簇形状 | 速度 | 备注 |
| --- | --- | --- | --- |
| K-Means | 球形 / 等大小 | 快 | 默认选,大数据可 MiniBatchKMeans |
| DBSCAN | 任意形状 | 慢 | 密度聚类,自动定簇数,抗噪声 |
| Spectral | 任意形状 | 中 | 谱聚类,相似度矩阵 + 图割 |
| GMM | 椭圆 | 中 | EM 学习,概率聚类,可重叠 |
| MeanShift | 任意形状 | 慢 | 核密度估计,自动定簇数 |

## 环境准备

- Python 3.10+ (纯 stdlib)
- OS:跨平台

## 运行方式

```bash
python3 kmeans.py
```

## 关键代码片段

```python
def kmeans_plus_plus_init(X, k, rng):
    """概率 ∝ D(x)² 选远处点作初始中心。"""
    centers = [list(X[rng.randrange(len(X))])]
    while len(centers) < k:
        # D(x) = min 距离平方(已选中心)
        dists_sq = [min(euclidean(x, c) ** 2 for c in centers) for x in X]
        total = sum(dists_sq)
        # 加权采样
        probs = [d / total for d in dists_sq]
        r = rng.random()
        cum = 0.0
        for i, p in enumerate(probs):
            cum += p
            if cum >= r:
                centers.append(list(X[i]))
                break
    return centers

# Lloyd 单次迭代(E + M)
for it in range(max_iter):
    labels = [min(range(k), key=lambda j: euclidean(x, centers[j])) for x in X]
    new_centers = [
        [sum(X[i][d] for i, l in enumerate(labels) if l == j) / max(1, labels.count(j))
         for d in range(dim)]
        for j in range(k)
    ]
    # 收敛:中心移动距离 < tol
    shift = max(euclidean(a, b) for a, b in zip(prev, new_centers))
    if shift < tol:
        break
```

## 性能与边界

- 单次 Lloyd:`O(k·n·T)`,sklearn OpenMP 并行化,小 chunks(256 样本)分片
- 大数据(>10⁴ 样本):`MiniBatchKMeans` 增量更新 + mini-batches,5-10x 加速,质量略差
- k-means++ 初始化比 random 平均减少 1-3 次迭代
- `n_init=10` random 几乎总是找到全局最优(简单数据);k-means++ `n_init=1` 即可

## 注意事项与常见坑

1. **必须标准化**:不标准化 → 量纲大的特征主导距离 → 簇扭曲;`StandardScaler` 先 fit
2. **k 选择**:无先验用肘部法 + silhouette;有先验按业务定
3. **k-means++ vs random**:random 在 n_init=10 也不一定能找到最优;k-means++ 默认 'auto' 一次即可(sklearn 1.4+)
4. **空簇处理**:某簇无样本时 Lloyds 退化,sklearn 默认随机补;生产中可用 KMeans++ 重启
5. **簇数大于数据**:k > n 时部分簇必空;`n_clusters` 必须 < n_samples
6. **n_init='auto'**:sklearn 1.4+ 默认值,random 走 10 次、k-means++ 走 1 次(后者已近最优)
7. **算法选择**:`algorithm='lloyd'` 默认,`elkan` 适合 K 较小且簇紧凑的数据(三角不等式省距离计算)
8. **新样本聚类**:sklearn KMeans 直接 `predict(new_X)` 即可(惰性指派)
9. **可视化**:sklearn 文档示例 `Demo of k-means assumptions` 展示何时失败

## 参考资料(实际阅读过的权威来源)

- [scikit-learn Clustering §2.3](https://scikit-learn.org/stable/modules/clustering.html) — Lloyd 3 步 + k-means++ 描述 + inertia 缺陷(非归一化 + 高维诅咒) + EM 等价 + voronoi 视角
- [scikit-learn KMeans API](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.KMeans.html) — n_init='auto' + algorithm='lloyd' 改名历史(1.1 'full' → 'lloyd') + 复杂度 O(k·n·T)
- [scikit-learn k_means function](https://scikit-learn.org/1.3/modules/generated/sklearn.cluster.k_means.html) — 低层 API、n_init 1.4+ 默认值变化、'auto' 规则
- Arthur, D. & Vassilvitskii, S. (2007) "k-means++: The advantages of careful seeding" — k-means++ 概率初始化奠基
- Lloyd, S. P. (1982) "Least squares quantization in PCM" — Lloyd 算法原始描述
- Bishop《Pattern Recognition and Machine Learning》Ch.9.1 — EM 算法视角
