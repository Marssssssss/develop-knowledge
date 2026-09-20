# Cypher 路径匹配模式：关系唯一性、DIFFERENT RELATIONSHIPS 与 ACYCLIC

> 目录：`07-数据存储/05-图数据库/PathMatchModes/`（Neo4j/Cypher 第三批）
> 实现：Python（`path_modes.py` + `selfcheck_path_modes.py`，87 断言实跑全绿）、Go（`path_modes.go` + `selfcheck_path_modes.go`）

## 1. 简介

Cypher 的图模式匹配不是简单的「沿边随便走」。**同一条关系能否在一条结果里出现两次**、**同一个节点能否重复出现**，由 **match mode / path mode** 决定。这两条规则直接决定了 `MATCH p = (a)-[:X]-*(b)` 会返回多少条路径——差一个模式，结果可以差几十倍，而且**很多"查不到"的诡异结果正是默认的关系唯一性造成的**。

本 demo 把三种约束等级做成可枚举的模型，并用官方文档给出的两张示例图（七桥图、路由器网络）作为**数值基准**，量化三种模式的差异。

## 2. 原理详解

### 2.1 三种约束等级

| 等级 | Cypher 写法 | 约束 |
| --- | --- | --- |
| `WALK` | `MATCH REPEATABLE ELEMENTS` | 无唯一性约束，关系与节点都可重复 |
| `TRAIL` | `MATCH DIFFERENT RELATIONSHIPS`（**默认**） | **关系**在同一条结果里不可重复（与方向无关）；节点可重复 |
| `ACYCLIC` | `MATCH p = ACYCLIC (…)` | 关系不可重复 **且 节点**在同一条路径内不可重复 |

关键点：

- **默认是 TRAIL，不是 WALK**。官方文档原话：*"By default Cypher does not allow the same relationship to be traversed more than once in a given MATCH result, regardless of the direction it is traversed in. The same restriction does not hold for nodes, which may be re-traversed any number of times."*
- `DIFFERENT RELATIONSHIPS` **不改变语义**，只是把默认行为显式写出（`MATCH DIFFERENT RELATIONSHIPS p = (start)--{,2}(end)` 与 `MATCH p = (start)--{,2}(end)` 功能等价）。它自 Neo4j 2025.06 起提供，仅 Cypher 25 可用。
- GQL 的三档路径模式 `WALK` / `TRAIL` / `ACYCLIC` 都能写，但 `TRAIL` 与 `WALK` **不额外改变** `DIFFERENT RELATIONSHIPS` / `REPEATABLE ELEMENTS` 已施加的约束。
- `ACYCLIC` 自 Neo4j 2026.03 起提供（Cypher 25 only）。它禁止的是**路径内**节点重复，**跨路径**的节点重复仍允许，因此 equijoin 依然成立。
- 约束包含关系：`ACYCLIC ⊆ TRAIL ⊆ WALK`。注意 **ACYCLIC 蕴含 TRAIL**——重复一条关系必然把至少一个节点带回路径，所以禁节点重复自然就禁掉了关系重复（模型里仍显式写两条判断以便观察）。

### 2.2 为什么默认是关系唯一：七桥问题

官方用**柯尼斯堡七桥**来解释默认行为：不重复遍历关系 ⇔ 不重复过桥。

- `(:Kneiphof)-[:BRIDGE]->{5}()` → **2 条**，桥序列 `[1,5,6,4,7]` 与 `[6,4,1,5,7]`；
- `(:Kneiphof)--{6}()` → **48 条**；
- `(:Kneiphof)--{7}()` → **0 条**。

第 3 条正是欧拉的结论：7 座桥每座恰好走一次的散步**不存在**。六步之后七座桥已用掉六座，第七步必然要重走某座桥，而 TRAIL 禁止这样做。**把模式放宽到 WALK，7 步立刻有解**——这是关系唯一性最有鉴别力的一个判据。

### 2.3 ACYCLIC 的剪枝价值

ACYCLIC 的收益不只是"结果少"，而是**在遍历过程中剪枝**：

- 旧写法：`MATCH p = … WHERE size(nodes(p)) = COUNT { UNWIND nodes(p) AS n RETURN DISTINCT n }`——引擎先把**所有**含环路径生成出来、驻留内存，再由 `WHERE` 丢弃；
- `MATCH p = ACYCLIC …`：引擎在扩展时就监测当前路径已访问过的节点，**遇到即剪**，无效路径根本不会被完整生成。

### 2.4 变长 `+` 的口径说明

官方路由器示例中，TRAIL 版本要写内联谓词 `(l WHERE l.name <> 'Z')-[:LINK]-(m WHERE m.name <> 'A')` 来排除中途经过起点/终点；而 ACYCLIC 版本**这些谓词是冗余的**（节点不可重复 ⇒ 不可能回到 A，也不可能先到 Z 再离开又回来）。

本模型对变长 `+` 采用「到达终点即产出并停止扩展」。这在 ACYCLIC 下与官方语义**严格等价**；在 TRAIL 下等价于把上述内联谓词写进模式。README 与代码注释都标注了这一口径。

## 3. 对比：三种模式在同一模式串上的结果

七桥图，无向、定长 6：

| 模式 | 路径条数 |
| --- | --- |
| WALK | 2759 |
| TRAIL | 48 |
| ACYCLIC | 0（4 节点图，ACYCLIC 最长只有 3 跳） |

WALK 是 TRAIL 的 **57 倍**——同一句模式串，只差一个 match mode。

路由器网络，`A → Z` 变长 `+`、无向：

| 模式 | 路径条数 |
| --- | --- |
| ACYCLIC | 80 |
| TRAIL（≤8 跳截断） | 78，且**含**官方给出的环路径 `A-B-E-G-J-I-H-J-Z` |
| WALK（≤8 跳截断） | 412，严格多于 TRAIL，且出现重复关系 |

## 4. 环境

- Python 3.8+（仅标准库）；Go 1.18+（仅标准库）。
- 无需 Neo4j 实例：本 demo 是**语义模型**，不连接数据库。

## 5. 运行方式

```bash
cd PathMatchModes
python selfcheck_path_modes.py     # 87 条断言，输出 "ALL OK"
go run .                            # Go 版自检（需把两个 .go 放同一目录）
```

## 6. 关键代码

`allows()` 是整个模型的语义核心，三种模式只差两行判断：

```python
def allows(mode, rel_seen, node_seen, rel, node):
    if mode == WALK:
        return True
    if mode == ACYCLIC:
        # 节点唯一 ⇒ 关系必然唯一（重复关系必然带回重复节点）
        return rel.rid not in rel_seen and node not in node_seen
    if mode == TRAIL:
        return rel.rid not in rel_seen
```

枚举是带回溯的 DFS，`rel_seen` / `node_seen` 在递归进出时成对增删：

```python
rel_seen.add(rel.rid); node_seen.add(nxt)
dfs(nxt, ...)
node_seen.discard(nxt); rel_seen.discard(rel.rid)
```

## 7. 性能边界与注意事项

- **WALK 在变长模式下不会自然终止**：允许重复关系意味着路径可以无限延长（在环上绕圈）。官方只能靠 `{n}` 定长或 `+` 的上界来约束；本模型用 `max_hops` 兜底，默认 64。
- **ACYCLIC 天然有界**：路径最长不超过「节点数 − 1」跳，这是它比 WALK/TRAIL 快得多的根本原因之一。
- **ACYCLIC 也会让「回到起点」的路径全部消失**：如果你的业务语义允许绕回起点（例如环路巡检），ACYCLIC 会给出偏少的结果。
- **无向遍历会让边数翻倍**：两条方向相反的关系 `r1(X→Y)` 与 `r2(Y→X)`，在无向模式下从 X 看是**两条不同的边**。最小图自检里 TRAIL 的 2 跳解是 **2 条**而不是 1 条，WALK 是 **4 条**（允许 `r1,r1`）——这是最容易写错的地方。
- **有向 vs 无向差异极大**：路由器网络 A→Z 的 ACYCLIC 路径，有向遍历只有 15 条，无向遍历 80 条；官方给出的 `A-B-E-G-J-I-Z` 用到了 `J→I`，而图中只有 `I→J`，**有向遍历会直接漏掉它**。

## 8. 常见坑

1. **把默认当成 WALK**：写 `(a)-[:X]-*(b)` 期待返回所有走法，实际拿到的是 TRAIL 结果，短路径查询常常"莫名少一半"。
2. **用 `WHERE` 后过滤代替 ACYCLIC**：语义等价但代价是先把含环路径全量生成，内存与耗时都差一个量级。
3. **忘记 ACYCLIC 只管"路径内"**：跨路径的节点重复是允许的，equijoin 不受影响。
4. **在 TRAIL 下变长查询不写终点排除谓词**：会枚举出"经过终点又绕回来"的路径，结果数暴涨。
5. **版本混淆**：`DIFFERENT RELATIONSHIPS`（2025.06）与 `ACYCLIC`（2026.03）都只在 Cypher 25 提供，Cypher 5 上写这两个关键字会报语法错误。

## 9. 参考资料（实际阅读）

- [Cypher Manual → Patterns → Paths with unique relationships](https://neo4j.com/docs/cypher-manual/current/patterns/unique-relationship-paths/) — 默认关系唯一性、`DIFFERENT RELATIONSHIPS`、七桥示例图与 2/48/0 三组结果
- [Cypher Manual → Patterns → Acyclic paths](https://neo4j.com/docs/cypher-manual/current/patterns/acyclic-paths/) — `ACYCLIC` 语义、GQL path modes、路由器网络与各中间路由器占比
- [Cypher Manual → Patterns](https://neo4j.com/docs/cypher-manual/current/patterns/) — 章节索引（Primer / 定长 / 变长 / 非线性 / 最短路 / 唯一关系 / 重复元素 / 无环）

> 说明：官方 `Paths with unique relationships` 页公布了七桥图的**绝对条数**（2 / 48 / 0），本模型逐一复现；`Acyclic paths` 页只公布了**各中间路由器的百分比**（10 项）与该查询返回 10 行，未公布路径总数，本模型在无向遍历下得到 80 条且 10 项百分比全部精确复现（分母须为 8 的倍数，80 与之自洽）。
