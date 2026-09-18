# 键集分页与深分页

## 简介

- **OFFSET 分页**（`LIMIT 10 OFFSET 10000`）语义上只有"丢弃前 N 行"这一个参数，数据库必须先把这些行**排好序、取出来、再扔掉**；**键集分页**（keyset / seek method）改成"只取上一个已见位置之后的数据"，用游标替代行数。
- 关键概念：
  - **OFFSET 的语义**：SQL:2023 Part 2 §4.17.3 对派生表的定义是 "the rows in the derived table are **first sorted** according to the `<order by clause>` and then limited by **dropping** the number of rows specified in the `<result offset clause>` from the beginning"；
  - **深分页**：PostgreSQL 文档明确 "The rows skipped by an `OFFSET` clause still have to be computed inside the server; therefore a large `OFFSET` might be inefficient."；
  - **键集/seek**：`WHERE id < ?last_seen_id ORDER BY id DESC FETCH FIRST 10 ROWS ONLY`；
  - **tie-breaker**：排序键有并列时必须补一个唯一列（如主键），否则游标会停在原地；
  - **稳定性**：翻页期间插入/删除会让 OFFSET 出现重复或漏行，键集分页不受影响。
- 历史背景：OFFSET 的写法源于 SQL 标准，但"跳过 N 行"这个抽象丢掉了一切上下文（不知道上次看到哪个值），所以它天生只能做"取出来再扔"。Markus Winand 的 "no-offset" 运动就是在推动框架原生支持 keyset。

## 原理详解

1. **OFFSET 的执行路径**：按 `ORDER BY` 排序 → 从头开始取出 `OFFSET` 行 → 丢弃 → 再取 `LIMIT` 行。所以第 1000 页（每页 10）要取出 **10000 行**才返回 10 行。
2. **键集的执行路径**：把"上一个已见键"作为游标 → 在 (sort key, tie-breaker) 的唯一索引上做一次定位（O(log N)）→ 顺序取 10 行。同一页的取行数从 10000 降到 10 行 + 一次二分。
3. **为什么必须唯一序**：文档要求 "use an `ORDER BY` clause that constrains the result rows into a unique order. Otherwise you will get an unpredictable subset"。当排序键有并列时，数据库对同分行的相对顺序是不稳定的，第 1 页与第 2 页可能互相重叠。
4. **tie-breaker 的写法**：`WHERE (score, id) < (?, ?)` 的行值比较（row-value comparison）一次把两列都纳入游标；只写 `WHERE score < ?` 会在同分处**永久停在第一页**。
5. **翻页期间的写入**：OFFSET 的本质是"用行数描述位置"，一旦前面插入一行，第 1 页末尾的行会被挤到第 2 页 → **出现重复**（删除则相反：漏行）。键集用"值"描述位置，天然免疫这类漂移（代价是看不到"新插入的、排在你前面"的行）。
6. **LIMIT 影响计划**：文档指出 "The query optimizer takes `LIMIT` into account when generating query plans"，因此不同 `LIMIT`/`OFFSET` 值可能拿到不同计划（不同行序），**没有 ORDER BY 时结果会自相矛盾**；这不是 bug，而是 SQL 不承诺无序结果。
7. **边界语义**：`OFFSET 0` 等同于省略 `OFFSET`，`LIMIT ALL` 等同于省略 `LIMIT`（`LIMIT NULL` / `OFFSET NULL` 同理）。

### OFFSET vs 键集

| 维度 | OFFSET | 键集（keyset / seek） |
| --- | --- | --- |
| 第 1000 页取行数 | 10000 行（本 demo 实测） | 10 行 + O(log N) 定位 |
| 翻页期间插入 | 出现重复 | 不重复、不漏行 |
| 跳转到任意页 | 可以（给定页号即可） | **不可以**（必须从已知键往后走） |
| 需要的参数 | 页号 | 上一页最后一个键（多列） |
| 索引要求 | `ORDER BY` 列上有索引才不必排序 | 必须能在 (sort key, tie-breaker) 上定位 |
| 适用界面 | 有页码的列表页 | 无限滚动 / "加载更多" |

## 环境准备

- Python 3.9+（仅标准库）；Go 1.18+（可选对照）。demo 用内存索引模拟排序后的扫描代价。

## 运行方式

### Python

```bash
python3 python/keyset_pagination.py
```

### Go

```bash
cd go && go run keyset_pagination.go
```

## 关键代码片段

```python
# OFFSET 的全部语义：先排序，再丢弃 N 行（被丢弃的行照样被取出来）
ordered = sorted(self.rows, key=lambda r: (-r[0], -r[1]))
skipped = page * size
self.rows_examined += page * size + size
return ordered[skipped:skipped + size]
```

```python
# 键集：用「上一页最后一个键」定位，不丢弃任何行
key = (-last[0], -last[1])
start = bisect.bisect_right([(-r[0], -r[1]) for r in ordered], key)
self.rows_examined += start.bit_length() + size          # O(log N) + 本页行数
```

```go
// 行值比较：(score, id) < (cursor.score, cursor.id) —— 两列一起当游标
start = sort.Search(len(ordered), func(i int) bool {
	return ordered[i].score < cursor.score ||
		(ordered[i].score == cursor.score && ordered[i].id < cursor.id)
})
```

## 性能与边界

- 代价模型：OFFSET 第 p 页取行数 = `p × size + size`（线性增长）；键集 ≈ `O(log N) + size`（与页深无关）。
- 本 demo 实测：同为取第 1000 页，OFFSET 取 10000 行、键集取 24 行（10 + 4 位定位 + 10），**差 400 倍以上**。
- 键集无法"跳到第 N 页"，因此只适合顺序浏览的界面（无限滚动、导出游标）；需要页码跳转就得回退到 OFFSET 或额外维护位置表。
- 大 OFFSET 的代价不只是取行：它还让优化器更可能放弃索引走顺序扫描（文档：不同 LIMIT 会得到不同计划）。
- 游标列必须是**唯一且不可变**的：用 `updated_at` 之类的可变列做游标，会在行被更新后出现重复/漏行。

## 注意事项与常见坑

- **把 OFFSET 当成"免费的跳过"**：被跳过的行由服务器计算出来，代价实打实。
- **排序键不唯一却不加 tie-breaker**：同分行在不同页之间重复出现；行值比较 `(score, id) < (?, ?)` 是标准解法。
- **没有 ORDER BY**：分页结果不可预测，且不同 `LIMIT` 触发不同计划时前后页会完全不衔接（demo 第 5 节复现了这一点）。
- **用页号做游标**：`?page=3` 只能用于 OFFSET；键集分页的对外契约是"返回下一页的游标"，而不是页码。
- **把游标当成保密数据**：游标里通常编码了排序列的原始值，直接暴露时要考虑是否泄露业务数据（可改为服务端存储游标）。
- **忽略并发删除**：键集在"游标指向的行被删除"时仍能正确定位（比较的是值不是那一行），但按键值倒序游标的实现（用 row id 而非值）则会漏行。

## 参考资料（实际阅读过的权威来源）

- [PostgreSQL 18 Documentation — 7.6. LIMIT and OFFSET](https://www.postgresql.org/docs/current/queries-limit.html) — OFFSET 先跳过再数 LIMIT、被跳过的行仍由服务器计算、必须用唯一 ORDER BY、优化器会因 LIMIT 改变计划导致结果不一致、`OFFSET 0`/`LIMIT ALL` 的等价语义。
- [use-the-index-luke.com — We need tool support for keyset pagination](https://use-the-index-luke.com/no-offset) — 引用 SQL:2023 Part 2 §4.17.3 的"先排序再丢弃 N 行"定义、keyset 基本写法、插入导致的重复异常、无法跳转到任意页的局限、各语言生态的 keyset 支持现状。
