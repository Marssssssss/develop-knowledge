# Cypher 执行计划：火山模型算子树（Volcano/迭代器模型）

## 简介

一条 Cypher 查询的生命周期是：**声明式查询 → 解析 + planner（逻辑计划）→ 物理计划 → runtime 执行**。执行计划是一棵**算子二叉树**：根是 `ProduceResults`，数据从叶算子**自底向上**流。本 demo 用 Python + Go 双版本实现火山模型内核：**拉取式迭代器（open/next）**、四类算子（叶扫描 / Expand / 二元 Apply 与 Join / Eager 化），以及官方的 `EXPLAIN` vs `PROFILE` 语义与计划树展示约定。

## 原理详解

### 1. 算子树与数据流向

- 计划表格的**最顶行是根算子 `ProduceResults`**，最底行是叶算子；数据自底向上流。
- 每个算子有 **0/1/2 个子算子**：0 = 叶（`AllNodesScan`/`NodeByLabelScan`/`NodeIndexSeek`/`Argument`），1 = 一元（`Filter`/`Expand`/`Projection`/`Limit`/`Sort`），2 = 二元。
- 展示约定：二元算子的 **RHS（右侧输入）先显示且缩进更深**，LHS 与算子本身同缩进。

### 2. 二元算子的两种执行模式（官方核心区分）

| 类型 | 执行方式 | 例子 |
| --- | --- | --- |
| **Apply 风格** | LHS 每产出**一行**，RHS 就带着该行的变量绑定**重新执行一次**（嵌套循环） | `Apply`、`Anti`（`NOT EXISTS`：RHS 0 行才输出 LHS 行） |
| **Join 风格** | 两侧都是**完整输入**，由算子组合两侧结果 | `NodeHashJoin`、`CartesianProduct` |

`Argument` 是 Apply 的专用叶算子：**只透传已绑定变量，不做计算**。

### 3. 惰性 vs Eager

官方："查询求值一般是**惰性**的——大多数算子一产出就立即推给父算子"。但**聚合与排序类是 Eager 算子**：必须收完全部输入才能产出第一行，整个上游输出被物化进缓冲，**内存代价 O(行数)**；Eager 化的原因（如读写触及同一数据的正确性）显示在 Details 列。

### 4. Expand 与无索引邻接

`Expand(All)` 对每行已绑定节点沿邻接表扩展一跳——图数据库的性能根基：**只读该节点的邻接表，代价 O(度数)，与全图大小无关**（对比关系库 JOIN 的 O(表大小) 或索引查找）。`Expand(Into)` 则在两点均已绑定时检查两点间关系是否存在。

### 5. Estimated Rows、DB Hits 与 PROFILE

- `EXPLAIN` 只出计划给**估算行数**；`PROFILE` 实际执行，给出真实 `Rows`、`DB Hits`（存储层访问抽象单位：实体读/属性读/索引项读……）、每算子峰值内存、Page Cache 命中。
- **估算来自索引/标签统计与选择性模型，无运行时反馈，可能是错的**——它用于计划选择（`Planner COST`），应视为提示；估算与实测偏差大 → 统计过期或该加索引。
- `Rows ≠ DB Hits`：一行匹配可能触发多次存储访问；Time 列按 pipeline 而非算子计（pipelined runtime 融合算子共享时间）。

## 环境

- Python 3.8+ / Go 1.18+，无第三方依赖。

## 运行方式

```bash
python pipeline.py            # 算子库在同目录 operators.py
go run pipeline.go pipeline_ops.go
```

按用户约定不实际运行，逻辑经人工代码审查。

## 关键代码

```python
class CartesianProduct(Operator):      # Join 风格: 两侧完整输入
    def next(self):
        rr = next(self.right_it, None) # 当前左行 × 逐右行
        ...
class Apply(Operator):                 # Apply 风格: 每左行重启 RHS
    def next(self):
        lr = self.left.next()
        rhs = self.right_factory(dict(lr))  # 带左行绑定重建 RHS
        rhs.open(self.ctx)
```

## 性能边界

| 项 | 边界 |
| --- | --- |
| 惰性流水线 | 首行延迟 O(1)（不缓冲）；内存 O(1) |
| Eager/Sort/聚合 | 必须全量缓冲，内存 O(行数)，大结果集是 OOM 风险点 |
| Apply 嵌套循环 | RHS 执行次数 = LHS 行数，LHS 大时须换 Join 类计划 |
| CartesianProduct | 行数 = 左行数 × 右行数，planner 尽量避免 |

## 注意事项与常见坑

1. **读计划自底向上**：表格最底行才是执行的起点（官方文档特别强调）。
2. **Estimated Rows 可能严重失真**：统计过期时 cost-based planner 会选错计划——对比 PROFILE 实测是调优第一步。
3. **PROFILE 会真的执行**（可能写库、消耗更多资源），只应在主动调优时用。
4. **Memory 列非累计**：两个算子各报 100MB 不代表用了 200MB（各算子峰值独立计）。
5. **Eager 不是缺陷而是正确性需要**：读写同一数据时必须 Eager 化防止看到自己未提交的中间态。

## 参考资料（实际读过）

1. Neo4j Cypher Manual — Execution plans（算子树/二元算子两类/惰性 vs Eager/EXPLAIN vs PROFILE，本 README 主要依据）: https://neo4j.com/docs/cypher-manual/current/execution-plans/
2. Neo4j Cypher Manual — MATCH（查询生命周期与子句组合）: https://neo4j.com/docs/cypher-manual/current/clauses/match/
