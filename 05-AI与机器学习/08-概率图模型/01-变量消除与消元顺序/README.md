# 变量消除与消元顺序启发式

## 1. 简介

变量消除（Variable Elimination, VE）是图模型上最朴素的精确推断算法：一次只对**一个**变量求和、把它消掉，用中间因子把已算过的结果传给下一步。它的复杂度不是由图的大小决定，而是由**消元顺序**决定——同一个模型、同一份 CPD，换个顺序可能从线性变成指数级。

本 demo 按 [pgmpy](https://github.com/pgmpy/pgmpy) 官方实现逐行转写「贪心求消元顺序」的整套机制：道德化 → 代价函数 → fill-in 边 → induced width，并给出可复现的官方 doctest 黄金值。

## 2. 原理

### 2.1 道德化

贝叶斯网是有向图，不能直接套无向图的团/消元概念。pgmpy 的 `BaseEliminationOrder.__init__` 先 `model.moralize()`：保留骨架、**给每个结点的全体父结点两两连边**（嫁娶），再丢掉方向。共同子结点的父结点因此在无向图里被绑成一个团。

### 2.2 贪心循环

```
while remaining:
    node = min(remaining, key=self.cost)      # 平局取列表中靠前者
    ordering.append(node)
    remaining.remove(node)
    self.moralized_model.add_edges_from(self.fill_in_edges(node))
    self.moralized_model.remove_node(node)
```

`fill_in_edges(node)` = 邻居两两组合中**尚无边**的一对。删掉一个点就必须把它的邻居粘成团，否则这些邻居之间的相关信息会丢失。

### 2.3 四类代价函数（+ Kjaerulff H1–H6）

| 启发式 | cost(node) | 记号 |
| --- | --- | --- |
| `MinFill` | 要补的边数 | — |
| `MinNeighbors` | 当前度数 | — |
| `MinWeight` | 邻居基数之积 | — |
| `WeightedMinFill` | 每条待补边按两端基数乘积计价后求和 | — |
| `H1..H6` | `S`、`S/E`、`S−M`、`S−C`、`S/M`、`S/C` | `S`=邻居基数积，`E`=自身基数，`M`/`C`=含该点的极大团 Size 的最大值/之和 |

### 2.4 induced width

`induced_width(order) = 诱导图最大团大小 − 1`。诱导图的团集合 = **CPD 作用域 ∪ 每步消元产生的中间作用域**；pgmpy 的官方口径是「每个因子只计一次」——已经含被消掉变量的因子不再参与后续消元。

## 3. 对比：数边 ≠ 加权

同一张图上，两种口径会给出**相反**的顺序：

- 图：`x—p, x—q, x—y, y—m, y—n, y—o`，基数 `x=y=p=q=10`、`m=n=o=2`，只消 `{x, y}`
- `MinFill`：`x` 只需补 3 条边，`y` 要补 6 条 ⇒ 选 `x`（度数的 `MinNeighbors` 也选 `x`，3 < 4）
- `WeightedMinFill`：`x` 的边都是 10×10（共 300），`y` 只有 20/20/20/4/4/4（共 72）⇒ 选 `y`

实测两种顺序真正补出的边数是 **12（x→y）** 与 **13（y→x）**：数边口径这次反而补得更少——说明**启发式之间不存在支配关系**，选谁要看代价模型是否贴合真实计算量（真实计算量 ∝ 中间因子的表格大小 ∝ 基数乘积）。

## 4. 环境

Python 3.13（纯标准库，无第三方依赖）；Go 1.21（无工具链时按 README 人工审查）。

## 5. 运行

```bash
cd python && python selfcheck_elim.py     # 41 条断言，实跑通过
cd ../go && go run .
```

## 6. 关键代码

```python
def fill_in_edges(self, node):
    graph = self.moralized_model
    return [(u, v) for u, v in combinations(graph.neighbors(node), 2)
            if not graph.has_edge(u, v)]
```

整条贪心链只有这一个几何操作 + 一个 `cost()`，七种启发式共用同一个循环（模板方法模式）。

## 7. 性能边界

- 消元顺序决定一切：链 `A—B—C—D` 上从头/尾消宽度为 **1**，先消中间点 `B` 会造出团 `{A,C}`，宽度变 **2**。
- 宽度 `w` 的模型，VE 的代价 ∝ `N·d^(w+1)`（`d` 为最大基数），所以启发式的目标是最小化 `w`，不是最小化边数。
- 三角化（沿顺序补齐全部 fill-in 边）后整图必为**弦图**；`networkx` 用 MCS（最大势搜索）+「已编号邻居集必须是团」来判定弦性。
- 贪心启发式不保证最优，只是工程上够用；Kjaerulff(1990) 的 H1–H6 是在基数已知时更精细的估价。

## 8. 坑

1. **`get_elimination_order(nodes=...)` 的 `nodes` 只做集合筛选**：`remaining` 由图结点插入序过滤而来，传入 `["z","y","x"]` 得到的仍是 `["x","y","z"]`。想控制顺序必须改图的插入序。
2. **平局靠 `min()` 的稳定性裁决**：`min(remaining, key=cost)` 返回最先遇到的最小值，所以同代价时按列表先后——这也是官方 doctest 第 2 步在 `d`/`l` 都为零时取 `d` 的原因。
3. **道德化会凭空造边**：`j` 的父结点 `s`/`l` 因此相邻，算 `s` 的代价时邻居是 `{i,j,l}` 而不是 `{i,j}`（本 demo 实测 8 而非 4）。
4. **induced graph 里「因子只计一次」**：把已含被消变量的因子再算一遍会让团无谓膨胀，宽度只增不减。
5. **fill-in 边数与计算量不成正比**：见 §3，MinFill 补边更少但基数乘积更大。

## 9. 参考（本轮实际读过的来源）

- [pgmpy/pgmpy@dev — `pgmpy/inference/EliminationOrder.py`](https://raw.githubusercontent.com/pgmpy/pgmpy/dev/pgmpy/inference/EliminationOrder.py)：贪心框架、`fill_in_edges`、MinFill/MinNeighbors/MinWeight/WeightedMinFill、Kjaerulff H1–H6 的 `S/E/M/C` 定义与 `WeightedMinFill` 官方 doctest（Asia 网 → `['c','d','l','s','g']`）
- [pgmpy/pgmpy@dev — `pgmpy/inference/ExactInference.py`](https://raw.githubusercontent.com/pgmpy/pgmpy/dev/pgmpy/inference/ExactInference.py)：`induced_graph` / `induced_width` 的团集合构造与「每个因子只计一次」口径、官方 doctest `induced_width(["C","D","A","B","E"]) == 3`
- [pgmpy/pgmpy@dev — `pgmpy/models/JunctionTree.py`](https://raw.githubusercontent.com/pgmpy/pgmpy/dev/pgmpy/models/JunctionTree.py)：连接树的结点是团、边是 sepset，`add_edge` 会拦「成环破坏连接树性质」、`check_model` 要求连通
- [networkx/networkx@main — `networkx/algorithms/chordal.py`](https://raw.githubusercontent.com/networkx/networkx/main/networkx/algorithms/chordal.py)：`is_chordal`（MCS + chordality breaker）、`chordal_graph_treewidth = 最大团 − 1`、`complete_to_chordal_graph` 用 MCS-M 做**极小**（非最小）三角化
- Kjærulff, U. (1990). *Triangulation of graphs — algorithms giving small total state space*：H1–H6 六个启发式的出处
- Berry, Blair, Heggernes & Peyton (2004). *Maximum Cardinality Search for Computing Minimal Triangulations of Graphs*. Algorithmica 39: 287–298：MCS-M 的出处

> 口径说明：`networkx` 的 `complete_to_chordal_graph` 只保证**极小**三角化（不能再删边），不保证**最小**（边数最少）；pgmpy 的贪心同理。二者都会在 README/文档里把这一点写清楚，本 demo 不做「最优三角化」的断言。
