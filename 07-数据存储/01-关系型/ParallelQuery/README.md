# 并行查询:worker 数是怎么算出来的

## 简介

同一条 `SELECT`,PostgreSQL 可能派 0 个、1 个、2 个……worker 去跑。`Workers Planned: 2`
这个数字不是拍出来的,而是 `allpaths.c` 里一段二十行的循环算出来的:

> Select the number of workers based on the log of the size of the relation.

本 demo 逐行转写这段 `compute_parallel_worker`,并把它和四个旋钮
(`min_parallel_table_scan_size`、`max_parallel_workers_per_gather`、表级 reloption、
BASEREL 门槛)的关系摆在一起。结论之一有点反直觉:

> **默认配置下 worker 数几乎恒为 2** —— 因为 `max_parallel_workers_per_gather` 默认就是 2,
> 表只要超过 24MB(3072 页)就会被压回 2。想让大表真的吃到更多核,得先抬这个上限。

## 原理详解

### 1. 官方公式(转写自 `compute_parallel_worker`)

```text
if 表级 reloption parallel_workers 已设置:
    workers = reloption                       # 不看别的因素
else if 是 BASEREL 且 (heap_pages < min_parallel_table_scan_size
                       or index_pages < min_parallel_index_scan_size):
    return 0                                  # 太小, 不值得
else:
    workers = 1; threshold = min_parallel_table_scan_size
    while heap_pages >= threshold * 3:
        workers += 1; threshold *= 3
    # 索引侧同样算一遍, 取两者的**较小值**
return min(workers, max_workers)
```

于是阈值序列是 `1024 → 3072 → 9216 → 27648 → 82944 → …`(每档 ×3),
**表每大三倍多一个 worker**。

| 页数 | 约合 | log3 结果 | 默认上限 2 后 |
| --- | --- | --- | --- |
| 512 | 4MB | 1 | **0**(下界门槛) |
| 1023 | 8MB−8kB | 1 | **0** |
| 1024 | 8MB | 1 | 1 |
| 3071 | 24MB−8kB | 1 | 1 |
| 3072 | 24MB | 2 | 2 |
| 9216 | 72MB | 3 | 2 |
| 100000 | 781MB | 5 | 2 |

### 2. 四个旋钮

| 旋钮 | 默认 | 作用 |
| --- | --- | --- |
| `min_parallel_table_scan_size` | 8MB(1024 页) | 下界:**低于它直接不并行**(仅对 BASEREL 生效) |
| `max_parallel_workers_per_gather` | 2 | 上界:`Min(workers, 上限)` |
| 表级 `parallel_workers` reloption | 未设 | 设置后**直接决定**,且跳过下界门槛 |
| 是否 BASEREL | — | 继承子表/分区不检查下界,因为要和兄弟分区合起来看 |

索引扫描侧用 `min_parallel_index_scan_size`(默认 512kB = 64 页)独立算一遍,然后**取堆与索引的较小值**
——所以"表很大但走索引只碰几页"时,worker 数会按索引那一侧压下来。

### 3. 计划期 vs 运行期

`Workers Planned: 2` 只是计划。执行时如果拿不到后台进程
(受 `max_worker_processes`、`max_parallel_workers` 限制),**leader 会自己跑完**整个
Gather 以下的部分,就像 Gather 不存在一样。文档因此建议:如果经常发生,要么调大
`max_worker_processes`/`max_parallel_workers`,要么调小 `max_parallel_workers_per_gather`
以免规划出一个串行下更差的计划。

### 4. 代价模型为什么常常否决并行

```text
Gather 总代价 = parallel_setup_cost(1000) + 并行部分代价/(workers+1) + rows × parallel_tuple_cost(0.1)
```

1000 的启动代价等价于"顺序扫 1000 个页面"。所以小表上并行计划几乎必输;
只有**扫得多、吐得少**的查询(聚合、`LIKE '%x%'` 过滤)才划得来 —— 这也正是文档说的
"Queries that touch a large amount of data but return only a few rows to the user will
typically benefit most"。

### 5. 什么时候根本不会生成并行计划

官方文档 15.2 的四条硬约束(本 demo 的 `can_parallelize`):

| 条件 | 例子 |
| --- | --- |
| 写数据或锁行 | `UPDATE`、`INSERT ... SELECT`(但 `CREATE TABLE AS` / `REFRESH MATERIALIZED VIEW` 的 SELECT 部分例外) |
| 查询可能被挂起 | `DECLARE CURSOR`、PL/pgSQL 的 `FOR x IN query LOOP` |
| 用到 `PARALLEL UNSAFE` 函数 | **用户自定义函数默认是 UNSAFE** |
| 已在并行查询内 | 并行 worker 里再发 SQL,不会再并行 |

另外 `max_parallel_workers_per_gather = 0` 或单用户模式下也不会并行。

## 对比:三种"看起来并行了其实没有"

| 现象 | 真相 |
| --- | --- |
| 计划里出现 `Gather`,但 `Workers Launched: 0` | 运行时没抢到 worker,leader 独自跑完 |
| 计划里压根没有 `Gather` | 命中上面四条硬约束之一,或代价模型判输 |
| `Workers Planned` 很大但只跑了一小会儿 | 多数 worker 分不到数据块(块按"领一段再领一段"分配) |

## 环境与运行

- Python 3(仅标准库)、Go 1.22(仅标准库)

```bash
python python/main.py
python python/selfcheck_parallel.py   # 38 项断言
cd go && go run .
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/workers.py` | `log3_workers`、`compute_parallel_worker`、`parallel_plan_cost`、`can_parallelize` |
| `go/parallel.go` `go/main.go` | 同一模型的 Go 转写 |

## 性能边界与注意事项

- **本机不跑 PostgreSQL**:本 demo 复现的是**规划期公式**,不模拟实际执行与块分配。
- **公式算的是"想要几个",不是"拿到几个"**:上限与运行时进程池都会再压一刀。
- **别只调 `max_parallel_workers_per_gather`**:全局 `max_parallel_workers`(默认 8)与
  `max_worker_processes` 才是总闸,调大会挤掉其他后台任务。
- **并行聚合有两阶段**:`Partial Aggregate` → `Gather` → `Finalize Aggregate`;
  分组数接近输入行数时 Finalize 会成为瓶颈,规划器通常会自动放弃并行聚合。
- **并行不是免费的**:`parallel_tuple_cost=0.1` 意味着每行跨进程传递都要计价,
  吐出行数多的查询并行反而更慢。
- **索引并行扫描目前只支持 btree**。

## 参考资料

- PostgreSQL 18 官方文档《Parallel Query》15.1–15.4
  (Gather/Gather Merge、何时能用、并行计划、并行安全)
  —— <https://www.postgresql.org/docs/current/parallel-query.html>
  · <https://www.postgresql.org/docs/current/how-parallel-query-works.html>
  · <https://www.postgresql.org/docs/current/when-can-parallel-query-be-used.html>
  · <https://www.postgresql.org/docs/current/parallel-plans.html>
- PostgreSQL 源码 `src/backend/optimizer/path/allpaths.c`
  (`compute_parallel_worker` 的 log3 循环、`Min(..., max_workers)`、BASEREL 门槛)
  —— 经 `cdn.jsdelivr.net/gh/postgres/postgres@master/...` 实读
- PostgreSQL 18 官方文档 GUC:`min_parallel_table_scan_size=8MB`、
  `min_parallel_index_scan_size=512kB`、`parallel_setup_cost=1000`、`parallel_tuple_cost=0.1`
  (<https://www.postgresql.org/docs/current/runtime-config-query.html>);
  `max_parallel_workers=8`、`parallel_leader_participation=on`
  (<https://www.postgresql.org/docs/current/runtime-config-resource.html>)
