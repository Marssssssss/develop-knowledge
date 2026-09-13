# 属性图模型(Neo4j Property Graph)最小实现

## 简介

属性图模型（Property Graph Model, LPG）是 Neo4j 等图数据库采用的核心数据模型，由四种元素构成：
- **节点（Node）**：实体离散对象，可有零或多个 **标签（Label）** 来分类，可有零或多个 **属性（key-value）**。
- **关系（Relationship）**：连接两个节点，必须有方向、必须恰好一个 **类型（Type）**、可有零或多个属性。
- **标签（Label）**：节点分组标签，无层级无继承。
- **属性（Property）**：附加在节点或关系上的任意 key-value。

> 关系有方向，但查询时可被忽略（Neo4j 官方文档原话："Relationships always have a direction. However, the direction can be disregarded where it is not useful"）。

本 demo 用 Python + Go 双版本复现"邻接表 + 按 (类型, 方向) 分桶的双向链表"，演示电影图（Tom Hanks、Forrest Gump、Sally Field、Apollo 13）。

## 原理详解

### 1. 节点（Node）
```
Node {
    nid:        int
    labels:     set[str]            # 例如 {Person, Actor}
    props:      dict                # 任意 key-value
    first_rel_out: dict[type, rid]  # 出向链表头(按 type 分桶)
    first_rel_in : dict[type, rid]  # 入向链表头
}
```

### 2. 关系（Relationship）
```
Relationship {
    rid:        int
    type:       str                 # 例如 "ACTED_IN"
    src_id:     int                 # 起始节点
    dst_id:     int                 # 终止节点
    props:      dict
    # src 上的双向链表(outgoing chain)
    next_out:   Optional[int]
    prev_out:   Optional[int]
    # dst 上的双向链表(incoming chain)
    next_in:    Optional[int]
    prev_in:    Optional[int]
}
```

### 3. 创建关系时挂链表头
`create_rel(src, dst, type, ...)` 时：
- 在 src 上把 `first_rel_out[type]` 指向新 rid，新 rid 的 `next_out` 指向旧 rid，旧 rid 的 `prev_out` 更新为新 rid。
- 在 dst 上同理操作 `first_rel_in[type]` 与 `next_in`/`prev_in`。

### 4. 遍历邻居
`neighbors_out(nid, type_)` 从 `n.first_rel_out[type_]` 出发，沿 `next_out` 走完链表，返回 `[(dst_id, rid), ...]`。
- 时间复杂度：O(d)，d 为该类型上的度。
- 这是 Neo4j "无索引邻接"（index-free adjacency）特性的微观体现：不需要全局索引，只跟随定长记录上的指针即可。

## 对比

| 实现 | 数据结构 | 空间 | 查询 | 删除 |
|---|---|---|---|---|
| 本 demo（双向链表） | 邻接表 + 双向链表 | O(V+E) | O(d) | O(1) 重链 |
| CSR（压缩稀疏行） | 整型数组 offset/adj | O(V+E) | O(log V) + 二分 | 难 |
| 邻接矩阵 | 二维 bool/int | O(V²) | O(1) 是否邻接 | O(V) |

## 环境准备

- Python ≥ 3.8
- Go ≥ 1.21

## 运行方式

```bash
# Python
cd python && python property_graph_demo.py

# Go
cd go && go run property_graph_demo.go
```

## 关键代码片段

`PropertyGraph.create_rel` —— 创建关系并挂双向链表：

```python
old_head_out = src.first_rel_out.get(type_)
old_head_in  = dst.first_rel_in.get(type_)
r = Relationship(
    rid, type, src_id, dst_id, props,
    next_out=old_head_out, prev_out=None,
    next_in=old_head_in,  prev_in=None,
)
# 更新旧头节点的 prev
if old_head_out is not None:
    self.rels[old_head_out].prev_out = rid
if old_head_in is not None:
    self.rels[old_head_in].prev_in = rid
src.first_rel_out[type] = rid
dst.first_rel_in[type]  = rid
```

## 性能与边界

- 空间复杂度：O(V + E) 节点/关系记录。
- 邻接查询：O(d) d 为该节点某类型上的度（与全图大小无关，这是 Neo4j "无索引邻接" 的关键）。
- 写入放大：创建 1 条关系需要更新 4 个指针（prev_out / next_out / prev_in / next_in）+ 2 个 head；因此 Neo4j 文档指出"the joins are done on creation"。

## 注意事项与常见坑

- **方向可忽略**：本 demo 把出向/入向链表分开存，查询反向需用 `neighbors_in_iter(nid, type_)`。
- **关系类型必须恰好 1 个**：Neo4j 官方"a relationship must have exactly one relationship type"，不要尝试给关系多个 type。
- **删除关系时双向链表 relink**：要更新 prev/next 4 个指针 + 2 个 head，遗漏任一会导致遍历陷入环或跳过节点。
- **节点可多 label**：本 demo 用 `set[str]` 存；Neo4j 真实实现用位图(bitset)索引每 label 的节点列表。

## 参考资料

- [Neo4j Getting Started - Graph database concepts](https://neo4j.com/docs/getting-started/current/graphdb-concepts/) — 属性图模型 / 节点 / 关系 / 标签 / 属性定义
- [Neo4j Developer Guides - What is a Graph Database](https://neo4j.com/developer/graph-database) — LPG 模型与无索引邻接特性的关系
- [Neo4j Developer Guides - Graph Data Modeling](https://neo4j.com/docs/getting-started/data-modeling/guide-data-modeling/) — 白板友好模型与建模过程