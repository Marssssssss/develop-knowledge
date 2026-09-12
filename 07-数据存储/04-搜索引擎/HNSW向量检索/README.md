# HNSW 向量近似近邻检索

## 简介

HNSW（Hierarchical Navigable Small World）是 2016 年由 Yandex 的 Malkov 与 Yashunin 提出的**分层可导航小世界图**，专门用来在高维空间做**近似最近邻（ANN）**搜索——给定 query 向量，从亿级向量库里找出 top-k 个"足够近"的结果。它是当下事实标准：Milvus、Qdrant、Pinecone、Weaviate、Elasticsearch 8+、Lucene 9+，底层都用 HNSW（或其变体）作为缺省索引。

它要解决的问题：

- 暴力搜索（brute-force）复杂度 O(N·d) —— 上亿 × 768 维根本跑不动；
- KD-Tree / LSH 等老 ANN 算法在维度 ≥ 16 时遭遇"维度灾难"，recall 急剧下降；
- HNSW 用**多层图 + 贪心导航**，在**高维（128-1024 维）+ 大规模（10^7+）**下保持亚毫秒级返回率，且 recall@10 长期稳定在 95-99%。

- **关键概念**
  - **可达性图（proximity graph）**：每个节点是一个向量，边连向"距离近"的邻居；搜索在图上贪心走。
  - **多层结构**：最底层有全部节点，越高层越稀疏——高层如同"高速公路"，让搜索从全局视角快速跳到目标区域。
  - **M / M0**：每个节点除层 0 外每层最多 M 个邻居；层 0 用 2M（更稠以保住 recall）。
  - **efConstruction / ef**：插入 / 搜索的候选队列大小，控制 recall–latency trade-off。
  - **mL = 1/log M**：层数指数衰减因子——绝大多数节点只在层 0，越往上越罕。
- **历史背景**：起源于 NSW（Navigable Small World）2014 年同期工作，加入"分层 + 指数衰减层分配"成为 2016-2018 工业界最广泛采用的 ANN 索引；HNSW 与 Skip List 的结构对偶，常被描述为"向量库里的跳表"。

## 原理详解

### 1. 节点与层分配

每个向量插入时分配一个**最高层 `l`**：

```
l = floor( −log(uniform(0, 1]) · mL )         (paper Algorithm 1 line 4)
```

由于 `uniform` 大概率接近 1，绝大多数节点的 `l = 0`；只有少数点拿到 `l = 1, 2, ...`。结果：层数高的节点极少，恰好充当"高速公路枢纽"。

### 2. 多层结构

```
        L3:   ·————————·                    ← 跨区域高速公路
              ┊        ┊
        L2:   ·——·——·——·——·                ← 中等密度导航
              ┊   ┊   ┊   ┊
        L1:   ·——·——·——·——·——·——·——·——    ← 全部节点（最底层）
              ↕   ↕   ↕   ↕   ↕   ↕   ↕
```

层数随节点数指数衰减：`#nodes(L=k) ≈ N / log N · exp(−α·k)`。

### 3. 插入（Algorithm 1 精简）

```
INSERT(q, M, efConstruction):
    l = assign_level()
    if entry 还没确定:  entry = q;  return

    ep = entry
    # phase 1：自顶向下贪心，每层 ef=1 找最近的 1 个
    for lc = top .. l+1:
        W = SEARCH-LAYER(q, ep, ef=1, lc)
        ep = nearest(W)

    # phase 2：层 0..l，每层 ef=efConstruction，并双向连边
    for lc = l .. 0:
        W = SEARCH-LAYER(q, ep, ef=efConstruction, lc)
        neighbors = top-M of W  (层 0 用 top-2M)
        双向连边（修剪超过上限的旧边）
```

### 4. search_layer（Algorithm 2）

单层内的贪心遍历。维护两个集合：

- **C = candidates（待扩展）**：按到 q 的距离升序，最先扩展最近者；
- **W = dynamic nearest list**：已经发现的最近邻居集，cap = `ef`。

```
SEARCH-LAYER(q, ep, ef, lc):
    v = ep                      # visited
    W = ep
    C = ep
    while |C| > 0:
        c = nearest from C      # 距离 q 最近
        f = furthest from W     # 距离 q 最远
        if dist(c, q) > dist(f, q):
            break               # 再扩展也不可能更好
        for each e in neighborhood(c) at layer lc:
            if e ∉ v:
                v.add(e)
                if dist(e, q) < dist(f, q) or |W| < ef:
                    C.add(e); W.add(e)
                    if |W| > ef: W.drop(furthest)
    return W
```

### 5. K-NN 搜索（Algorithm 5）

```
K-NN-SEARCH(hnsw, q, K, ef):
    W = []
    ep = entry;  target_level = top
    for lc = target_level .. 1:        # 自顶向下，ef=1
        W = SEARCH-LAYER(q, ep, 1, lc)
        ep = nearest(W)
    W = SEARCH-LAYER(q, ep, ef, lc=0) # 层 0 用完整 ef
    return K nearest from W
```

**复杂度分析（paper §4.3）**：搜索访问的节点数 ≈ `O(log N)` —— 多层结构让"出-入-出"的全局跳转只走高层常数条边，进入层 1 后在稠密图里走"局部最优"。

### 6. 核心 API 参数

```text
hnsw_index = {
  "M":                16,          # 每层最大邻居数（除层 0）—— 越大 recall 越好但插入更慢
  "M0":               32,          # 层 0 最大邻居数（默认 2M）
  "efConstruction":   200,         # 插入候选队列 —— recall 上限
  "ef":               100,         # 搜索候选队列 —— recall/qps 旋钮
  "mL":   1.0 / log(M)             # 几乎不手动改
}
search(q, k, ef=ef):
  → 返回 top-k (id, distance)
```

## 对比 / 选型

| 维度 | **HNSW** | Annoy（Spotify） | IVF-PQ（Faiss） | ScaNN | KD-Tree |
|------|---------|------------------|------------------|-------|---------|
| 数据结构 | 多层图 | 多棵随机投影树 | 倒排 + 乘积量化 | 分区 + 各向异性量化 | k-d 树 |
| 高维（d≥128）recall | 95-99% | 80-90% | 85-95% | 95-99% | 维度灾难 |
| 构建时间 | 较慢 | 快 | 中 | 慢 | 快 |
| 查询延迟 | 亚毫秒 | 毫秒 | 毫秒 | 毫秒 | 微秒（低维） |
| 内存占用 | 高（每节点存邻居） | 中 | 低（量化） | 中 | 低 |
| 增量插入 | ✅ | ❌（需 rebuild） | ✅ | ❌ | ✅ |
| 工业代表 | Milvus、Qdrant、ES | Spotify 2017 之前 | Faiss | Google | scikit-learn 默认 |

**实战选**：
- 高维 + 在线流式写入 → HNSW（首先考虑）；
- 海量 + 内存紧 → IVF-PQ（量化牺牲一点 recall）；
- 离线一次构建、查询量小 → Annoy；
- 真的需要"exact" 时 → brute-force（也用作 recall@K 的 ground truth）。

## 环境准备

- Python ≥ 3.8（仅标准库 `math` / `random` / `dataclasses`）
- Go ≥ 1.20（仅标准库 `math` / `math/rand` / `sort`）

## 运行方式

```bash
# Python
python3 python/hnsw_demo.py

# Go
cd go && go run hnsw_demo.go
```

两个程序执行**完全相同的演示**：

1. 在 2D 平面上插入 20 个点；打印每个点的最高层与顶层邻居；
2. 用 4 个查询点跑 K-NN-SEARCH，对比暴力搜索的 ground truth，打印 recall@3；
3. 打印每层节点数分布——高层非常稀疏，"高速公路"意象。

## 关键代码片段

Python 版（`python/hnsw_demo.py`）：

```python
def assign_level(rng):
    """l = floor(−log(uniform) · mL)  —— 大多数节点只到层 0"""
    return int(math.floor(-math.log(rng.random()) * ML))

def knn_search(q, k, nodes, entry):
    ep = entry
    target = nodes[entry].level
    # 自顶向下，每层 ef=1 贪心
    for lc in range(target, 0, -1):
        W = search_layer(q, [ep], 1, lc, nodes)
        ep = W[0] if W else ep
    # 层 0 用完整 ef
    W = search_layer(q, [ep], EF_SEARCH, 0, nodes)
    return sorted(W, key=lambda x: euclid(q, nodes[x].vec))[:k]
```

Go 版（`go/hnsw_demo.go`）：`assignLevel` / `searchLayer` / `knnSearch` 三个核心函数一一对应 paper Algorithms 1 / 2 / 5。

## 性能与边界

- **复杂度**：插入 `O(M · efConstruction · log N)`，查询 `O(ef · log N)`，N 是节点总数。
- **配置 trade-off（paper §5 + Lucene 实测）**：
  - `M ↑` → recall ↑ 内存 ↑ 插入速度 ↓
  - `efConstruction ↑` → recall ↑ 插入时间线性 ↑
  - `ef ↑` → recall ↑ 查询 latency ↑（生产中按需在线调整）
- **维度敏感**：维度增高时**查询抖动变小**（curse of dimensionality 不显著恶化），但**内存占用线性增长**（每节点存 M·L 个邻居，L 通常 6-8）。
- **删除 / 更新**：vanilla HNSW 不直接支持删除；Lucene / Milvus 用"软删除 + markDeleted" + 后台合并回收。

## 注意事项与常见坑

1. **邻居数远超 `M`**：插入过密或经常 reorder，部分节点邻居数可能暂时超过 `M`；正常修剪策略会回稳。但若是反复插入-删除循环没合并，会出现度数爆炸。
2. **ef 太小 → 漏召回**：`ef < k` 是常见错误；至少 `ef ≥ k`，一般取 `ef = max(k, 50) ~ 200` 之间。
3. **维度归一化**：余弦相似要先 `L2 normalize` 再用欧氏距离等价的 HNSW；混用单位向量 + 非单位向量距离会被错配。
4. **冷启动 + 1 个 entry**：插入第一个节点时 entry 就是它，第二个节点要从 entry 走 search_layer——此时 graph 是空的，邻居集只能放 entry 自身；这一阶段边很少，recall 不足是正常的（"warmup"阶段）。
5. **扁平分布的向量**：HNSW 假设"数据有局部邻域结构"；如果语料真随机分布，recall 会比聚类语料差。解决：在写入前做 KMeans 聚类，再分片存。
6. **量化兼容**：HNSW 通常不与 Product Quantization 共用；FP32 + HNSW 是事实组合。如果非要 IVF + PQ 组合，应该走 Faiss 路径。

## 参考资料（实际阅读过的权威来源）

- [Malkov & Yashunin, *Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs*, arXiv:1603.09320v4 (2018)](https://arxiv.org/abs/1603.09320) — HNSW 论文原文，Algorithm 1/2/3/5 与 §4.1 复杂度分析
- [Vector search in Elasticsearch: The rationale behind the design | Elastic Search Labs Blog](https://www.elastic.co/search-labs/blog/vector-search-elasticsearch-rationale) — Lucene 9.x 把 HNSW 集成进段（segments）的设计权衡
- [Apache Lucene 10 release highlights | Elastic Search Labs Blog](https://www.elastic.co/search-labs/blog/apache-lucene-10-release-highlights) — Lucene 10 的 HNSW IO 优化（HNSW 绑定 mmap）
- [Hierarchical Navigable Small World (HNSW) — paper PDF](https://arxiv.org/pdf/1603.09320) — 上述论文 PDF 全文（含图 2-4 复杂度曲线）
- [ANN-Benchmarks (Spotify)](https://github.com/erikbern/ann-benchmarks) — HNSW vs Annoy / Faiss / ScaNN 等多家 recall-QPS 长期基准
