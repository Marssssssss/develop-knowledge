# PageRank 幂迭代算法

## 简介

**PageRank** 由 Larry Page 与 Sergey Brin 于 1998 年在 Stanford 提出，最初用于 Google 搜索对网页排序。它把"重要性"建模为一个**随机游走者**（random surfer）——在每一步以概率 d=0.85 沿着当前页面的出链之一继续浏览，以概率 1−d=0.15 跳到任意随机页面（即"teleport"）。

稳态分布（stationary distribution）就是每个节点的 PageRank 分数。它的优雅之处在于：**Teleport 让不可约+非周期条件成立 → Perron-Frobenius 定理保证唯一收敛 → 幂迭代（power method）线性收敛**。

本 demo 用 Python + Go 双版本实现幂迭代，并处理悬挂节点（dangling node：出度为 0）。

## 原理详解

### 1. 公式

Wikipedia 给出的标准形式（d 为阻尼因子，N 为节点数）：

```
PR(u) = (1 - d) / N + d * Σ_{v → u} PR(v) / L(v)
```

其中 L(v) 是 v 的出度。

### 2. 悬挂节点（Dangling Node）问题

若 v 的出度为 0（无出链），`PR(v) / L(v)` 分母为 0 → 该节点上的 PR 无法传递。处理方法：

> "a random surfer jumps to a random page upon visiting a page with no links, in order to avoid the rank-sink effect"

——把 dangling node 的 PR 总和乘以 d/N 平摊到所有节点。

### 3. 幂迭代伪代码

```
PR_0 = uniform(1/N)
repeat until |PR_{k+1} - PR_k|_∞ < tol:
    for each node u:
        new_PR[u] = (1 - d) / N + d * Σ_{v → u} PR_k[v] / L(v)
    for each dangling node v:
        for each node u: new_PR[u] += d * PR_k[v] / N
    PR_{k+1} = new_PR
```

### 4. 收敛速度

阻尼因子 d=0.85 直接决定收敛速度：
> "Selecting a damping factor significantly smaller than 1 allows for fast convergence of the power method, since α is in fact the contraction factor."

每轮误差减少至少 (1−d)=0.15（几何收敛）。Google 早期 2600 万页数据集用 50~100 轮收敛。

### 5. 经典 4 节点示例

Wikipedia "PageRank" 的经典示例：
```
B → A, C
C → A
D → A, B, C
```
A 是 hub（所有节点都指向它），预期 PR(A) > PR(D) > PR(B) ≈ PR(C)。

## 对比

| 算法 | 适用规模 | 收敛速度 |
|---|---|---|
| 幂迭代（本 demo） | 中小图 | 几何级,每轮 (1-d) ≈ 15% 误差减少 |
| Arnoldi / Lanczos | 大图（>10^9） | 更快但代价大 |
| Inner-Outer 迭代 | 超大规模 | 收敛更稳 |

## 环境准备

- Python ≥ 3.8
- Go ≥ 1.21

## 运行方式

```bash
# Python
cd python && python pagerank_demo.py

# Go
cd go && go run pagerank_demo.go
```

## 关键代码片段

Python 幂迭代：
```python
teleport = (1.0 - damping) / N
for it in range(max_iter):
    new_pr = {n: teleport for n in nodes}
    for u, v in edges:
        if out_deg[u] > 0:
            new_pr[v] += damping * pr[u] / out_deg[u]
    # 悬挂节点贡献
    dangling_sum = sum(pr[n] for n in nodes if out_deg[n] == 0)
    if dangling_sum > 0:
        share = damping * dangling_sum / N
        for n in nodes:
            new_pr[n] += share
    diff = max(abs(new_pr[n] - pr[n]) for n in nodes)
    if diff < tol: break
    pr = new_pr
```

## 性能与边界

- 时间：O(k · (V + E))，k 为收敛轮数；典型 k ≈ 50~100，d=0.85 时大约 50 轮达到 1e-6 精度。
- 空间：O(V + E)。
- **悬挂节点**：必须处理，否则 hub 上的 PR 会被"吸收"导致悬挂节点外分数全 0。
- **初值无关**：任何非负初始向量都会收敛到同一稳态（依据 Perron-Frobenius 定理）。
- **sum(PR) = 1**：是概率分布，所有 PR 之和恒为 1，可作自检。

## 注意事项与常见坑

- **悬挂节点陷阱**：实现时常被忽略，导致 dangling node 的 PR "凭空消失"，分数不归 1。
- **d=1 不收敛**：若 d=1，纯链接矩阵可能不满足不可约+非周期，幂迭代会陷入循环或长周期。
- **Web vs 内部图**：Web 链接图近似 bow-tie 结构，建议 d 在 0.5~0.99 区间选择；其他场景（如推荐系统、知识图谱）需实验调优。
- **个性化向量 v**：本 demo 用均匀分布 teleport；若改为非均匀（"personalized PageRank"），可对特定节点增加权重。

## 参考资料

- [Wikipedia - PageRank](https://en.wikipedia.org/wiki/PageRank) — 公式、收敛性、悬挂节点处理
- [Greif/Callut SIAM Review 2010 - An Inner-Outer Iteration for Computing PageRank](https://www.cs.ubc.ca/~greif/Publications/gggl2010.pdf) — 收敛率与 α 关系、内积 outer iteration
- [world-of-mathematics.com - PageRank: The Linear Algebra Behind Google](http://world-of-mathematics.com/blog/pagerank-and-google) — Perron-Frobenius 与幂迭代直观解释