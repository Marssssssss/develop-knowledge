# 图数据库

> 本目录为类目拓展占位（2026-09-12 巡检自动新建），尚未产生 demo。
> 后续轮次会在此目录下按主题生成 demo。

## 子领域与待研究清单

按 Neo4j 官方「property graph model」与 Cypher 参考卡分类，下面 6 个方向覆盖图数据库的核心知识点；每条都对应一个未来 demo（至少含 1-2 种主流实现）。

| 主题 | 核心要点 | 推荐 demo 切入点 |
| --- | --- | --- |
| **属性图模型（Property Graph）** | 节点 / 关系 / 标签 / 属性 四元组；关系有方向但查询时可忽略；节点可有多个 label | `PropertyGraphModel/` —— 用 hash map + 邻接表手写最简图结构 |
| **Cypher 模式匹配** | `(n:Person)-[:KNOWS]->(m:Person)` ASCII-art 模式；MATCH / OPTIONAL MATCH / WHERE / RETURN 流水线 | `CypherParser/` —— 词法 + 递归下降解析最少子集 |
| **图遍历与最短路径（BFS / DFS / Dijkstra）** | 邻接表 + 队列/栈；Dijkstra 在加权边上 | `GraphTraversal/` —— BFS + DFS + Dijkstra + A*（与 `01-游戏开发/A*` 区别：无启发目标） |
| **PageRank / Louvain 社区发现** | PageRank 迭代收敛（α = 0.85 阻尼）；Louvain 模块度优化 | `PageRank/` / `Louvain/` —— 幂迭代至差值 < 1e-6 |
| **Neo4j 存储层（固定记录 + 双向链表）** | 节点 / 关系均存为定长记录 + first-next/second-prev 双向链表；适合遍历但写放大 | `Neo4jStorage/` —— 演示节点与关系的记录格式与链表遍历 |
| **GQL 国际标准（ISO/IEC 39075:2024）** | GQL 是图查询首个 ISO 标准；Cypher 是其最大输入流派；PGQL / GQL-core 等 | `GQL/` —— 标准架构与多语言 binding 概述（无需 demo，仅索引） |

## 关键概念速览（依据 Neo4j 官方文档）

- **节点（Node）**：域内实体的离散对象，可有零或多个 label（用于分类），可有零或多个属性（key-value）。
- **关系（Relationship）**：连接 source 与 target 节点，必须有方向、必须恰好一个 type、可有零或多个属性。方向在查询时可被忽略。
- **标签（Label）**：节点分组标签（例：`Person`、`Actor`），无层级无继承。
- **Cypher**：声明式查询语言，模式匹配风格 `(:Person {name:'Tom'})-[:ACTED_IN]->(:Movie)`。
- **属性图模型（PGM）**：Neo4j 与 GQL 标准采用的图数据模型，由节点/关系/标签/属性构成。

## 参考资料（实际阅读过的权威来源）

- [Graph database concepts - Neo4j Getting Started](https://neo4j.com/docs/getting-started/current/graphdb-concepts/) — 属性图模型 / 节点 / 关系 / 标签 / 属性 定义
- [Neo4j Cypher Refcard 3.0](https://neo4j.com/docs/cypher-refcard/3.0) — Cypher 语句完整语法卡片
- [Neo4j Cheat Sheet & Quick Reference](https://dev.neo4j.com/neo4j_cheatsheet) — MATCH / WHERE / RETURN / WITH / UNION 用法速查
- [Querying with Cypher - Neo4j Developer Guides](https://gh10950307176.development.neo4j.dev/developer/cypher/querying) — MATCH / RETURN / 别名 子句示例

## 已完成 demo

（暂无）

## 待研究（细化）

- [ ] 属性图模型的最小可读 demo（C / Python / Go）
- [ ] Cypher 子集解析器：MATCH/RETURN/WHERE 三子句
- [ ] 图遍历：BFS / DFS / Dijkstra 对比
- [ ] PageRank 幂迭代实现与收敛阈值讨论
- [ ] Neo4j 存储层定长记录与链表遍历
- [ ] GQL 标准概览（ISO/IEC 39075:2024）