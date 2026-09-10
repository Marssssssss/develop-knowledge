# A* 寻路算法

## 简介

A*（A-star）是 1968 年由斯坦福研究院（SRI）的 Peter Hart、Nils Nilsson、Bertram Raphael 在 Shakey 机器人的路径规划项目中提出的**启发式最优图搜索算法**。它将 Dijkstra 算法"保证最优"与贪心最佳优先搜索"朝目标探索"两种性质结合为一个评价函数：

- **f(n) = g(n) + h(n)**：g 为起点到 n 的实际代价，h 为 n 到目标的启发式估计，优先扩展 f 最小的节点。
- **可采纳性（admissibility）**：h 永不高估真实剩余代价（h(n) ≤ h*(n)）时，A* 保证找到最优路径。
- **一致性（consistency / monotonicity）**：对每条边满足 h(n) ≤ cost(n,n') + h(n')，此时节点首次出队即得最优距离，无需重开。
- **最优效率**：Dechter & Pearl（1985）证明使用一致启发式时，没有其他可采纳算法扩展的节点比 A* 更少。
- **两个极端**：h ≡ 0 时 A* 退化为 Dijkstra；只看 h 不看 g 则退化为 Greedy Best-First（快但不保证最优）。

历史：Nilsson 最初想用只看 h 的 Graph Traverser，Raphael 建议用 g + h，Hart 提出并证明了可采纳性/一致性概念（原始论文 *A Formal Basis for the Heuristic Determination of Minimum Cost Paths*, IEEE Trans. SSC, 1968）。

## 原理详解

工作机制（与 Dijkstra 唯一区别是优先队列的键）：

1. 将起点加入优先队列（open set），g(start)=0，优先级 f = h(start)。
2. 取出 f 最小的节点 current；若 current 即目标，结束（h(goal)=0，此时 f = 最短路长）。
3. 对 current 的每个邻居 next，计算 new_cost = g(current) + cost(current, next)。
4. 若 next 未访问过或 new_cost < g(next)：更新 g(next)、came_from(next)=current，以 new_cost + h(next) 为优先级入队（或降低其键）。
5. 回到第 2 步；队列为空则无解。
6. 从 goal 沿 came_from 回溯重建路径。

```
                 ┌──────────────────────────────────┐
   frontier      │  priority = f(n) = g(n) + h(n)   │
   (open set) ──▶│  取 f 最小者 u                    │
                 └────────┬─────────────────────────┘
                          │ u == goal ? ── 是 ──▶ 回溯 came_from
                          ▼ 否
              遍历邻居 v：tentative_g = g[u] + w(u,v)
              若改善：g[v]=tentative_g, came[v]=u,
                     push(v, tentative_g + h(v))
```

核心数据结构与 API（以 Python 参考实现为例）：

| 组件 | 说明 |
| --- | --- |
| `heuristic(a, b)` | 网格 4 邻接用曼哈顿距离 `abs(dx)+abs(dy)`；8 邻接对角代价 √2 时用 octile `max+ (√2−1)·min`；任意方向用欧氏距离 |
| `frontier` / `heapq` | 最小堆，返回 f 最低者；Red Blob 指出 C++ 需配置 `std::priority_queue` 为 min-heap |
| `cost_so_far` | 即 g 表；Red Blob 实现要点：**不需要 decrease-key**，直接插入重复条目，出队时检查是否已定稿即可 |
| `came_from` | 父指针表，用于回溯路径 |

权重网格代价：本 demo 模拟 Red Blob 的"森林地图"——普通格代价 1、森林格代价 5、墙不可通行，展示 A* 在**带权图**上绕开高代价区域。

## 对比 / 选型

| 算法 | 优先队列键 | 最优性 | 探索范围 | 适用 |
| --- | --- | --- | --- | --- |
| BFS | 入队序 | 最短边数（等权时最优） | 全方向 | 等权图、flow field |
| Dijkstra | g | 是 | 全方向均匀 | 多目标 / 到所有点 |
| Greedy Best-First | h | **否** | 朝目标，障碍多时路径差 | 快糙 |
| A* | g + h | h 可采纳时是 | 朝目标且带最优保证 | 单目标（游戏寻路默认选择） |

h 越接近真实代价 A* 越快；h 越小越接近 Dijkstra。

## 环境准备

- 操作系统：任意（demo 仅标准库 + 控制台输出）
- 语言版本：C（C99）/ Python 3.8+ / Go 1.21+
- 依赖：无

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -o astar astar.c
./astar
```

### Python

```bash
python3 astar.py
```

### Go

```bash
go run astar.go
```

三个版本均输出：Dijkstra / Greedy / A* 三种搜索的代价、扩展节点数对比，以及 A* 路径的 ASCII 可视化（`*` = 路径，`#` = 墙，`f` = 森林）。

## 关键代码片段

Python 版核心循环（对应原理详解第 2-4 步）：

```python
def search(grid, start, goal, mode):
    # mode: 0=Dijkstra(只看 g) 1=A*(g+h) 2=Greedy(只看 h)
    frontier = [(0, start)]                 # 最小堆 (priority, loc)
    came_from = {start: None}
    cost_so_far = {start: 0}                 # g 值表
    expanded = 0
    while frontier:
        _, current = heapq.heappop(frontier) # 2. 取 f 最小者
        if current == goal:                  #    early exit
            break
        expanded += 1
        for nxt in neighbors(grid, current): # 3. 松弛邻居
            new_cost = cost_so_far[current] + grid.cost(current, nxt)
            if nxt not in cost_so_far or new_cost < cost_so_far[nxt]:
                cost_so_far[nxt] = new_cost  # 4. 改善则更新
                pr = new_cost if mode == 0 else (heuristic(goal, nxt) if mode == 2 else new_cost + heuristic(goal, nxt))
                heapq.heappush(frontier, (pr, nxt))
                came_from[nxt] = current
    return came_from, cost_so_far, expanded
```

注意此实现**不做 decrease-key**：直接堆入重复节点，出队后通过 `cost_so_far` 判断是否为过期条目（Red Blob Games 实现页的标准做法）。

## 性能与边界

- 时间/空间复杂度：最坏 O(b^d)（b=分支因子，d=解深度），空间同阶——A* 把所有生成节点存在内存，这是其主要实际缺陷（维基百科）。
- 启发式质量决定效率：h 完美时 A* 沿最优路径直奔目标；h=0 退化为 Dijkstra。
- 大地图游戏实践：用 NavMesh 把多边形图喂给同一算法而非逐格；均匀网格可用 JPS（Harabor & Grastien 2011）获得 10-100 倍加速。

## 注意事项与常见坑

- **启发式与移动代价不匹配导致次优**：4 邻接网格用了对角距离会高估、违反可采纳性 → 得到次优路径；8 邻接对角代价 √2 必须用 octile 而非曼哈顿（曼哈顿仍可采纳但偏弱、更慢）。
- **曼哈顿距离在带权格上仍可采纳吗**：曼哈顿只假设单位步代价 1，格代价 ≥1（本 demo 森林=5）时仍不高估，可采纳成立；若存在代价 <1 的格则可能失效。
- **缺少重开（reopen）逻辑**：仅可采纳而不一致的 h 需允许重开已关闭节点，否则不保证最优（本 demo 用"允许重复入队 + g 值改善检查"规避）。
- **浮点优先级 tie-break**：f 相同时的顺序不稳定，跨语言输出路径可能等价但形状不同，属正常现象。

## 参考资料（实际阅读过的权威来源）

- [Introduction to the A* Algorithm — Red Blob Games](https://www.redblobgames.com/pathfinding/a-star/introduction.html) — 全文阅读：BFS/Dijkstra/Greedy/A* 的统一框架、f=g+h、可采纳性、算法选型建议与 Python 参考代码
- [Implementation of A* — Red Blob Games](http://www.redblobgames.com/pathfinding/a-star/implementation.html) — Graph/Location/Queue 抽象、优先队列无需 decrease-key 的实现要点（经搜索快照阅读主要章节）
- [A* search algorithm — Wikipedia (IPFS 镜像)](http://en.wikipedia-on-ipfs.org/wiki/A*_search_algorithm) — Shakey/1968 历史、可采纳性与一致性定义、O(b^d) 复杂度、D* 与 memory-bounded 变体
- [A* Algorithm — Mastering Algorithms](https://masteringalgorithms.com/chapters/a-star) — 1968 原始论文出处、可采纳/一致性证明、曼哈顿/Chebyshev/octile/欧氏四种网格启发式及适用条件
