# JPS：跳点搜索的剪枝规则与最优性

> 新子领域条目 `01-游戏开发/06-AI/寻路算法/跳点搜索JPS`。JPS（Jump Point Search，Harabor & Grastien, AAAI 2011）解决的是网格寻路里最烦人的问题：**路径对称**——一大片空地上从 A 到 B 有成千上万条等长路径，A\* 会乖乖地把它们全展开一遍。本 demo 按论文原文实现剪枝规则、跳点判定与两个算法，并与朴素 8 连通 A\* 对拍。

## 简介

JPS 的核心是一个 **macro operator**：不逐格展开，而是沿方向「跳」，只把**跳点**放进 open list，两个跳点之间的中间节点**永远不展开**。论文证明了这保持最优性，且不需要预处理、不占额外内存。

三条定义的原文口径：

| 概念 | 原文 | 本 demo 的实现 |
| --- | --- | --- |
| 剪枝（直线） | `len(⟨p(x),...,n⟩\x) ≤ len(⟨p(x),x,n⟩)` | 3x3 邻域内 BFS 求「绕开 x 的最短路」，与「经过 x」比长度 |
| 剪枝（对角） | 同上但**严格** `<` | 同上，判据换成严格小于 |
| natural neighbour | 无障碍假设下剪完之后剩下的 | 直线移动 = `{d}`；对角移动 = `{d, d₁, d₂}` |
| forced neighbour | 不是 natural，且 `len(⟨p(x),x,n⟩) < len(⟨p(x),...,n⟩\x)` | 同上 |

**关键**：`natural` 是按**移动方向**固定下来的集合（与障碍无关），`forced` 才是「因为障碍存在而变得必要」的例外。论文 Figure 2 的两个 forced 例子：

- 图 2(b)：向右直行，`x` 正上方是障碍 ⇒ **右上（编号 3）** 变成 forced；
- 图 2(d)：向右上对角，`x` 正左方是障碍 ⇒ **左上（编号 1）** 变成 forced。

## 原理详解

### 1. 邻居编号沿用论文 Figure 2

```
1 2 3     1 = 左上   2 = 上   3 = 右上
4 x 5     4 = 左             5 = 右
6 7 8     6 = 左下   7 = 下   8 = 右下
```

对着论文读代码时不用再换算：`p(x) = 4` 就是「从左往右走」，natural 只剩 `5`；`p(x) = 6` 就是「往右上走」，natural 是 `{2, 3, 5}`——与论文正文「prune all neighbours except n = 2, n = 3 and n = 5」一致。

### 2. `len(⟨p(x),...,n⟩\x)` 是 BFS，不是欧氏距离

这条路径必须**只由 `neighbours(x)` 里的节点组成，且不经过 `x`**。本 demo 直接在 3x3 邻域里跑一次 BFS（直线边权 1、对角边权 √2），走不到就返回 `inf`——**`inf` 正是 forced 成立的原因**：绕不过去，所以只能经过 `x`。

实测（5x5，向右直行，正上方放障碍）：

| 候选 n | `len(⟨p,x,n⟩)` | `len(⟨p,...,n⟩\x)` | 结论 |
| --- | --- | --- | --- |
| 3（右上） | 2.414 | **3.828** | **forced**（绕不过去，只能经过 x） |
| 6（左下） | 2.414 | 1.000 | 剪掉 |
| 7（下） | 2.000 | 1.414 | 剪掉 |
| 8（右下） | 2.414 | 2.414 | 剪掉（直线用 `≤`，等号也剪） |

### 3. Definition 2：跳点的三个条件

```
条件 1：y 就是目标
条件 2：y 至少有一个 forced neighbour
条件 3：d 是对角方向，且沿 d₁ 或 d₂ 能跳到一个跳点
```

`Algorithm 2 (jump)` 的顺序是**先条件 1、再条件 2、再（若对角）递归试两个正交方向、最后沿 d 递归**。第三条是论文特别强调的："**This check is essential for preserving optimality**"——对角步进之前必须先确认两个正交方向都跳不动。

### 4. Algorithm 1：先剪枝，再跳

```
successors(x) ← ∅
neighbours(x) ← prune(x, neighbours(x))
for all n ∈ neighbours(x):
    n ← jump(x, direction(x,n), s, g)
    add n to successors(x)
```

起点没有 `p(x)`，**不剪枝**（论文原文："if x is the start node p(x) is null and nothing is pruned"）。

## 对比：JPS 与朴素 A\*（本 demo 实测）

| 地图 | 起点→终点 | JPS 展开节点 | A\* 展开节点 | 路径代价 |
| --- | --- | --- | --- | --- |
| 15×15 空旷 | (0,0)→(14,14) | **1** | 14 | 相等 |
| 30×30 空旷 | (0,0)→(29,29) | **1** | 29 | 相等 |
| 30×30 随机障碍 15% | (0,0)→(29,29) | 48 | 78 | 相等 |
| 12×12 带缺口竖墙 | (1,5)→(10,5) | 2 | 9 | 相等 |
| 40×40 随机障碍 20% | (0,0)→(39,39) | 81 | 123 | 相等 |

「展开节点」= 从 open list 弹出的次数。**每一组代价都严格相等**（`|Δ| < 1e-9`），这就是 Theorem 1 说的最优性保持。

## 环境

- Python 3.12+（仅标准库）
- Go 1.21+（无第三方依赖）；本机无 Go 工具链时走人工审查 + `bracket_check.py` / `go_sanity.py`

## 运行方式

```bash
cd python && python selfcheck_jps.py     # 30 条断言，输出 PASS = 30 + 两组展开数对比
cd go     && go run .                     # 打印 forced 判定与 30x30 空旷地图的跳跃结果
```

## 关键代码

Python：`forced` 的判定（Definition 1 的直译）

```python
for i in grid.neighbours(x):
    if i in natural:
        continue
    n = (x[0] + NB[i][0], x[1] + NB[i][1])
    with_x = octile(p, x) + octile(x, n)
    without_x = path_without_x(grid, x, p, n)     # 走不到 → inf
    if with_x < without_x:
        out.append(i)
```

Python：`jump` 的条件 3（对角步进前先试正交方向）

```python
if d[0] != 0 and d[1] != 0:
    for di in ((d[0], 0), (0, d[1])):
        if jump(grid, n, di, start, goal) is not None:
            return n
return jump(grid, n, d, start, goal)
```

## 性能边界

- **省的是「展开」，不是「扫描」**：`jump` 本身仍然要一格一格走过中间节点（只是不入 open list）。在空旷地形上省得最多（实测 1 vs 29），在障碍密集、跳点遍地的地方优势会明显缩小（40×40、20% 障碍时 81 vs 123）。
- **递归 `jump` 是尾递归形式的深递归**：长直走廊上递归深度等于走廊长度，Python 默认递归上限 1000 会先炸；Go 侧是迭代友好的，但仍要注意栈。
- **每次 `forced` 判定都要跑一次 3x3 BFS**，这是本实现为了「忠实于定义」付出的常数开销；工程实现（含论文作者自己的代码）都是把规则表硬编码成查表。
- 论文的前提是 **uniform-cost、8 连通、无障碍代价差异** 的网格；一旦引入地形代价（沼泽、坡度），剪枝规则的前提（式 (1)(2) 的等号关系）不再成立，必须换 JPS+ 之类的变体。

## 注意事项与常见坑

1. **直线用 `≤`、对角用 `<`**：两处判据差一个等号，照抄成一样的会在对角移动上误剪 forced 邻居，从而破坏最优性（而且只在特定障碍布局下暴露）。
2. **`natural` 与障碍无关**：它是按移动方向固定下来的集合。把「剪完之后剩下的」当成 natural，会让 Definition 1 的两条变成自相矛盾（无障碍时 `!natural` 与 `len_with < len_without` 不可能同时成立）。
3. **起点不剪枝**，`p(x)` 为空时 8 个邻居全部参与。
4. **条件 3 不能省**：对角方向上必须先确认两个正交方向都跳不动，否则会跳过真正的转弯点，路径变长。
5. **`jump` 返回 null 不等于「这个方向走不通」，只代表「这个方向没有跳点」**——目标不在该方向上时很常见，调用方要自己处理 null。
6. 本实现用 `octile` 距离作为 `g(y) = g(x) + dist(x,y)`，前提是 **跳点与当前节点共线**；这一点由 `jump` 的构造保证。

## 参考资料

实际读过并逐条对照的原文：

- Daniel Harabor, Alban Grastien, *Online Graph Pruning for Pathfinding on Grid Maps*, AAAI 2011（全文 6 页）— §Neighbour Pruning Rules 的式 (1)(2)、Definition 1/2/3、Algorithm 1/2、Theorem 1 的最优性论证、Figure 1–3
- 同文 Related Work 部分对 HPA\*、Swamps、dead-end heuristic、fast expansion 的定位（用于说明 JPS 是「在线 + 无预处理 + 保最优」）
