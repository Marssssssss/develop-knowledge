# EXPLAIN 执行计划解读

## 简介

- `EXPLAIN` 显示规划器为一条 SQL 选出的**执行计划树**；`EXPLAIN ANALYZE` 会**真的执行**该查询，并把每个节点的真实行数与真实耗时附在估算值旁边。它是应用侧判断"SQL 为什么慢"的第一手证据。
- 关键概念：
  - **startup / total cost**：`cost=0.00..445.00` 是"开始输出前"与"跑完全部"的估算代价；
  - **rows / width**：该节点**输出**的估算行数与平均行宽（不是扫描行数）；
  - **actual time / loops**：真实毫秒与节点被执行的次数，`actual time` 与 `actual rows` 都是**每次执行的均值**；
  - **Rows Removed by Filter / Index Recheck**：被过滤条件或索引重检丢弃的行数；
  - **BUFFERS**：`shared hit/read/dirtied/written`，`ANALYZE` 会**隐式打开**它。
- 历史背景：代价模型以"页读取"为单位（`seq_page_cost` 约定为 1.0），所以 cost 是**相对量**，与毫秒不可直接比较 —— 这条是新人读计划时最大的误解来源。

## 原理详解

1. **代价算式**：文档给出 `(disk pages read * seq_page_cost) + (rows scanned * cpu_tuple_cost)`，默认 `seq_page_cost=1.0`、`cpu_tuple_cost=0.01`，算例 `345 × 1.0 + 10000 × 0.01 = 445`。
2. **上层节点代价包含全部子节点代价**，因此"第一个数字"就是规划器要最小化的目标；但文档同时提醒代价**不包含**把结果转成文本、发往客户端的时间。
3. **rows 的语义**：文档原话是 "not the number of rows processed or scanned by the plan node, but rather the number emitted by the node"，所以看到 `Seq Scan rows=7000` 而表有 10000 行，说明 filter 丢掉了 3000 行 —— 计划里会显式写 `Rows Removed by Filter: 3000`。
4. **loops 的口径**：嵌套循环的内层扫描每个外层行执行一次，此时 `loops=10000`、`actual time=0.003ms`、`rows=1` 都是**均值**；文档要求 "Multiply by the `loops` value to get the total time actually spent in the node"。
5. **估算 vs 实际**：文档说最该看的是 "whether the estimated row counts are reasonably close to reality"。但 **LIMIT 场景的差异不是估算错误** —— 子节点的 cost/rows 仍按"跑完"显示，实际只取了 2 行；文档明确 "This is not an estimation error, only a discrepancy in the way the estimates and true values are displayed"。
6. **扫描方式的取舍**：无 `WHERE` → Seq Scan；只占**一页**的表 "you'll nearly always get a sequential scan plan whether indexes are available or not"；返回极少行 → Index Scan（按索引序取行，且能免掉 `ORDER BY` 的排序）；中等选择性 → Bitmap Index Scan + Bitmap Heap Scan 两步。
7. **Merge Join 的统计假象**：外层重复键值会让内层被"回退重扫"，文档说明 `EXPLAIN ANALYZE` **把这些重复发射当成真实行数**，于是内层 actual rows 可能远大于表里真实行数。

### 常见节点与解读要点

| 节点 | 出现场景 | 解读要点 |
| --- | --- | --- |
| `Seq Scan` | 无索引可用 / 小表 / 高选择性差 | 一页表必然走它，不要急着加索引 |
| `Index Scan` | 返回极少行；`ORDER BY` 与索引序一致 | 省掉 Sort，但随机读代价高 |
| `Bitmap Index Scan` + `Bitmap Heap Scan` | 中等选择性 | 两步：先定位元组位置，再按页批量回表 |
| `Nested Loop` | 外层小、内层有索引 | 内层 loops = 外层行数，看总耗时必须乘 loops |
| `Hash Join` | 大表等值连接 | 构建哈希表的内存受 `work_mem` 影响 |
| `Merge Join` | 两侧已按连接键有序 | actual rows 可能被重复扫描放大 |
| `Limit` | 分页 | 子节点估算仍是"跑完"口径，别误判为估算偏差 |

## 环境准备

- Python 3.9+（仅标准库）；Go 1.18+（可选对照）。demo 内置的是**文档示例的真实计划文本**，无需安装 PostgreSQL。

## 运行方式

### Python

```bash
python3 python/explain_model.py
```

### Go

```bash
cd go && go run explain_model.go
```

## 关键代码片段

```python
def total_ms(self) -> float:
    """文档：actual time 与 rows 都是每次执行的均值，
    'Multiply by the loops value to get the total time actually spent in the node.'"""
    return round(self.a_end * self.loops, 4)
```

```python
def seq_scan_cost(pages: int, rows: int) -> float:
    """(disk pages read * seq_page_cost) + (rows scanned * cpu_tuple_cost)"""
    return pages * SEQ_PAGE_COST + rows * CPU_TUPLE_COST      # 345, 10000 -> 445
```

```python
# 被 Limit 截断的分支不能报成「估算偏差」
trunc = under_limit or node.label.startswith("Limit")
if ratio > 10 or ratio < 0.1:
    (truncated if under_limit else misestimates).append((node.label, ratio))
```

## 性能与边界

- cost 是**无量纲相对量**，只为比较不同计划服务；`actual time` 才是毫秒，两者不可比。
- `EXPLAIN ANALYZE` 会真的执行 SQL（含 DML），所以**不要**在线上对写语句随手 `ANALYZE`；`BUFFERS` 是 `ANALYZE` 的隐式副作用，可用 `EXPLAIN (ANALYZE, BUFFERS OFF)` 关掉。
- `Planning time` **不含解析与重写**；`Execution time` **不含解析、重写、规划**，但含执行器启停与触发器（`AFTER` 触发器不计入 DML 节点）；`SERIALIZE` 选项可额外测量"转成可显示格式"的耗时。
- 估算误差本身来自 `ANALYZE` 的**随机采样**统计，文档提醒不同机器、不同时间的结果会有小幅差异。
- 关闭计划类型用 `enable_seqscan = off` 之类的开关时，文档明确它们**只是劝阻**而不是禁止；被压制的节点会在输出里标 `Disabled: true`。
- 要把输出喂给程序分析，应使用 `FORMAT JSON/XML/YAML`，文本格式不是稳定接口。

## 注意事项与常见坑

- **把 cost 当毫秒**：cost=445 不等于 445ms；`actual time` 的单位才是毫秒。
- **漏乘 loops**：嵌套循环里 `0.003ms` 看着无感，乘上 `loops=10000` 就是 30ms，往往才是真正的热点。
- **把 LIMIT 的估算差异当 bug**：子节点 rows 是"跑完"口径，与实际只取 2 行不是一回事。
- **忽略 Rows Removed**：`Rows Removed by Filter: 3000` 意味着 3000 行被读出来又丢掉，是加索引/改条件的直接依据；lossy 索引则表现为 `Rows Removed by Index Recheck`。
- **Merge Join 的 actual rows 陷阱**：数值可能大于表真实行数（重复发射被计入），别据此判断"数据翻倍了"。
- **一页小表加索引无效**：规划器算下来只有一次页读取，任何索引路径都更贵。
- **`FULL`/`VERBOSE` 之外没有"隐藏列"**：要机器可读就用 JSON 格式，别用正则硬解析文本计划（本 demo 的解析器仅用于演示，README 已注明文本格式非稳定接口）。

## 参考资料（实际阅读过的权威来源）

- [PostgreSQL 18 Documentation — 14.1. Using EXPLAIN](https://www.postgresql.org/docs/current/using-explain.html) — 代价算式与默认常量、startup/total/rows/width 语义、actual time 与 loops 的均值口径、Rows Removed by Filter/Index Recheck、BUFFERS 语义、Seq Scan 与索引路径的取舍、Merge Join 重复发射假象、Planning/Execution time 边界、Disabled 标记、FORMAT 建议。
