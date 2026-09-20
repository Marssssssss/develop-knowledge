# Louvain 社区发现与模块度

> 目录：`07-数据存储/05-图数据库/LouvainModularity/`（Neo4j/Cypher 第三批）
> 实现：Python（`louvain.py` + `selfcheck_louvain.py`，48 断言实跑全绿）、Go（`louvain.go` + `louvain_levels.go` + `selfcheck_louvain.go`）

## 1. 简介

Louvain 是图数据库里做社区发现的事实标准（Neo4j GDS 内置）。它的核心不是某个巧妙的聚类规则，而是**一个能增量计算的模块度增益公式**：每次只把单个节点移到邻居社区，用 ΔQ 判断值不值，从而把"全局最优划分"这个 NP-hard 问题变成线性时间的贪心过程。

本 demo 从原论文实现模块度、ΔQ 增量公式、两阶段迭代与聚合，并用论文里的 **ring of 30 cliques** 复现其两次 pass 的结果。

## 2. 原理详解

### 2.1 模块度（论文式 1）

```
Q = 1/(2m) · Σ_ij [ A_ij − k_i·k_j/(2m) ] · δ(c_i, c_j)
```

- `A_ij`：i、j 之间边的权重
- `k_i = Σ_j A_ij`：入射到 i 的边权之和
- `m = (1/2) Σ_ij A_ij`
- `δ(u,v)`：u = v 为 1，否则 0

Q 是 **−1 到 1 之间的标量**，衡量"社区内部连边密度 vs 社区之间连边密度"。按社区分组后可改写成：

```
Q = Σ_C [ Σin(C)/(2m) − (Σtot(C)/(2m))² ]
```

其中 `Σin(C)` 是 C 内部边权之和（按双重求和口径，含自环的 2 倍），`Σtot(C)` 是**入射到 C 中节点**的边权之和。这个等价形式是 ΔQ 公式的基础。

### 2.2 ΔQ 增量公式（论文式 2）

把**孤立**节点 i 移入社区 C 的增益：

```
ΔQ = [ (Σin + k_i,in)/(2m) − ((Σtot + k_i)/(2m))² ]
     − [ Σin/(2m) − (Σtot/(2m))² − (k_i/(2m))² ]
```

`k_i,in` 是 i 到 C 中节点的边权之和。这个式子只依赖 C 的三个汇总量，**不需要重扫全图**，这正是 Louvain 快的原因。

**关键实现细节（最容易写错）**：论文说的是"先把 i 从原社区 **移除**，再评估移入邻社区的增益"。所以一次真实移动的净增益是

```
净增益 = ΔQ_add(C) − ΔQ_add(D \ {i})
```

而不是 `ΔQ_add(C) > 0`。本 demo 用断言固定住这一点：在两个三角形 + 一条连接边的图上，把节点 2 从社区 {0,1,2} 移到 {3,4,5}，ΔQ_add 分别是 `0.1633` 和 `−0.0714`，**净增益 = −0.2347**，与重算 Q 的差值**精确相等（1e-9）**。若只按 `ΔQ_add(C) > 0` 判断，就会做出错误的移动。

### 2.3 两阶段与 pass

**第一阶段**：初始每个节点一个社区；反复按顺序考察每个节点，把它移入**净增益最大且为正**的邻居社区；直到没有任何单个移动能改进 Q（局部极大）。一个节点会被考察多次。

**第二阶段**：把第一阶段得到的社区**聚合**成超节点。两个超节点之间的边权 = 原来两个社区之间所有边权之和；**同一社区内部的边变成该社区的自环**。

两个阶段合称一次 **pass**，反复迭代直到不再变化。**社区数每 pass 严格减少**，所以绝大部分计算时间花在第一个 pass 上。

### 2.4 自环约定（本 demo 显式采用并断言）

聚合图的自环在邻接矩阵里必须记为 `A_ii = 2·s_i`。只有这个约定能同时满足：

1. `m = 全部边权之和`（自环按一次计入）；
2. **Q 在聚合前后保持不变**。

自检里两条都断言了：聚合后 `m` 不变、`Q` 不变（1e-9）；同时用**反向断言**固定住——若把自环只记一次（`A_ii = s_i`），`m` 与"总边权"会不相等，`A(a,a)` 也会算错。这是实现 Louvain 时最隐蔽的一个坑。

### 2.5 顺序依赖与参数

论文明确指出**输出依赖节点考察顺序**（"The output of the algorithm depends on the order in which the nodes are considered"），并说初步结果显示顺序对最终 Q 影响不大、但对计算时间有影响。本 demo 按节点编号顺序考察以保证可复现。

Neo4j GDS 暴露的参数语义：

| 参数 | 语义 |
| --- | --- |
| `maxLevels` | 最大层级数 |
| `maxIterations` | **每个 level** 上模块度优化的最大迭代次数 |
| `tolerance` | 迭代间模块度变化小于该值即视为稳定并返回 |
| `includeIntermediateCommunities` | 是否输出中间层级的社区 |
| `consecutiveIds` | 社区 ID 是否连续编号 |
| `seedProperty` / `relationshipWeightProperty` | 种子与边权属性 |

结果含最终 `modularity` 与**每个 level 的 modularity 列表**（本 demo 的 `q_per_level` 与之对应）。

## 3. 对比：贪心 vs 精确

| 维度 | 精确模块度优化 | Louvain |
| --- | --- | --- |
| 复杂度 | NP-hard | 典型稀疏数据上**近似线性** |
| 是否全局最优 | 是 | **否**（局部极大） |
| 层次结构 | 无 | 天然多层级（社区之社区） |
| 分辨率极限 | 有 | 部分缓解（第一阶段只移动单个节点） |

## 4. 环境

- Python 3.8+（标准库）；Go 1.18+（标准库）。
- ring of 30 cliques（150 节点）在本机约 2.5 秒跑完。

## 5. 运行方式

```bash
cd LouvainModularity
python selfcheck_louvain.py     # 48 条断言，输出 "ALL OK"
go run .
```

## 6. 关键代码

`delta_q_add` 就是式 (2) 的直接翻译：

```python
def delta_q_add(g, part, i, target):
    m = g.m()
    sin  = _sigma_in(g, target)     # Σin
    stot = _sigma_tot(g, target)    # Σtot
    ki   = g.k(i)                   # k_i
    ki_in = sum(g.A(i, x) + g.A(x, i) for x in target)   # k_i,in（双向）
    after  = (sin + ki_in) / (2*m) - ((stot + ki) / (2*m)) ** 2
    before = sin / (2*m) - (stot / (2*m)) ** 2 - (ki / (2*m)) ** 2
    return after - before
```

第一阶段里「先移除、再选最好的社区」：

```python
cands    = {part[j] for j in g.neighbors(i)} | {own}
baseline = delta_q_add(g, part, i, rest[own])   # 留在原社区（去掉 i 后）
best_c, best_gain = own, baseline
for c in cands:
    gain = delta_q_add(g, part, i, rest[c])
    if gain > best_gain + TOL:
        best_gain, best_c = gain, c
if best_c != own:            # 净增益 > 0 才真的动
    part[i] = best_c
```

## 7. 性能边界与注意事项

- **局部极大不是全局最优**：不同考察顺序会得到不同划分（Q 接近但社区边界不同）。
- **分辨率极限（resolution limit）**：模块度优化识别不出小于某个尺度的社区。本 demo 量化了这个征兆——在 ring of 30 cliques 上，**自然划分（30 个团）的 Q = 0.8758，反而低于两两合并后的 15 社区 Q = 0.8879**；而只有 2 个团时合并会**降低** Q，算法正确地保持 2 个社区。
- **tolerance 是把双刃剑**：调大能提前返回（自检里 tolerance=10 时第一轮就停），但会牺牲划分质量。
- **自环必须双重计数**，否则聚合后 Q 会漂移，多层级迭代的结果不可信。

## 8. 常见坑

1. **把 ΔQ_add(C) > 0 当成移动条件**——没有先扣掉"留在原社区"的收益，会做出降低 Q 的移动。
2. **自环只记一次**——破坏 `m` 与聚合不变性，且症状隐蔽（前几层看不出来）。
3. **聚合时漏掉原有自环**——社区内部的自环也要并进超节点的自环权重。
4. **候选社区不含原社区**——那么节点一旦被移出就无法"不动"，算法会在两个社区间来回抖动。
5. **忘记 Σtot 是"入射到 C 的边权"**——包含跨出社区的那部分，不能只算内部边。
6. **以为 Louvain 给的是全局最优**——它是启发式，同一份数据多次运行（不同顺序）结果可能不同。

## 9. 参考资料（实际阅读）

- Blondel, Guillaume, Lambiotte, Lefebvre — [Fast unfolding of communities in large networks](https://arxiv.org/abs/0803.0476), arXiv:0803.0476v2（12 页 PDF 全文抽取后逐段回读：式(1)(2)、两阶段、聚合自环、ring of 30 cliques 两次 pass、分辨率极限讨论、复杂度论断）
- [Neo4j Graph Data Science → Louvain](https://neo4j.com/docs/graph-data-science/current/algorithms/louvain/) — GDS 版的参数（maxLevels / maxIterations / tolerance / includeIntermediateCommunities / consecutiveIds / seedProperty）与结果字段（最终 modularity、每 level modularity 列表）
