# Neo4j 约束：唯一性 / 存在性 / 类型 / Key

> 目录：`07-数据存储/05-图数据库/SchemaConstraints/`（Neo4j/Cypher 第三批）
> 实现：Python（`schema_constraints.py` + `selfcheck_schema_constraints.py`，49 断言实跑全绿）、Go（`schema_constraints.go` + `selfcheck_schema_constraints.go`）

## 1. 简介

Neo4j 的约束不只是"唯一索引"的别名。四类约束各有作用域与 Edition 限制，其中 **Key 约束 = 存在性 + 唯一性**这一条最容易被忽略。本 demo 把四类约束的建立语法、命名冲突规则、`IF NOT EXISTS` 的三种命中情形，以及写入时的校验语义做成可断言的模型。

## 2. 原理详解

### 2.1 四类约束

| 类型 | 语法 | Edition | 语义 |
| --- | --- | --- | --- |
| **Property uniqueness** | `REQUIRE n.prop IS [NODE] UNIQUE` | Community | 指定属性（组合）的值在同类实体中唯一 |
| **Property existence** | `REQUIRE n.prop IS NOT NULL` | **Enterprise** | 属性必须存在 |
| **Property type** | `REQUIRE n.prop IS :: <TYPE>` | **Enterprise** | 属性必须是声明的类型 |
| **Key** | `REQUIRE n.prop IS [NODE] KEY` | **Enterprise** | **存在性 + 唯一性** |

- 节点用 `FOR (n:Label)`，关系用 `FOR ()-[r:REL_TYPE]-()`；`[NODE]` / `[REL[ATIONSHIP]]` 关键字**可省略**。
- 唯一性与 Key 都支持复合：`REQUIRE (n.p1, …, n.pn) IS NODE KEY`。
- 官方对 Key 的表述：*"Key constraints ensure that the property exist and the property value is unique for all nodes with a specific label or all relationships with a specific type. For composite key constraints on multiple properties, all properties must exists and the combination of property values must be unique."*
- 用旧 `CREATE CONSTRAINT` 语法创建的约束会被**自动加入**数据库的 graph type；官方推荐直接用 graph type 定义 schema（它提供更多、更复杂的约束类型）。

### 2.2 属性类型约束的类型表达式

比想象中丰富：

- 基础类型：`STRING` / `INTEGER` / `FLOAT` / `BOOLEAN` / `DATE` / `DURATION` / `POINT` / `MAP` …
- **容器元素可标非空**：`LIST<STRING NOT NULL>`、`LIST<INTEGER NOT NULL>`、`LIST<POINT NOT NULL>` …
- **联合类型**：`STRING | LIST<STRING NOT NULL>`
- **带维度的向量**：`VECTOR<INT32>(42)`、`VECTOR<FLOAT32>(1536)`

### 2.3 命名与幂等

- 约束名**建议显式给出**，且在**索引与约束之间**必须唯一（与索引共享同一命名空间）。
- `IF NOT EXISTS` 的命中条件有两个，**满足其一即不创建、只发通知**：
  1. 存在**同名**约束（无论它是什么类型）——官方 Example 29 就是"用一个已存在但类型不同的约束名来创建"；
  2. 存在**同类型且同模式**的约束（名字可以不同）。
- 通知文案形如：`` `CREATE CONSTRAINT new_sequels IF NOT EXISTS …` has no effect. `CONSTRAINT sequels …` already exists. ``

### 2.4 类型约束与 null 的交互（跨页结论）

官方文档 `Working with null` 明确写道：*"All data types in Cypher are nullable. This means that type predicate expressions always return true for null values."*

因此 **`IS :: STRING` 挡不住 null**：属性缺失或显式为 null 时，类型约束**通过**。想同时要求存在，就得再加一条存在性约束——或者直接用 **Key 约束**。

### 2.5 需要标注口径的两处

官方文档本页**没有**明确规定的两点，本模型按下列方式实现并在代码中标注：

1. **唯一性判定中 null/缺失属性是否参与**：本模型按「缺少任一指定属性的实体不参与唯一性校验」实现。
2. **长度/边界类细节**（如 `LIST<...>` 的空列表是否满足 `NOT NULL`）：本模型按「空列表里没有 null，故满足」实现。

## 3. 对比：唯一性 vs Key

| 输入 | `IS UNIQUE` | `IS NODE KEY` |
| --- | --- | --- |
| 首个有值实体 | ✅ 通过 | ✅ 通过 |
| 值重复 | ❌ 冲突 | ❌ 冲突 |
| **属性缺失** | ✅ **通过**（不参与唯一性） | ❌ **冲突**（存在性部分先拦） |
| 不同标签/类型 | ✅ 通过 | ✅ 通过 |
| 复合属性全同 | ❌ 冲突 | ❌ 冲突 |
| 复合属性仅一项相同 | ✅ 通过 | ✅ 通过 |

这一行的差异是选择 `UNIQUE` 还是 `KEY` 的实际依据。

## 4. 环境

- Python 3.8+（标准库）；Go 1.18+（标准库）。
- 无需 Neo4j 实例。

## 5. 运行方式

```bash
cd SchemaConstraints
python selfcheck_schema_constraints.py     # 49 条断言，输出 "ALL OK"
go run .
```

## 6. 关键代码

Key 约束就是复用存在性与唯一性两段校验：

```python
elif con.kind == KEY:
    # Key = 存在性 + 唯一性
    self._check_exists(con, ent)
    self._check_unique(con, ent)
```

类型表达式的解析用「先按 `|` 切联合，再逐支匹配」：

```python
def _value_matches_type(v, spec):
    for alt in spec.split("|"):
        if _one_matches(v, alt.strip()):
            return True
    return False
```

`_one_matches` 里 `LIST<...>` 要先把 ` NOT NULL` 后缀摘掉再递归到元素类型；空列表视为满足 `NOT NULL`。注意 `BOOLEAN` 必须显式排除出 `INTEGER`（Go 里 `bool` 与 `int` 是不同类型，Python 里 `bool` 是 `int` 的子类，必须单独判断）。

## 7. 性能边界与注意事项

- **existence / type / key 三类都是 Enterprise Edition 功能**，社区版建不了。
- **约束建不出来 vs 数据写不进去是两回事**：已有数据违反约束时，建约束会失败；本模型只覆盖"约束已存在时写入被拦"。
- **`IF NOT EXISTS` 的名字冲突优先于类型/模式匹配**：即使你要建的约束类型完全不同，只要撞了名字就什么都不做。

## 8. 常见坑

1. **以为 `IS :: STRING` 能挡 null**——挡不住，类型谓词对 null 恒为 true。
2. **以为唯一性约束能保证属性存在**——不能，缺属性的实体直接跳过唯一性校验；要 `KEY`。
3. **以为改名就能重复建同一约束**——同类型同模式照样报错。
4. **以为 `IF NOT EXISTS` 一定能建成**——撞名字就静默跳过，你的约束其实没建上。
5. **把约束名取得和索引名一样**——两者共享命名空间，必然冲突。
6. **在社区版上建 existence/type/key 约束**——语法看着没问题，实际不被支持。

## 9. 参考资料（实际阅读）

- [Cypher Manual → Constraints](https://neo4j.com/docs/cypher-manual/current/constraints/) — 四类约束与 Edition 标注、graph type 建议
- [Cypher Manual → Constraints → Syntax](https://neo4j.com/docs/cypher-manual/current/constraints/syntax/) — `CREATE CONSTRAINT` 完整语法：`IS [NODE] UNIQUE` / `IS NOT NULL` / `IS :: <TYPE>` / `IS [NODE] KEY`，节点与关系两种形式
- [Cypher Manual → Constraints → Managing constraints](https://neo4j.com/docs/cypher-manual/current/constraints/managing-constraints/) — Key = 存在性 + 唯一性、`LIST<STRING NOT NULL>` 与联合类型、`VECTOR<INT32>(42)`、命名唯一、`IF NOT EXISTS` 通知与三种命中情形
- [Cypher Manual → Working with null](https://neo4j.com/docs/cypher-manual/current/values-and-types/working-with-null/) — 类型谓词对 null 恒为 true（本 demo 类型约束处理 null 的依据）
