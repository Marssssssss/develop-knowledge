# 物化视图与增量刷新(Materialized View + REFRESH CONCURRENTLY)

## 简介

物化视图把视图的查询结果**像表一样持久化**,读时直接扫描结果而不再执行查询 —— 用"数据可能陈旧"换取聚合查询的数量级加速(PostgreSQL 官方文档给出的示例:外部表查询 188ms → 物化视图索引扫描 0.117ms)。本 demo 模拟:创建即物化、全量 REFRESH 的阻塞语义、CONCURRENTLY 的 diff 增量与唯一索引前置条件。

关键概念:物化(持久化)/ 视图定义存储 / `REFRESH MATERIALIZED VIEW` / `CONCURRENTLY` / 唯一索引 / `relispopulated`。

## 原理详解

**物化 vs 视图 vs 表**(postgresql.org/docs/current/rules-materializedviews.html):

| | 视图 | 物化视图 | 表 |
| --- | --- | --- | --- |
| 查询时 | 每次重算 | 直接读持久化数据 | 直接读 |
| 可直接 DML | ✗ | ✗(不能随后更新) | ✓ |
| 建索引 | ✗ | ✓ | ✓ |
| 数据新鲜度 | 实时 | 取决于 REFRESH 时机 | 实时 |

- `CREATE MATERIALIZED VIEW` = `CREATE TABLE ... AS SELECT` + **把定义(查询)像视图一样存进系统目录**(解析器眼中它就是一个 relation);`pg_class.relispopulated` 标记是否已填充。
- 由于可以**建索引**,物化视图上还能做 index-only scan,进一步放大收益(官方 file_fdw 示例:EXPLAIN 里 Foreign Scan → Index Only Scan)。

**两种刷新语义**:

1. **全量 `REFRESH`**:重新执行定义查询生成全新数据后整体替换 —— 期间持独占锁,**阻塞所有 SELECT**。
2. **`REFRESH ... CONCURRENTLY`**:
   - **前置条件**:物化视图上必须有**唯一索引**(无则直接报错,本 demo 断言 3 复现)。
   - 流程:在**临时版本**上算出结果 → 与现有数据做 **diff**(按唯一键等值比对)→ 只把变化行应用到正式数据 → 读全程不阻塞。
   - 代价:比全量慢(要做比对),换来"刷新期间读者不受影响"。

## 对比/选型

| 维度 | 全量 REFRESH | CONCURRENTLY |
| --- | --- | --- |
| 刷新期间 SELECT | 阻塞 | 不阻塞 |
| 前置条件 | 无 | 唯一索引 |
| 刷新成本 | 重算 | 重算 + diff |
| 适用 | 维护窗口、小视图 | 7×24 在线大视图 |

## 环境

- Python ≥ 3.8;Go ≥ 1.21(静态审查)

## 运行方式

```bash
python3 python/main.py   # 输出 ALL 5 ... ASSERTIONS PASSED
go run go/main.go
```

## 关键代码片段

```python
# CONCURRENTLY 前置条件:唯一索引
if self.unique_index is None:
    raise RuntimeError('... CONCURRENTLY requires a UNIQUE index')

# diff 增量(真实 PG 同样按唯一键做等值比对)
new_keys, old_keys = set(tmp), set(self.data)
self._deleted  = old_keys - new_keys
self._inserted = new_keys - old_keys
```

## 性能与边界

- 官方文档实测:file_fdw 远程访问 188ms vs 物化视图 0.117ms(1600×);fdw 无索引,物化视图可建 gist/trgm 索引再提速(1431ms → 198ms)。
- CONCURRENTLY 的 diff 需要物化视图**能被唯一键定位**;无唯一键的视图只能全量。
- 数据不会自动更新 —— 需要外部调度(cron/事件)触发 REFRESH。

## 注意事项与常见坑

- 全量 REFRESH 忘了维护窗口 → 刷新期间前端全部超时(本 demo 断言 2 用锁模拟该阻塞)。
- CONCURRENTLY 报错 `"...must have a unique index"` 最常见于手滑把索引建在底层表上而不是物化视图上。
- 刷新失败(如临时空间不足)会让 `relispopulated = false`,查询会直接报错而非读旧数据。
- 物化视图的统计信息(ANALYZE)不会随 REFRESH 自动更新,老统计可能误导优化器。

## 参考资料(实际阅读过的权威来源)

- [PostgreSQL Docs: 39.3 Materialized Views](https://www.postgresql.org/docs/current/rules-materializedviews.html) — 与表/视图的差异、定义存储方式、官方 file_fdw 性能示例(EXPLAIN 数字)
