# 选择率与基数估计(Row Estimation)

> 规划器选错计划的根源几乎都在**行数估错**。本 demo 把 PostgreSQL 官方
> 《69.1 Row Estimation Examples》的**五个带算例的公式**逐条建模,与文档数字对拍——
> 这些公式就是 `pg_stats` 里 MCV/直方图/n_distinct 被用起来的确切方式。

## 1. 基数起点:pg_class 的缩放机制

`relpages/reltuples` 来自上次 VACUUM/ANALYZE;规划器取**当前真实页数**(廉价操作),
不一致时按比例缩放 `reltuples`——统计过期时估算仍会部分自修正。

## 2. 五条选择率公式(官方算例)

| 场景 | 公式 | 官方数字 |
| --- | --- | --- |
| 范围·唯一列 | `(整桶数 + 桶内线性分数)/桶数` | unique1<1000 → 0.100697 → 1007 行 |
| 等值·不在 MCV | `(1-Σmcv_freq)/(n_distinct-n_mcv)` | stringu1='xxx' → 0.0014559 → 15 行 |
| 范围·有 MCV | `MCV 精确部分 + 直方图部分×直方图占比` | <'IAAAAA' → 0.307669 → 3077 行 |
| 多条件 | 独立假设:选择率**相乘** | 0.0001466 → 1 行 |
| 等值连接(双唯一) | `(1-nf₁)(1-nf₂)/max(n₁,n₂)` | 0.0001,连接行数=笛卡尔积×sel=50 |

关键细节:

- **直方图不含 MCV 占比的群体**:非唯一列先对 MCV 逐值精确判定,
  剩余群体再用直方图估,最后按占比加权合并——常见值的命中是**精确值**;
- `n_distinct = -1` 表示"全唯一"(pg_stats 里最常见于唯一键);
- 连接行数用**约束后的外层基数 × 内层全量**做笛卡尔积——不是 EXPLAIN 里
  内层 Index Scan 的 rows=1 相乘(计划选择前连接大小已先估好);
- 等值不在 MCV 的均摊假设:剩余群体在其余 distinct 值间**均匀分布**。

## 3. 误差为什么放大

两个 1% 的独立条件相乘 = 0.01%——**误差按选择率平方级放大**。
多列强相关(如 city/state)时独立假设严重失真,这正是
`CREATE STATISTICS`(多列统计)存在的理由;估错 100 倍行数,
seq scan ↔ index scan 的代价对比就会翻转。

## 自检

`python selestim_check.py` —— 8 项断言,全部与官方文档算例数字对拍
(1007/50/15/3077/1/50 行),外加 relpages 缩放与误差平方级放大演示。
Go 侧 `selestim.go` 为同语义复刻(静态审查)。

## 参考资料(实读)

- [PostgreSQL 18 — 69.1. Row Estimation Examples](https://www.postgresql.org/docs/current/row-estimation-examples.html)(五公式与全部算例出处)
- 本目录 README(2026-09-21 已读 Using EXPLAIN 的 cost 口径)
