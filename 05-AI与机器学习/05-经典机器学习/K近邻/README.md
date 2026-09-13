# K-近邻 (KNN)

## 简介

KNN 是**惰性学习**(lazy learning)的代表:训练阶段只"记住"数据,预测阶段才计算。它无参数、无显式训练过程,可同时用于分类(投票)和回归(均值)。性能关键在距离度量和加速结构(KD-Tree / Ball-Tree)。

- **距离度量**: Minkowski `d(a,b) = (Σ|aᵢ−bᵢ|^p)^(1/p)`,`p=1` Manhattan / `p=2` Euclidean
- **k**: 默认 5;`k=1` 完全拟合(高方差)、`k=N` 常数预测(高偏差)
- **权重**: `uniform` 等权 / `distance` 1/distance 加权(近邻话语权更大)
- **加速结构**: KD-Tree(中位数切分,适合低维)/ Ball-Tree(球形切分,适合高维)/ brute(O(n·d) 但稳)
- **leaf_size**: BallTree/KDTree 叶大小,影响内存/速度,sklearn 默认 30

## 原理详解

1. **惰性学习**:fit() 只存数据;predict() 对每个 query 计算与所有训练点的距离,选 k 近邻投票
2. **距离度量**:
   - Euclidean (p=2): `√(Σ(aᵢ−bᵢ)²)` —— 球形等距,默认
   - Manhattan (p=1): `Σ|aᵢ−bᵢ|` —— 网格等距,高维稀疏数据更稳
   - Minkowski (p): 一般化,`p→∞` 退化为 Chebyshev
3. **分类投票**:
   - `uniform`: `ŷ = argmax Σ I(yᵢ = c)`,每个近邻等权
   - `distance`: `ŷ = argmax Σ I(yᵢ = c)/dᵢ`,距离越近话语权越大
4. **KD-Tree**:
   - 构建:递归按 `axis = depth % d` 中位数切分,O(n·d·log n) 平衡
   - 搜索:沿 query 维下降到叶,回溯时若 `|q[axis] − split[axis]| < 当前最远 k 距离` 则搜另一子树
   - 复杂度:build O(n·d·log n) / query O(log n) 平均;**d > 20 退化为 O(n)**,Ball-Tree 更稳
5. **回归预测**: `ŷ = mean(y近邻)` 或 `mean(y近邻/d)` 加权;`KNeighborsRegressor` 同 API
6. **多分类**:无需 OvR,直接多类投票(`Counter` 计每类)
7. **决策边界**:非线性,贴近 Voronoi 图;对噪声样本敏感(尤其 k=1)

## 对比 / 选型

| 算法 | query 复杂度 | 维度限制 | 备注 |
| --- | --- | --- | --- |
| brute | O(n·d) | 任意 | 最稳,无构建成本 |
| KD-Tree | O(log n) 平均 | d ≤ 20 | sklearn `algorithm='kd_tree'` |
| Ball-Tree | O(log n) | d 较大 | sklearn `algorithm='ball_tree'`,球形切分 |
| auto | 自动选 | — | sklearn 默认,按数据决定 |

| 距离 | 公式 | 适用场景 |
| --- | --- | --- |
| euclidean | √(Σ(aᵢ−bᵢ)²) | 默认,球形簇 |
| manhattan | Σ\|aᵢ−bᵢ\| | 高维稀疏、网格对齐 |
| chebyshev | max(\|aᵢ−bᵢ\|) | 棋盘距离 |
| cosine | 1 − a·b/(\|a\|·\|b\|) | 文本向量(关注方向) |

## 环境准备

- Python 3.10+ (纯 stdlib)
- OS:跨平台

## 运行方式

```bash
python3 knn.py
```

## 关键代码片段

```python
def euclidean(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

def kdtree_knn_search(root, query, k):
    best = []  # 容量 k,保存 (dist, train_idx)
    def search(node):
        if node is None:
            return
        d = euclidean(query, node.point)
        # 替换最远 if 更近
        if len(best) < k or d < max(b[0] for b in best):
            if len(best) == k:
                worst_i = max(range(k), key=lambda i: best[i][0])
                best[worst_i] = (d, node.idx)
            else:
                best.append((d, node.idx))
        # 剪枝:球与分裂超平面相交才搜另一子树
        diff = query[node.axis] - node.point[node.axis]
        first, second = (node.left, node.right) if diff < 0 else (node.right, node.left)
        search(first)
        if abs(diff) < max(b[0] for b in best):
            search(second)
    search(root)
    return sorted(best)
```

## 性能与边界

- Brute query:n·d 计算 = n·d 次乘加,n=10⁴/d=2 → 20K 次/查询
- KD-Tree query:实测 n=2000/d=2 brute 5x 慢于 KD-Tree;d > 20 后两者差不多
- 训练样本大时 KD-Tree 收益明显;d 高时建议 Ball-Tree 或 brute
- sklearn `n_jobs=-1` 并行查询;`algorithm='auto'` 自动选

## 注意事项与常见坑

1. **特征必须标准化**:不标准化 → 量纲大的特征主导距离 → KNN 失效;`StandardScaler` 先 fit
2. **k 必须小于训练集大小**:`n_neighbors ≥ n` 时退化;sklearn 默认 5,小数据集可调到 √n
3. **平票时不稳定**:sklearn 文档明示 "if two neighbors k+1 and k have identical distances but different labels, the result will depend on the ordering of the training data";`weights='distance'` 缓解
4. **KD-Tree 高维诅咒**:d > 20 KD-Tree 比 brute 还慢(常数因子),`algorithm='auto'` 会自动选 Ball-Tree
5. **距离度量选择**:数据含 0/1 二值特征时 Manhattan 比 Euclidean 更合理;文本 TF-IDF 用 cosine
6. **回归 KNN**:KNeighborsRegressor 同 API;`weights='distance'` 加权均值,远点权重低
7. **缺失值**:KNN 自己不做缺失值填补,需预处理(`KNNImputer` 是 sklearn 独立类)

## 参考资料(实际阅读过的权威来源)

- [scikit-learn Nearest Neighbors §1.6](https://scikit-learn.org/stable/modules/neighbors.html) — 4 algorithm 对比、KDTree/BallTree 接口、unsupervised vs supervised
- [scikit-learn KNeighborsClassifier API](https://scikit-learn.org/0.24/modules/generated/sklearn.neighbors.KNeighborsClassifier.html) — n_neighbors / weights / algorithm / leaf_size / metric / p 全参数 + 平票警告
- [scikit-learn BallTree API](https://scikit-learn.org/dev/modules/generated/sklearn.neighbors.BallTree.html) — BallTree 完整接口、valid_metrics 列表(欧氏/曼哈顿/chebyshev/hamming/jaccard/haversine 等)
- Cover & Hart《Nearest Neighbor Pattern Classification》(1967 TIT) — KNN 收敛性奠基
- Friedman, Hastie, Tibshirani《Elements of Statistical Learning》Ch.13.3 — KNN 偏差-方差分析、curse of dimensionality
