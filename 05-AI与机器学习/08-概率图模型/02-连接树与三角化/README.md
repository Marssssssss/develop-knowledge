# 连接树与三角化

## 1. 简介

变量消除一次只回答一个查询；想一次拿到**所有**变量的边缘分布，需要把「消元」这件事提前做掉——这就是连接树（junction tree / clique tree）：先把图**三角化**成弦图，取其极大团做结点，按 sepset 权重建一棵树，再跑两趟消息传递（Lauritzen-Spiegelhalter）把它**校准**。校准后任意团的信念就是对应变量的联合边缘。

本 demo 按 networkx 的 `chordal.py` 与 pgmpy 的 `ExactInference.py` / `JunctionTree.py` 逐行转写，并用**暴力枚举联合分布**作独立真值参照。

## 2. 原理

### 2.1 三角化（MCS-M）

`networkx.algorithms.chordal.complete_to_chordal_graph` 用的是 **MCS-M**（最大势搜索 + 极小填边）：

```
for i in range(n, 0, -1):
    z = max(unnumbered, key=lambda node: weight[node])   # 权重最大者先编号
    alpha[z] = i
    for y in unnumbered:
        if G.has_edge(y, z):      update_nodes.append(y)        # 原图已有边
        elif has_path(H.subgraph([权重 < weight[y] 的点] + [z, y]), y, z):
            update_nodes.append(y); chords.add((z, y))          # 需要补弦
    for node in update_nodes: weight[node] += 1
```

注意两处「不对称」：**判边用原图 `G`，判通路用正在被加边的 `H`**；通路只在「权重严格小于 `y`」的子图上找。文档明确说它给出的是**极小**（minimal，不能再删边）三角化，不是**最小**（minimum，边数最少）。

### 2.2 极大团与树宽

`chordal_graph_cliques` 在 MCS 序下取「已编号邻居集 ∪ 自身」即得极大团；`chordal_graph_treewidth = 最大团大小 − 1`。官方 doctest：`nx.barbell_graph(4, 6)` → **3**（两个 K4 决定最大团为 4）。

### 2.3 连接树的构造与 RIP

团树的边权取 `|Ci ∩ Cj|`，取**最大生成树**即得连接树。它必须满足**交性（running intersection property）**：任意两团的公共变量，出现在它们之间路径上的**每一个**团里。pgmpy 的 `JunctionTree.add_edge` 会在「两点间已有通路」时直接抛错（成环会破坏连接树性质），`check_model` 要求整棵树连通。

### 2.4 LS 校准

```
beliefs[C] = 团 C 上的势函数之积      # 初始化
sepsets[e] = 1                        # 初始化
collect:     对 BFS 边序**逆序**发消息（叶 → 根）
distribute:  对 BFS 边序**正序**发消息（根 → 叶）

σ   = Σ_{Ci − S_{ij}} βi              # 投影到 sepset
βj ← βj · (σ / μ_ij)                  # 除掉旧消息再吸收
μ_ij ← σ                              # 记住这一轮的消息
```

收敛判据是「一条边两侧对 sepset 的投影**与 sepset 信念三者相等**」。

## 3. 对比：除掉旧消息是必需的

两趟消息传递里，同一条边会被走两次。如果不做 `βj ← βj·(σ/μ)` 中的**除法**，就等于把同一条消息乘了两遍。本 demo 用 `calibrate(divide_out=False)` 做负向对照：真值由暴力枚举给出，关掉除法后**至少有一个变量的边缘与真值不符**（实测 5 个变量全部偏离），而保留除法时全部吻合（容差 1e-9）。

## 4. 环境

Python 3.13（纯标准库）；Go 1.21（无工具链时按 README 人工审查 + 三项静态检查）。

## 5. 运行

```bash
cd python && python selfcheck_jt.py     # 40 条断言，实跑通过
cd ../go && go run .
```

## 6. 关键代码

```python
def _update(self, sender, receiver, divide_out=True):
    sepset = sorted(set(self.cliques[sender]) & set(self.cliques[receiver]))
    sigma = self.beliefs[sender].marginalize([v for v in self.cliques[sender]
                                              if v not in sepset])
    key = frozenset((sender, receiver))     # 一条边只存一份 sepset 信念
    if divide_out and key in self.sepsets:
        self.beliefs[receiver] = self.beliefs[receiver].product(
            sigma.divide(self.sepsets[key]))
    else:
        self.beliefs[receiver] = self.beliefs[receiver].product(sigma)
    self.sepsets[key] = sigma
```

## 7. 性能边界

- 团树算法的代价 ∝ Σ_C |域(C)|，域大小是基数乘积 —— 树宽 `w`、最大基数 `d` 时是 `O(n·d^(w+1))`。**树宽才是瓶颈**，三角化就是在 minimize 它。
- 三角化是 NP-hard：MCS-M 与 min-fill 都是启发式，只保证极小不保证最小。
- 两趟（collect + distribute）就够：树上没有环，`n−1` 条边各走两次即可全局校准；多跑一轮也不会再变。

## 8. 坑

1. **sepset 信念按「边」存而不是按「方向」存**：pgmpy 用 `frozenset(edge)` 作键，collect 阶段写下的 `μ` 会被 distribute 阶段除掉。按方向存两个 `μ` 会让除法失效、信念重复计入（本 demo 第一版就踩了，`is_converged` 直接失败）。
2. **`complete_to_chordal_graph` 对已是弦图的输入会短路**：直接返回原图与全 0 的 `alpha`，不要拿 `alpha` 当消元顺序去用。
3. **判边用 `G`、判通路用 `H`**：两个图不一样，写反会得到非弦的「三角化」结果。
4. **`alpha` 是 1..n 的排列，值越大越先被消**，不要当成「第几个被消」。
5. **交性必须显式校验**：最大生成树在退化情形（权重并列）下仍可能不满足 RIP，本 demo 提供了 `check_running_intersection` 逐对路径验证。

## 9. 参考（本轮实际读过的来源）

- [networkx/networkx@main — `networkx/algorithms/chordal.py`](https://raw.githubusercontent.com/networkx/networkx/main/networkx/algorithms/chordal.py)：`is_chordal` / `find_induced_nodes` / `chordal_graph_cliques` / `chordal_graph_treewidth`（官方 doctest `barbell_graph(4,6) → 3`）/ `complete_to_chordal_graph`（MCS-M，文档明示 minimal 而非 minimum）
- [pgmpy/pgmpy@dev — `pgmpy/inference/ExactInference.py`](https://raw.githubusercontent.com/pgmpy/pgmpy/dev/pgmpy/inference/ExactInference.py)：`BeliefPropagation._update_beliefs`（σ / 除旧消息 / 存 μ）、`_is_converged`（sepset 三方一致）、`_calibrate_junction_tree`（Lauritzen-Spiegelhalter，引 Koller & Friedman Algorithm 10.3）
- [pgmpy/pgmpy@dev — `pgmpy/models/JunctionTree.py`](https://raw.githubusercontent.com/pgmpy/pgmpy/dev/pgmpy/models/JunctionTree.py)：团作结点、sepset 作边、`add_edge` 的成环拒绝、`check_model` 的连通性要求
- Berry, Blair, Heggernes & Peyton (2004). *Maximum Cardinality Search for Computing Minimal Triangulations of Graphs*. Algorithmica 39: 287–298：MCS-M 的出处
- Koller & Friedman (2009). *Probabilistic Graphical Models* Algorithm 10.3（由 pgmpy 源码脚注索引）：团树校准

> 口径说明：本 demo 的势函数直接定义在弦图的极大团上（联合 ∝ ∏ψ_C），因此「校准后的团信念 = 真实边缘」可以被暴力枚举严格验证；若势函数定义在一般图上，需先把每条势函数指派给一个包含其作用域的团，这一步的不同指派只影响常数因子，不影响归一化后的边缘。
