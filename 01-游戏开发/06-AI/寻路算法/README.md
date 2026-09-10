# 寻路算法

## 经典算法

| 算法 | 完备 | 最优 | 适用 |
| --- | --- | --- | --- |
| BFS | 是 | 最短边数 | 均匀网格 |
| Dijkstra | 是 | 是 | 正权图 |
| A* | 否（启发式可采纳时是） | 是 | 网格/图（实用王者） |
| JPS（Jump Point Search） | 是 | 是 | 网格 A* 加速 |
| NavMesh | — | 是 | 任意多边形 |

## 已完成 demo

| demo | 知识点 | 语言 |
| --- | --- | --- |
| [A-star/](./A-star/) | A* 启发式搜索（f=g+h、可采纳性、Dijkstra/A*/Greedy 三模式对比） | C / Python / Go |

## 待研究

- [x] A* 算法最小实现（见 A-star/）
- [ ] JPS 加速原理
- [ ] NavMesh 烘焙思路