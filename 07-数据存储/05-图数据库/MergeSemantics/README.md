# Cypher MERGE：find-or-create 语义、ON CREATE/ON MATCH 与并发锁保证

## 简介

`MERGE` = `MATCH` + `CREATE`：模式**整体原子匹配**——找到就绑定（MATCH 语义），找不到才整体创建（CREATE 语义）。本 demo（Python + Go 双版本）实现其完整语义内核并复现官方文档的关键场景：**全属性精确匹配、查询内绑定复用、无向 MERGE 双向匹配、ON CREATE/ON MATCH、并发 MERGE 的排他锁 + 二次 MATCH 串行化保证**。

## 原理详解

### 1. 模式整体匹配：属性集必须完全一致

`MERGE (michael:Person {name: 'Michael Douglas'})` 只有在存在**属性集完全相同**的节点时才绑定。官方例子：库里已有 `{name, bornIn, chauffeurName}` 三属性的 Charlie Sheen，执行 `MERGE (:Person {name: 'Charlie Sheen', bornIn: 'New York', chauffeurName: 'Michael Black'})` 会**创建第二个"相似"节点**——MERGE 的匹配是精确相等而非"键相同"。**官方建议：MERGE 前先建唯一约束/键约束**——有约束时同样这条语句会直接报 `index entry conflict`（22N80/22N79）而不是静默创建。

### 2. 查询内绑定复用（Location 例子）

`MATCH (person:Person) MERGE (location:Location {name: person.bornIn})`：多个 `bornIn: 'New York'` 的人**只创建一个** Location 节点——先前 MERGE 已创建/绑定的节点对后续行**复用绑定**。对比同一查询里 `MERGE (person)-[:HAS_CHAUFFEUR]->(chauffeur:Chauffeur {name: person.chauffeurName})`（模式含关系的整体 MERGE）：匹配不到就整体创建，两个同名的 John Brown 会出现**两个** Chauffeur 节点。

### 3. 无向 MERGE 与 ON CREATE/ON MATCH

- **无向关系 MERGE** `(a)-[r:KNOWS]-(b)`：先按**两个方向**匹配，都匹配不到才**从左到右**创建。
- `ON CREATE SET`（本次创建了模式时执行）/ `ON MATCH SET`（绑定到已有模式时执行），两者可并用。

### 4. 并发 MERGE 关系：排他锁 + 二次 MATCH（本 demo 核心）

官方 "Concurrent relationship merges"：顺序执行时 MERGE 第一次创建、后续匹配；**并发执行时 Neo4j 保留这一行为**——首次 MATCH 失败后**对两端节点取排他锁**，随后**再次 MATCH**（吸收初次失败与取锁之间的竞态），仍无匹配才创建。这个串行化保证**不依赖唯一约束**——Cypher 根本没有"限制两节点间某类型关系数量"的约束，保证完全由锁行为提供。

### 5. MERGE 与 CREATE 的差异

MERGE 不接受 map 参数整体作属性表（`CREATE (n:Person $param)` 可以），每个属性必须显式写出——属性集就是匹配键。

## 环境

- Python 3.8+ / Go 1.18+，无第三方依赖。

## 运行方式

```bash
python merge.py
go run merge.go
```

按用户约定不实际运行，逻辑经人工代码审查。

## 关键代码

```python
# 并发 MERGE 关系的官方三步:
first = find_rel(...)            # 1. 首次 MATCH(无锁快路径)
if first is None:
    lock.acquire()               # 2. 两端节点排他锁
    second = find_rel(...)       # 3. 锁后二次 MATCH: 吸收竞态
    if second is None:
        create_rel(...)          #    仍无才创建
```

## 性能边界

| 项 | 边界 |
| --- | --- |
| 快路径 | 首次 MATCH 命中即返回，不加锁 |
| 慢路径（未命中） | 排他锁 + 二次 MATCH + 创建，锁持有到事务结束 |
| 无约束并发 | 正确但吞吐受锁串行化限制；有唯一约束时走索引更快 |
| 模式复杂度 | MERGE 整个模式（含关系）时任何一部分不匹配都整体创建 |

## 注意事项与常见坑

1. **MERGE ≠ upsert 的"按业务键合并"**：匹配键 = 整个属性集；多一个属性就是"不存在"→ 重复节点。**先建唯一约束**是官方第一建议。
2. **MERGE 保证存在性，不保证唯一性**：并发/属性不一致场景下的唯一性靠约束；无约束的关系 MERGE 靠锁串行化。
3. **部分绑定的陷阱**：模式中已绑定的变量（前序 MATCH/MERGE 绑定）不参与创建，但未绑定部分整体创建——HAS_CHAUFFEUR 例子中每人各得一个新 Chauffeur。
4. **ON CREATE 只对"本次创建的部分"生效**，绑定的既有节点不触发。
5. **先 MERGE 绑定再 MERGE 关系**（两步）与**一步 MERGE 整个模式**结果不同——前者复用节点，后者可能创建重复节点。

## 参考资料（实际读过）

1. Neo4j Cypher Manual — MERGE（find-or-create/ON CREATE/ON MATCH/无向/约束/并发锁，本 README 主要依据）: https://neo4j.com/docs/cypher-manual/current/clauses/merge/
2. Neo4j Cypher Manual — MATCH（模式匹配与绑定语义对照）: https://neo4j.com/docs/cypher-manual/current/clauses/match/
