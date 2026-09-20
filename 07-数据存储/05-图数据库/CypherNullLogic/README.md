# Cypher 的 null 与三值逻辑

> 目录：`07-数据存储/05-图数据库/CypherNullLogic/`（Neo4j/Cypher 第三批）
> 实现：Python（`cypher_null.py` + `selfcheck_cypher_null.py`，100 断言实跑全绿）、Go（`cypher_null.go` + `selfcheck_cypher_null.go`）

## 1. 简介

Cypher 里的 `null` 不是"空值"，而是**缺失或未知值（missing or unknown value）**。它被当作三值逻辑里的 **unknown** 参与运算，因此：

- `null = null` 得到 **`null`**，不是 `true`；
- `WHERE` 里 `null` 与 `false` 等价（都会被过滤掉）；
- `2 IN [1, null, 3]` 是 `null`，但 `2 IN [1, 2, null]` 是 `true`；
- `null IN []` 是 **`false`**，而 `null IN [1,2,3]` 是 `null`。

这四条是图查询里最常见的一类"数据明明在，却查不出来"的根因。本 demo 按官方真值表实现完整三值逻辑，并逐条断言。

## 2. 原理详解

### 2.1 三值逻辑真值表（官方文档原表，9 行）

| a | b | a AND b | a OR b | a XOR b | NOT a |
| --- | --- | --- | --- | --- | --- |
| false | false | false | false | false | **true** |
| false | null | false | null | null | **true** |
| false | true | false | true | true | **true** |
| true | false | false | true | true | false |
| true | null | null | **true** | null | false |
| true | true | true | true | false | false |
| null | false | false | null | null | null |
| null | null | null | null | null | null |
| null | true | null | **true** | null | null |

两条可直接从表里读出的规律，也是最容易被写错的地方：

- **AND 被 false 吸收，OR 被 true 吸收**：`false AND null = false`、`true OR null = true`。只要有一侧能"一锤定音"，null 就无法污染结果。
- **XOR 没有吸收律**：`true XOR null = null`、`false XOR null = null`。这是 XOR 与 AND/OR 的关键区别，本 demo 用真值表逐格断言。

### 2.2 null 不等于 null

官方原话：*"null is not equal to null. Not knowing two values does not imply that they are the same value."*

因此 `null = null` 与 `null <> null` **都得到 null**。要判断空值必须用专门的 `IS NULL` / `IS NOT NULL`——用 `=` 或 `<>` 永远得到 null，在 `WHERE` 里就被当成 false，**整行被静默过滤**。

`n.prop IS NOT NULL` 的语义是"属性存在**且**值不为 null"；属性存在但显式写成 null，与属性根本不存在，在 Cypher 里**不可区分**（都读到 null）。

### 2.3 IN 运算符

官方给出的 8 个例子：

| 表达式 | 结果 |
| --- | --- |
| `2 IN [1, 2, 3]` | true |
| `2 IN [1, null, 3]` | null |
| `2 IN [1, 2, null]` | **true** |
| `2 IN [1]` | false |
| `2 IN []` | false |
| `null IN [1, 2, 3]` | null |
| `null IN [1, null, 3]` | null |
| `null IN []` | **false** |

规则可概括为：*能确定存在 → true；列表含 null 且没有匹配元素 → null；否则 false。*

注意最后一行：`null IN []` 是 false 而不是 null。空列表让"未知"变成了"确定"——列表里没有任何元素，所以一定能确定不存在匹配。这是 `IN` 唯一一个"null 输入却得到确定布尔值"的分支，也是最容易实现的错的分支。

`all` / `any` / `none` / `single` 遵循同样的总纲：*能确定就返回 true 或 false，否则产出 null。* 实现上 `any` 就是 OR 折叠、`all` 就是 AND 折叠、`none` 是 `NOT any()`：

- `any([true, null]) = true`（OR 被 true 吸收）
- `all([false, null]) = false`（AND 被 false 吸收）
- `all([true, null]) = null`、`any([false, null]) = null`
- `single([true, null]) = null`（那个 null 若取 true 就有两个 true 了，无法确定）

### 2.4 [] 取值与区间

列表下标、映射取值、区间切片，只要**任一端是 null**，整个结果就是 null：

- `[1,2,3][null]` → null；`[1,2,3,4][null..2]` → null；`[1,2,3][1..null]` → null；`{age:25}[null]` → null。

用参数传边界时（$a[$lower..$upper]）很容易踩到。官方给出的规避写法是给上下界兜底：

```cypher
a[coalesce($lower,0)..coalesce($upper,size(a))]
```

### 2.5 类型谓词对 null 恒为 true

官方原话：*"All data types in Cypher are nullable. This means that type predicate expressions always return true for null values."*

即 `null IS :: INTEGER` 返回 **true**。这是个反直觉的坑：类型谓词**挡不住 null**，想排除空值必须额外写 `IS NOT NULL`。

### 2.6 ORDER BY 的 null 位置

官方原话：*"When sorting, null values appear last in ascending order and first in descending order."*

升序排最后、降序排最前——注意这与某些数据库的默认行为相反，分页时如果按可空字段排序，第一页可能整页都是 null。

## 3. 对比：SQL 三值逻辑 vs Cypher

| 维度 | SQL | Cypher |
| --- | --- | --- |
| `NULL = NULL` | NULL（未知） | **null**（同为未知） |
| `WHERE` 处理 | 非 true 即排除 | **非 true 即排除**（官方明确表述一致） |
| 空列表/空集合 | `x IN ()` 语法上不成立 | `null IN []` → **false**（可确定） |
| 类型谓词 | 不适用 | 对 null **恒为 true** |
| 排序 | 各实现不一（PG 默认 null 最后升序） | 官方规定：升序最后 / 降序最前 |

## 4. 环境

- Python 3.8+（标准库）；Go 1.18+（标准库，用到 type alias）。
- 无需 Neo4j 实例。

## 5. 运行方式

```bash
cd CypherNullLogic
python selfcheck_cypher_null.py     # 100 条断言，输出 "ALL OK"
go run .
```

## 6. 关键代码

AND / OR 的差别只在「哪一侧能一锤定音」：

```python
def cy_and(a, b):
    if a is False or b is False:   # false 吸收
        return False
    if a is None or b is None:
        return None
    return True

def cy_or(a, b):
    if a is True or b is True:     # true 吸收
        return True
    if a is None or b is None:
        return None
    return False
```

`IN` 必须自己实现，不能直接用 Python 的 `in`（Python 的 `None in [1,None,3]` 是 True，与 Cypher 语义完全不同）：

```python
def cy_in(x, lst):
    hit = False
    for v in lst:
        r = cy_eq(x, v)          # 注意：不是 x == v
        if r is True:
            return True          # 确定命中，立即短路
        if r is None:
            hit = True           # 记下"可能命中"，继续找确定命中
    return None if hit else False
```

Go 版用 `Tri = *bool` 表示三值：`nil` 即 null，`&true` / `&false` 为确定值。

## 7. 性能边界与注意事项

- **短路不是优化而是语义**：`cy_in` 里一旦出现确定命中必须立即返回 true，否则 `2 IN [1,2,null]` 会因为"存在 null"而错误地返回 null。
- **三值逻辑不能退化成二值再取反**：`NOT null` 仍是 null，所以 `none(...)` 必须写成 `NOT any(...)` 的**三值**取反，而不是"统计 false 的个数"。
- `single()` 的逐函数表格官方**没有公布**，本 demo 按"能确定就给确定值"的总纲实现，并在 README 与代码注释中标注为口径。

## 8. 常见坑

1. **用 `= null` 判空**：永远得到 null，在 `WHERE` 里整行消失，且不报错——最隐蔽的一类 bug。
2. **以为 `null IN []` 是 null**：实际是 false。
3. **以为 `2 IN [1,2,null]` 是 null**：实际是 true（先命中了 2）。
4. **用类型谓词挡 null**：`IS :: INTEGER` 对 null 返回 true，挡不住。
5. **按可空字段排序后分页**：降序时第一页可能全是 null。
6. **把 XOR 当成"不等"来短路**：`true XOR null` 是 null，不是 true。

## 9. 参考资料（实际阅读）

- [Cypher Manual → Values and types → Working with null](https://neo4j.com/docs/cypher-manual/current/values-and-types/working-with-null/) — 三值逻辑真值表、`IN` 的 8 个例子、`[]` 的 4 个例子、`IS NULL`/`IS NOT NULL`、类型谓词对 null 恒为 true、产出 null 的表达式清单
- [Cypher Manual → Clauses → ORDER BY → Null values](https://neo4j.com/docs/cypher-manual/current/clauses/order-by/) — "null values appear last in ascending order and first in descending order"
