# Neo4j 搜索性能索引：四类索引的谓词可解性与 planner 选择

> 目录：`07-数据存储/05-图数据库/SearchIndexes/`（Neo4j/Cypher 第三批）
> 实现：Python（`search_indexes.py` + `selfcheck_search_indexes.py`，50 断言实跑全绿）、Go（`search_indexes.go` + `selfcheck_search_indexes.go`）

## 1. 简介

"我在 `Person.name` 上建了索引，为什么 `CONTAINS` 还是全表扫？"——因为 Neo4j 的索引**按类型划分能解决的谓词**，不是建了就万能。本 demo 把四类搜索性能索引的谓词可解性、索引名命名空间、`IF NOT EXISTS` 的幂等行为、复合索引的收录条件做成可断言的模型。

## 2. 原理详解

### 2.1 四种搜索性能索引

| 类型 | 定位 | 解决的谓词 |
| --- | --- | --- |
| **Range** | **默认**，建索引不写类型就得到它 | 等值 `n.prop = v`、列表成员 `n.prop IN [...]`、存在性 `n.prop IS NOT NULL`、范围 `n.prop > v`、前缀 `STARTS WITH` |
| **Text** | 为 `STRING` 的子串/后缀查询优化 | 等值、列表成员、`STARTS WITH`、**`ENDS WITH`**、**`CONTAINS`** |
| **Point** | 空间 `POINT` 值 | `n.prop = point({x:…, y:…})`、`point.withinBBox(n.prop, ll, ur)`、`point.distance(n.prop, c) <= d` |
| **Token lookup** | **只**解决节点标签 / 关系类型谓词 | `label` / `rel_type`（**不能解决任何属性谓词**） |

两个关键点：

- **Token lookup 索引建库时就自带两个**（一个管节点标签、一个管关系类型），不需要手动建。
- **Range 与 Point 的配置能力相反**：Range **没有**可配置项；Point **有**（`spatial.cartesian.min` / `max` 默认各为 `[-1000000.0, -1000000.0]` / `[1000000.0, 1000000.0]`，另有 `cartesian-3d`、`wgs-84` 等系列）。

### 2.2 为什么 Range 解不了 CONTAINS

Range 把字符串按**字典序**整体索引，因此天然适合前缀（`STARTS WITH` 可以转成范围扫描）；但判断 `"vel"` 是否出现在字符串**中间**时，它只能把所有索引值扫一遍。

Text 索引改用 **trigram 索引**：字符串被切成重叠的、每个含 3 个 Unicode 码点的三字符组。官方给的例子：

```
"developer" → ["dev", "eve", "vel", "elo", "lop", "ope", "per"]
```

9 个码点切出 7 个三字符组（长度 n 切出 n−2 个）。查 `CONTAINS "vel"` 或 `ENDS WITH "per"` 时直接查三字符组即可命中。**代价是 Text 索引只做精确匹配**——近似匹配（变体、拼写错误）和相似度打分要走**全文索引**（属于 semantic indexes，不在本 demo 范围）。

### 2.3 名字空间与幂等

- 索引名在**索引与约束之间**必须唯一；不显式命名则自动生成。建议显式命名。
- `CREATE INDEX` **默认非幂等**：重复创建同名、或**同模式同类型**（名字不同也算）的索引都会报错。
- 加 `IF NOT EXISTS` 后不报错、不创建，只回一条 informational 通知，例如：
  `` `CREATE INDEX node_index_name IF NOT EXISTS FOR (e:Person) ON (e.nickname)` has no effect. `TEXT INDEX node_text_index_nickname FOR (e:Person) ON (e.nickname)` already exists. ``
- **约束冲突例外**：如果存在同名或同模式同 backing index 类型的约束，**即使加了 `IF NOT EXISTS` 仍会报错**。本 demo 把约束名检查放在幂等短路之前来体现这一点。

### 2.4 复合索引的收录条件

官方原文：*only nodes with the specified label and that contain all the specified properties are added to the index.*

即 `CREATE INDEX … FOR (n:Person) ON (n.age, n.country)` **只收录**同时带 `Person` 标签、且 `age` 与 `country` **都存在**的节点。缺任何一个属性的节点都不会进索引，因此针对它的查询也享受不到索引加速。

### 2.5 planner 选择索引与 USING 提示

多个索引可用时，**Cypher planner 会挑最能高效解决该谓词的那个**；也可以用 `USING` 关键字显式指定，绕过 planner 的判断。

## 3. 对比：四类索引在同一批谓词上的表现

| 谓词 | Range | Text | Point | Token |
| --- | --- | :-: | :-: | :-: |
| `=` / `IN` | ✅ | ✅ | — | — |
| `IS NOT NULL` | ✅ | — | — | — |
| 范围 `>` `<` | ✅ | — | — | — |
| `STARTS WITH` | ✅ | ✅ | — | — |
| `ENDS WITH` | ❌ | ✅ | — | — |
| `CONTAINS` | ❌ | ✅ | — | — |
| `point.distance` / `withinBBox` | — | — | ✅ | — |
| 节点标签 / 关系类型 | — | — | — | ✅ |

## 4. 环境

- Python 3.8+（标准库）；Go 1.18+（标准库）。
- 无需 Neo4j 实例。

## 5. 运行方式

```bash
cd SearchIndexes
python selfcheck_search_indexes.py     # 50 条断言，输出 "ALL OK"
go run .
```

## 6. 关键代码

谓词可解性就是一张表 + 一次属性覆盖检查：

```python
RANGE_PREDS = {"eq", "in", "exists", "range", "starts_with"}
TEXT_PREDS  = {"eq", "in", "starts_with", "ends_with", "contains"}
POINT_PREDS = {"point_eq", "within_bbox", "distance"}
TOKEN_PREDS = {"label", "rel_type"}

def can_solve(index, pred, prop=None):
    if index.itype == TOKEN:              # token 只认标签/类型谓词
        return pred in TOKEN_PREDS
    if pred not in PREDS_BY_TYPE[index.itype]:
        return False
    return prop is None or prop in index.props   # 属性索引必须覆盖该属性
```

建索引时**约束名检查必须早于 `IF NOT EXISTS` 短路**：

```python
if name in self.constraints:            # 约束冲突即使 IF NOT EXISTS 也报错
    raise SchemaError(...)
if if_not_exists:
    ...                                  # 命中既有索引 → 发通知并原样返回
```

## 7. 性能边界与注意事项

- **Text 索引只对 `STRING` 属性有意义**，且只做精确匹配；模糊/相似度场景要用全文索引。
- **trigram 对短查询串失效**：长度 < 3 的查询串切不出三字符组（`"ab"` → `[]`）。官方未规定其走法，本模型按「退化为普通子串判定」实现，README 已标注为口径。
- **复合索引的列顺序不影响收录条件，但影响可解的谓词组合**：只有索引覆盖到的属性才可能被索引加速。
- **`USING` 提示是强约束**：指定了一个解不了该谓词的索引会直接失败，而不是静默回退到 planner 的选择。

## 8. 常见坑

1. **建了 Range 索引却期待 `CONTAINS` 变快**——Range 解不了 `CONTAINS`/`ENDS WITH`，必须另建 Text 索引。
2. **以为名字不同就能重复建**——同模式同类型也算重复，默认会报错。
3. **以为 `IF NOT EXISTS` 万能**——撞上同名约束照样报错。
4. **以为复合索引覆盖部分属性也生效**——必须标签匹配且**全部**指定属性都存在。
5. **给 token lookup 索引加属性**——它只解决标签/类型谓词，带属性是非法的。
6. **忘记建库自带两个 token 索引**——不需要（也不应该）手动再建。

## 9. 参考资料（实际阅读）

- [Cypher Manual → Indexes → Search-performance indexes](https://neo4j.com/docs/cypher-manual/current/indexes-for-search-performance/) — 四类索引总览、建库自带两个 token 索引、planner 自动挑索引与 `USING` 提示
- [Cypher Manual → Indexes → Search-performance indexes → Create indexes](https://neo4j.com/docs/cypher-manual/current/indexes/search-performance-indexes/create-indexes/) — 不指定类型得 Range、Range/Text/Point 的 Supported predicates 表、trigram 索引与 `"developer"` 示例、Point 的 `spatial.cartesian` 默认配置、名字唯一、`IF NOT EXISTS` 通知文案、复合索引收录条件
