# 量化路径模式（Quantified Path Patterns）：`((a)-[r:NEXT]->(b)){1,3}` 的展开、组变量与剪枝

## 简介

Cypher 的可变长度路径匹配把"重复出现的路径片段"提取出来加**量词**，一个查询替代原来需要 `UNION` 拼接的多个固定长度 `MATCH`。本 demo 实现其最小语义内核：**量词展开（并集语义）、组变量（group variables）收集、关系唯一性、内联谓词（inline WHERE）剪枝**，并复现官方文档"路径爆炸"警告与缓解策略。

## 原理详解

### 1. 量词展开 = 固定长度模式的并集

官方文档（Patterns → Variable-length paths）：量化路径模式 `((:Stop)-[:NEXT]->(:Stop)){1,3}` 在概念上展开为：

```text
(:Stop)-[:NEXT]->(:Stop)
| (:Stop)-[:NEXT]->(:Stop)-[:NEXT]->(:Stop)
| (:Stop)-[:NEXT]->(:Stop)-[:NEXT]->(:Stop)-[:NEXT]->(:Stop)
```

关键规则：**展开式中相邻两个节点模式必须匹配同一节点**（前段终点 = 后段起点），可合并为单个节点模式，过滤条件合取连接。

### 2. 组变量：量词内的变量在外部是"列表"

在量化模式**内部**声明的变量（如 `((l)-[r:NEXT]->(m)){1,3}` 里的 `l/r/m`）称为**组变量**——在外部引用时绑定为**列表**，因为一条匹配路径中它被绑定 1..n 次。官方绑定示例：

| origin（单例） | l | r | m |
| --- | --- | --- | --- |
| n2 | `[n2,n3,n4]` | `[r2,r3,r4]` | `[n3,n4,n5]` |

注意 `m` 不含起点节点——收集从第二段开始。组变量天然兼容 `reduce()` 等列表函数（官方示例用 `reduce` 累加每段 `distance`）。

### 3. 关系唯一性：同一模式内关系只匹配一次

MATCH 文档明确："By default, Cypher will only match a relationship once inside a single pattern"（新版本可用 `REPEATABLE ELEMENTS` 放开）。**这是语义正确性的关键**：图里存在环（如 `x->dk`）时，若无唯一性约束，可变长度展开会无限自我复制路径。唯一性保证：一条路径内每条关系至多遍历一次 → 有环图中匹配数仍然有限（路径长度 ≤ 关系总数）。

### 4. 路径爆炸与四种官方缓解策略

官方警告原文要点：量化模式可能匹配**非常大量的路径**，"随着图规模增长，执行时间将**呈指数级增长**"。四种缓解：

1. **设置有限上界**（如 `{,10}`）；
2. **让模式更具体**（加标签、指定方向）；
3. **内联谓词**：遍历时即时剪枝（`((a)-[:LINK]-(b) WHERE point.distance(a.loc, ndl.loc) > point.distance(b.loc, ndl.loc))+`——每一步必须更接近终点，全英铁路网也只剩 1 条路径）；
4. **`allReduce` 累计值上界**：累计距离超阈值立即剪枝。

内联谓词与"先枚举全部再 WHERE 过滤"有本质区别：**剪掉的是整棵子树**，复杂度从指数级降到实际常数级。

### 5. 与旧 `*` 语法的关系

旧版 `-[REL*1..3]->` 仍可用但**不符合 GQL 标准**；量化关系简写 `-[REL]->{n,m}` 的量词**作用域仅为关系模式**，不覆盖紧邻的节点模式；旧语法不允许 WHERE、类型表达式仅限析取。

## 环境

- Python 3.8+ / Go 1.18+，无第三方依赖。

## 运行方式

```bash
python varlength.py
go run varlength.go
```

两个版本输出相同的 5 组验证（按用户约定不实际运行，逻辑经人工代码审查）。

## 关键代码

```python
used = set()                       # 关系唯一性
if rid in used: continue           # 一条路径内关系不得重复遍历
if inline_pred and not inline_pred(rid, props, nxt):
    continue                       # 内联谓词: 遍历时剪掉整棵子树
used.add(rid); path_rels.append(rid)
dfs(nxt, depth + 1)                # depth ∈ [lo, hi] 时产出绑定
```

## 性能边界

| 场景 | 复杂度 |
| --- | --- |
| 无环 + 无剪枝 | 路径数 = O(分支数^hi)，**指数级** |
| 有环 + 关系唯一性 | 路径数有限（≤ 长度为 hi 的简单路径数），但仍可能指数级 |
| 内联谓词强剪枝 | 实际探索路径数可降为常数（官方地理剪枝示例） |

## 注意事项与常见坑

1. **`{,10}` 与 `{1,10}`**：上界单独使用是合法简写；省略上界 = 无界，触发官方指数爆炸警告。
2. **组变量 ≠ 单例变量**：量化外的变量至多绑定一次；内部变量是列表，两者混用会得到列表/标量类型错误。
3. **关系唯一性默认开启**：需要可重复遍历时（如公交换乘允许往返）用新语法 `REPEATABLE ELEMENTS`（Cypher 25 / Neo4j 2025.06+）。
4. **先枚举再 LIMIT 不等于剪枝**：`ORDER BY distance LIMIT 1` 仍需枚举全部路径；内联谓词才能避免枚举。

## 参考资料（实际读过）

1. Neo4j Cypher Manual — Patterns（含可变长度路径章节索引）: https://neo4j.com/docs/cypher-manual/current/patterns/
2. Neo4j Cypher Manual — Variable-length paths（量词/组变量/剪枝策略，本 README 主要依据）: https://neo4j.com/docs/cypher-manual/current/patterns/variable-length-paths/
3. Neo4j Cypher Manual — MATCH（关系唯一性与 REPEATABLE ELEMENTS）: https://neo4j.com/docs/cypher-manual/current/clauses/match/
