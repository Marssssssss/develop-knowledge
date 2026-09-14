# 成本优化器 CBO(Cost-Based Optimizer)

> SQL 查询优化的核心:从统计信息到代价估算到 join 顺序搜索。

## 一、简介

数据库收到一条 SQL 后,要决定"怎么执行"——用什么访问路径(顺序扫/索引扫)、用什么 join 算法(NLJ/Hash/Merge)、表的 join 顺序是什么。**Cost-Based Optimizer(CBO)** 通过对每个候选"执行计划"估算代价,挑最小的那个。本 demo 实现:
- **统计信息**:per-column NDV + null_frac + MCV 列表 + 等深直方图
- **选择性估算**:针对 `=`/`<`/`>`/BETWEEN 的 row-count 预测
- **代价模型**:PG 默认 `seq_page_cost=1.0` + `random_page_cost=4.0` + `cpu_tuple_cost=0.01` + `cpu_index_cost=0.005`
- **Join 顺序搜索**:≤ 8 张表用**动态规划**(Selinger 1979 System R),> 8 张表切换到 **GEQO 遗传算法**

## 二、原理详解

### 2.1 统计信息

PostgreSQL 通过 `ANALYZE` 采样表(默认 30000 行),把结果存到 `pg_statistic`(对外视图 `pg_stats`):

```
ColumnStats {
    n_distinct       // NDV, 多少不同值
    null_frac        // NULL 比例
    mcv [(value, freq)]   // Most Common Values 列表
    histogram [(bound, cum_freq)]  // 等深直方图
}
```

选择性 = P(predicate):
- `=` 命中 MCV → 直接用 MCV 频率
- `=` 未命中 → `1/NDV`(均匀假设)
- `<` / `>` → 落到直方图哪个桶 → 用累计频率估算
- `AND` → `P(A) * P(B)`(独立假设,实际常有 correlation 失真)

### 2.2 代价模型

```
cost(seq_scan)  = seq_page_cost * pages + cpu_tuple_cost * rows
                  = 1.0 * 2500 + 0.01 * 100000 = 3500

cost(idx_scan)  = random_page_cost * index_pages    (B树下降)
                + random_page_cost * heap_fetches   (随机 I/O)
                + cpu_index_cost * rows
                + cpu_tuple_cost * rows * sel
```

**关键比率**:`random_page_cost / seq_page_cost = 4.0` —— SSD 上应调到 ~1.1。

### 2.3 Join 算法代价

- **Nested-Loop Join**:`cost(outer) + |outer| * cost(inner)`,适合 outer 很小 + inner 有索引。
- **Hash Join**:`cost(build) + cost(probe) * 1.2`(hash 探测常数因子),适合大数据等值 join。
- **Merge Join**:两边已排序时最优,否则先 sort。

### 2.4 Join 顺序搜索(Selinger 1979)

n 张表 join,理论有 `n! * 2^(n-1)` 种 left-deep / bushy 树。系统 R 用动态规划从下往上:

```
level 1: cost({T_i}) = best access path for T_i
level 2: cost({T_i, T_j}) = min over (T_i ⋈ T_j) of [cost(rest) + cost(join)]
level 3: ...
...
```

时间复杂度 O(3^n),n=12 时仍可行。**PostgreSQL 默认 `geqo_threshold = 12`** 时切换为 GEQO(遗传算法):

```
population  = pool_size random permutations
fitness     = 1 / cost
crossover   = edge recombination / order crossover
mutation    = (omitted in PG implementation per docs)
loop        = for `geqo_generations` generations
```

`geqo.html` §61.3 提到 PG GEQO 仅用 crossover,无 mutation。

### 2.5 Cardinality Estimation 的阿喀琉斯之踵

代价模型的准确性完全取决于 cardinality estimation(每个算子输出多少行)。估算错误一个数量级就足以让优化器选错 join 算法。常见错误来源:
- 相关列(假定独立,但 `tenant_id=7 AND region='eu'` 比 `1/N × 1/N` 严苛得多)→ 用 **extended statistics**(CREATE STATISTICS)修复。
- 倾斜分布(1/NDV 假设均匀)。
- Outdated stats → ANALYZE。

## 三、对比矩阵

| 数据库 | 优化器 | Join 搜索 | Cardinality | ML/RL |
| --- | --- | --- | --- | --- |
| PostgreSQL | CBO | DP + GEQO | 直方图 + extended stats | 无 |
| MySQL | CBO (8.0+) | DP, heuristic | 直方图 | 无 |
| Oracle | CBO | DP + adaptive | histograms + adaptive | 无 |
| SQL Server | CBO | DP + memo | histograms | 无(LEO) |
| CockroachDB | CBO | DP | histograms | 无 |
| Huawei GaussDB | CBO | DP + cascades | histograms | 部分 |

## 四、运行方式

```bash
cd 07-数据存储/01-关系型/CostBasedOptimizer/
python cbo.py
# 输出:
#   users SeqScan total cost   = 3500.00
#   users country=CN IndexScan = 4325.10  (sel=0.20)
#   DP plan levels:
#     {0,1,2}  ⋈  ...
#   GEQO 10-table best order: ['t3', 't0', 't1', ...]

gcc -std=c11 cbo.c -o cbo -lm && ./cbo
go run cbo.go
```

## 五、关键代码

`cbo.py` 的核心:

```python
def best_join_order_dp(self, joins):
    rels = sorted({j[0] for j in joins} | {j[1] for j in joins})
    n = len(rels)
    cost = {}
    for r in rels:
        s = (rels.index(r),)
        cost[s] = (self.estimate_scan_cost(r)[1], (), r)
    for sz in range(2, n + 1):
        for combo in _all_subsets_of_size(n, sz):
            best = min(
                (cost[rest][0] + self.estimate_scan_cost(rels[last])[1],
                 rest, rels[last])
                for last in combo if (rest := tuple(set(combo) - {last})) in cost
            )
            cost[combo] = best
    return reconstruct(cost)
```

## 六、性能边界

- **DP 时间**:O(3^n),n=10 几毫秒,n=12 几十毫秒,n=15 已不现实。
- **GEQO 时间**:O(gens × pool_size × cost_per_permutation),典型 30~80 generations × 30~50 pool。
- **ANALYZE 采样**:默认 30000 行(~几 MB),时间通常秒级;大表非常慢但后台 autovacuum 自动跑。

## 七、注意事项与常见坑

1. **统计不准**:大表 `ALTER TABLE ALTER COLUMN SET STATISTICS 10000` 提升精度。
2. **Prepared statement + 通用计划**:PG 第 6 次执行后切换为 generic plan,可能选错(参见 `plan_cache_mode = force_custom_plan`)。
3. **join_collapse_limit**:默认 8,显式 join 顺序超过会强制用 FROM ... WHERE 顺序。
4. **CBO ≠ 万能**:DBA 仍需关注 schema 设计、索引选择、SQL 写法。

## 八、参考资料

实际读过的权威链接:

1. PostgreSQL 14 Genetic Query Optimizer:https://www.postgresql.org/docs/current/geqo.html  *(§61.3 GEQO 在 PG 中的实现)*
2. PostgreSQL 14 Planner Statistics:https://www.postgresql.org/docs/current/planner-stats.html  *(histograms + extended statistics)*
3. "The Query Planner | Internals for Interns":https://internals-for-interns.com/posts/postgres-query-planner/  *(cost units + EXPLAIN ANALYZE BUFFERS 实战)*
4. "PostgreSQL Join Optimization: Nested Loop, Hash, and Merge":https://mydba.dev/blog/postgres-join-optimization  *(3 种 join 对照 + row_estimate_inaccurate 启发式)*
5. "Database Query Optimizers and Planners":https://engineering.zooz.com/@k.hassan202077/database-query-optimizers-and-planners-690d5f417a46  *(CBO/RBO + 4 阶段 pipeline)*
6. "Database Query Optimization: Techniques and Execution Plans":https://databasesystemsauthority.com/database-query-optimization  *(现代 CBO 综述 + cardinality estimation 错误后果)*
7. Selinger et al. 1979 "Access Path Selection in a Relational DBMS":https://www.cs.umb.edu/~cs630/Slides/S09/selinger.pdf  *(System R DP join enumeration 原始论文)*