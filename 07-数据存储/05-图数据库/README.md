# 图数据库

> 本目录最初为类目拓展占位（2026-09-12 巡检自动新建），现已产出 **三批 15 个 demo**。
> 第一批（属性图模型 / Cypher 解析 / 图遍历 / PageRank / 存储层）、第二批（量化路径 / Bolt / 并发控制 / 执行计划 / MERGE）、
> **第三批（2026-09-20 16:00 槽，ID 467-471）**：路径匹配模式 / null 三值逻辑 / 搜索性能索引 / 约束 / Louvain 模块度。

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

- [x] 属性图模型(LPG: 节点/关系/标签/属性, 双向链表出/入链) — 见 [PropertyGraphModel/](./PropertyGraphModel/)（Python / Go）
- [x] Cypher 只读子集(MATCH/WHERE/RETURN/ORDER BY/SKIP/LIMIT)递归下降解析器 — 见 [CypherParser/](./CypherParser/)（Python / Go）
- [x] 图遍历 BFS / DFS / Dijkstra(邻接表 + 队列/栈/堆) — 见 [GraphTraversal/](./GraphTraversal/)（C / Python / Go）
- [x] PageRank 幂迭代(d=0.85 阻尼 + 悬挂节点贡献) — 见 [PageRank/](./PageRank/)（Python / Go）
- [x] Neo4j 存储层(15 B NodeRecord + 34 B RelRecord + 双向链表) — 见 [Neo4jStorage/](./Neo4jStorage/)（C / Python）
- [x] 量化路径模式({min,max} 展开为并集 + 组变量列表绑定 + 关系唯一性 + 内联谓词剪枝) — 见 [VarLengthPath/](./VarLengthPath/)（Python / Go）
- [x] Bolt 协议(握手 60 60 B0 17 + 分块传输 00 00 边界 + PackStream marker 编码) — 见 [BoltProtocol/](./BoltProtocol/)（Python / Go）
- [x] Neo4j 并发控制(READ_COMMITTED 丢失更新 + 实体锁管理器 + 等待图死锁检测) — 见 [Neo4jLocks/](./Neo4jLocks/)（Python / Go）
- [x] Cypher 执行计划火山模型(算子树 open/next + Apply/Join 二元 + 惰性 vs Eager) — 见 [CypherPipeline/](./CypherPipeline/)（Python / Go）
- [x] MERGE 语义(find-or-create 全属性精确匹配 + ON CREATE/ON MATCH + 并发排他锁+二次 MATCH) — 见 [MergeSemantics/](./MergeSemantics/)（Python / Go）
- [x] Cypher 路径匹配模式(默认关系唯一性 TRAIL / REPEATABLE ELEMENTS=WALK / ACYCLIC；七桥图 2·48·0 与路由器网 80 条 + 十项占比) — 见 [PathMatchModes/](./PathMatchModes/)（Python 87 断言 / Go）
- [x] Cypher null 与三值逻辑(官方 9 行真值表、AND/OR 吸收 vs XOR 无吸收、IN 八例、`IS ::` 对 null 恒 true、排序 null 位置) — 见 [CypherNullLogic/](./CypherNullLogic/)（Python 100 断言 / Go）
- [x] 搜索性能索引(Range/Text/Point/Token 四类谓词可解性、trigram 索引、planner 选择与 USING、复合索引收录) — 见 [SearchIndexes/](./SearchIndexes/)（Python 50 断言 / Go）
- [x] Neo4j 约束(唯一性/存在性/类型/Key，Key=存在性+唯一性，`LIST<STRING NOT NULL>` 与联合类型) — 见 [SchemaConstraints/](./SchemaConstraints/)（Python 49 断言 / Go）
- [x] Louvain 与模块度(式(1) Q、式(2) ΔQ、两阶段聚合、自环 `A_ii=2s` 的聚合不变性、ring of 30 cliques 30→15) — 见 [LouvainModularity/](./LouvainModularity/)（Python 48 断言 / Go）

## 待研究（细化）

- [x] 属性图模型的最小可读 demo（C / Python / Go）
- [x] Cypher 子集解析器：MATCH/RETURN/WHERE 三子句
- [x] 图遍历：BFS / DFS / Dijkstra 对比
- [x] PageRank 幂迭代实现与收敛阈值讨论
- [x] Neo4j 存储层定长记录与链表遍历
- [x] 量化路径模式与可变长度遍历（2026-09-15 第二批）
- [x] Bolt 二进制协议三层（握手/分块/PackStream）（2026-09-15 第二批）
- [x] 并发控制：隔离级别/锁/死锁检测（2026-09-15 第二批）
- [x] 执行计划与火山模型算子树（2026-09-15 第二批）
- [x] MERGE 语义与并发（2026-09-15 第二批）
- [x] Cypher 路径匹配模式与 match mode（2026-09-20 第三批）
- [x] Cypher null 与三值逻辑（2026-09-20 第三批）
- [x] Neo4j 搜索性能索引与 planner 选择（2026-09-20 第三批）
- [x] Neo4j 约束四类与索引支撑约束（2026-09-20 第三批）
- [x] Louvain 社区发现（模块度优化）（2026-09-20 第三批）
- [ ] GQL 标准概览（ISO/IEC 39075:2024）
- [ ] 全文索引与向量索引（semantic indexes）
- [ ] 因果集群与 Raft（bookmarks 因果一致性）
- [ ] 图算法：WCC / 标签传播 / 节点相似度
- [ ] Graph type 与 schema 定义