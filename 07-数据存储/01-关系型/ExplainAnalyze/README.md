# EXPLAIN ANALYZE 的读数口径

## 简介

`EXPLAIN` 给的是**估算**;`EXPLAIN ANALYZE` 额外**真的执行一遍**并报告实测。二者并排
显示,于是"计划好不好"这个问题可以被量化成"估算和实测差多少"。

但**读数比执行更难**——输出里至少有三处数值不是你以为的那个意思:

1. `rows` 和 `actual time` 是**每次执行的平均值**,不是总和,要乘 `loops` 才是总量;
2. `BitmapAnd` / `BitmapOr` 的 `actual rows` **恒为 0**;
3. `LIMIT` / merge join 会让子节点**提前停**,此时估算与实测的落差**不是**估算错误。

本 demo 按 PostgreSQL 18 官方文档《Using EXPLAIN》把这些口径做成可执行模型,
用文档里的真实数字逐条对拍。

## 原理详解

### 1. 估算:cost / rows / width

```text
Seq Scan on tenk1 (cost=0.00..445.00 rows=10000 width=244)
```

| 字段 | 含义 |
| --- | --- |
| `cost=A..B` | A = startup cost(产出第一行前的开销),B = **按跑到底**算的总开销 |
| `rows` | **本节点输出**的行数(不是扫描的行数) |
| `width` | 输出行的平均字节宽 |

上层节点的 cost **包含**全部子节点。文档给的算式:

```text
cost = disk_pages * seq_page_cost + rows_scanned * cpu_tuple_cost
     = 345 * 1.0 + 10000 * 0.01 = 445
```

加上 `WHERE` 之后 cost 不降反升:多出的 `10000 * cpu_operator_cost(0.0025) = 25`,
即 `470` —— 因为过滤掉的那些行也要被读出来算一遍。

### 2. 实测:actual time / rows / loops

```text
Index Scan using tenk2_unique2 on tenk2 t2 (cost=0.29..7.90 rows=1 width=244)
                                  (actual time=0.003..0.003 rows=1.00 loops=10)
```

文档原话:

> In such cases the loops value reports the total number of executions of the node, and
> the actual time and rows values shown are **averages per-execution**. Multiply by the
> loops value to get the total time actually spent in the node.

所以:

| 量 | 公式 |
| --- | --- |
| 真实总行数 | `actual rows × loops` |
| 真实总耗时 | `actual time(last) × loops` |

文档里那个例子:内层被 10 个外行各触发一次,`actual time=0.003` 但总耗时是 `0.030 ms`。

### 3. Rows Removed by Filter

只在**至少拒绝了 1 行**时出现。它才是最有用的诊断数:`Seq Scan` 输出 7000 行却扫了
10000 行,说明过滤条件没走索引 —— 但光看 `rows=7000` 是看不出来的。

### 4. 三个读数陷阱(Caveats 章节)

| 现象 | 真相 |
| --- | --- |
| `BitmapAnd` / `BitmapOr` 的 `actual rows` 是 0 | 位图节点不产出行,**恒为 0**,与估算好坏无关 |
| `LIMIT` 下子节点估算 10 行、实测 2 行 | 估算按"跑到底"显示,实测被上层截断 —— **不是估算错误** |
| merge join 内层行数比关系本身还大 | 外层重复键会让内层**回溯重扫**,重复发射的行被重复计数 |

另外两条 Caveats 是环境性的,不在模型里:

- `EXPLAIN ANALYZE` **不把结果发给客户端**,所以不含网络传输与输出转换开销(除非 `SERIALIZE`);
- 计时本身有开销,慢 `gettimeofday()` 的机器上可能显著,用 `pg_test_timing` 测。

## 对比:估算 vs 实测

| 维度 | `EXPLAIN` | `EXPLAIN ANALYZE` |
| --- | --- | --- |
| 是否执行 | 否 | **是**(DML 会真的改动数据) |
| 数字来源 | 统计信息 + 代价模型 | 执行器 instrumentation |
| rows / time | 估算值 | 每次执行的**均值** |
| 额外信息 | 无 | `Rows Removed`、`Buffers`、`Index Searches`、`Heap Fetches`、`Sort Method`、`Hash Buckets` |
| 代价 | 只有规划时间 | 规划 + 执行 + 计时开销 |

## 环境与运行

- Python 3(仅标准库)、Go 1.22(仅标准库)

```bash
python python/main.py               # 文档示例的复现输出
python python/selfcheck_explain.py  # 33 项断言(含 445 / 470 / 0.030 三个文档原值)
cd go && go run .
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/planmodel.py` | `Node`(估算 + instrumentation)、`actual_rows` / `actual_time` / `total_*`、`est_error`、`render`、`seq_scan_cost` |
| `go/plan.go` `go/main.go` | 同一模型的 Go 转写 |

## 性能边界与注意事项

- **本机不跑 PostgreSQL**:本 demo 是**读数模型**,不是执行器;它复现的是文档口径,
  不替代真实 `EXPLAIN (ANALYZE, BUFFERS)` 输出。
- **别在小表上外推**:文档明确警告"小表结论不能推到大表",代价模型非线性,
  只占 1 页的表几乎总是走顺序扫描。
- **比值要看量级**:估算 1 行、实测 0 行时比值无意义(分母为 0),此时应该看
  `Rows Removed by Filter`。
- **估算误差的倍数不唯一**:demo 给的是 `est / (rows × loops)`;也有人用
  `max(est/actual, actual/est)`,两种口径在汇报时要说清楚(本项目取前者,低估时 < 1)。

## 参考资料

- PostgreSQL 18 官方文档《Using EXPLAIN》(14.1: EXPLAIN Basics / EXPLAIN ANALYZE / Caveats)
  —— <https://www.postgresql.org/docs/current/using-explain.html>
- PostgreSQL 18 官方文档《Query Planning》GUC(19.7): `seq_page_cost=1.0`、
  `cpu_tuple_cost=0.01`、`cpu_operator_cost=0.0025`、`cpu_index_tuple_cost=0.005`、
  `random_page_cost=4.0` —— <https://www.postgresql.org/docs/current/runtime-config-query.html>
