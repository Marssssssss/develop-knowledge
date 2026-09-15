# 在线 DDL(INSTANT / INPLACE / COPY + gh-ost)

## 简介

给亿级大表改结构而不停服,靠的是**在线 DDL**:MySQL 8.0 提供三种算法,按代价 `INSTANT < INPLACE < COPY`;gh-ost 则绕开 MySQL 自身 DDL,用 **binlog 流 + ghost 表**完成变更。本 demo 模拟三算法行为差异、MDL 独占窗口、INSTANT 行版本上限、gh-ost 的 copy→binlog 回放→原子 cut-over 三步。

关键概念:数据字典元数据 / 行版本(row versions,上限 64)/ row log / MDL(元数据锁)/ ghost 表 / cut-over。

## 原理详解

**三算法**(dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-operations.html):

| 维度 | INSTANT | INPLACE | COPY |
| --- | --- | --- | --- |
| 做什么 | 只改数据字典元数据 | 引擎内就地执行 | 服务器层临时表全量复制后换名 |
| 重建表 | 否 | 视操作(加二级索引否;改主键/行格式是) | 总是 |
| 并发 DML | ✓ | 多数 ✓(`LOCK=NONE`) | ✗(共享锁,只读) |
| 典型操作 | 加列(8.0.12+ 默认,8.0.29+ 任意位置)、删列、改名 | 加二级索引、加主键、扩 VARCHAR | 删主键、改列类型、缩 VARCHAR |
| 代价 | 秒级 | 中 | 全表复制 |

- **INPLACE 优于 COPY 的三个官方理由**(即使都重建表):无服务器层临时表 → 无额外 undo/redo;二级索引条目**预排序**;不用 change buffer。
- **MDL 独占窗口**:Online DDL 分 prepare → 执行 → commit 三阶段,**独占 MDL 只出现在 prepare 开始与 commit 收尾两端**;执行阶段的并发 DML 记入 **row log**,在 commit 阶段应用(`CREATE INDEX` 要"等所有访问该表的事务结束"后才算完成)。
- **INSTANT 行版本**:每次瞬时加/删列创建一个新行版本,`TOTAL_ROW_VERSIONS` 计数,**上限 64**,超限报 `ERROR 4092` 并要求改用 COPY/INPLACE;重建型 ALTER/OPTIMIZE 会把计数清零。
- 语法:`ALGORITHM={INSTANT|INPLACE|COPY|DEFAULT}, LOCK={NONE|SHARED|EXCLUSIVE|DEFAULT}`;显式指定的算法不被支持时**直接报错**而非静默降级。

**gh-ost**(github.com/github/gh-ost,与 pt-osc 对比):

1. 建 ghost 表(原表 likeness + 新结构),增量拷贝行;
2. **订阅主库 binlog 流**(而非 pt-osc 的触发器),把 copy 期间的 `INSERT/UPDATE/DELETE` 异步回放到 ghost 表;
3. copy 完成 + binlog 追平后,**原子 cut-over** 换名。
- 官方强调:无触发器 → 无触发器的锁与性能风险;可**真正暂停**(throttle 时主库完全回到原生负载);可先在从库上测试(`--test-on-replica`)。

## 环境

- Python ≥ 3.8;Go ≥ 1.21(静态审查)

## 运行方式

```bash
python3 python/main.py   # 输出 ALL 5 ... ASSERTIONS PASSED
go run go/main.go
```

## 关键代码片段

```python
# INSTANT:元数据 + 行版本,不碰数据行
self.schema.columns.append(name)
self.schema.row_versions += 1

# INPLACE:执行期 DML → row log,commit 阶段应用
row_log.extend(concurrent_dml)   # execute_phase
return list(row_log)              # commit_phase

# gh-ost:binlog 流回放(代替触发器)
elif kind == "U":
    for r in ghost.rows:
        if r.get("id") == pk: r.update(change)
```

## 性能与边界

- INSTANT 加列到 64 个行版本后必须重建表清零(手册 ERROR 4092)。
- COPY 期间写阻塞;主键变更即使 INPLACE 也要重建聚簇索引 + 全部二级索引。
- gh-ost 需要额外磁盘(原表 + ghost 双份)+ binlog 保留时间覆盖 copy 全程。

## 注意事项与常见坑

- **MDL 死等**:commit 阶段需要独占 MDL,若此时有长事务/慢查询占着该表,DDL 反过来阻塞后续所有新查询(D = "元数据锁雪崩"),大表变更务必先 `kill` 长事务。
- `ALGORITHM=INSTANT` 用在不支持的操作上会**直接报错**(这是特性:避免静默退化成 COPY)。
- gh-ost 在 SBR(statement 格式)下无法工作,必须 ROW(README 明确要求)。
- gh-ost 官方警告:cut-over 后旧主库若崩溃 failover,不应把含未同步事务的旧实例复用为 source。

## 参考资料(实际阅读过的权威来源)

- [MySQL 8.0 Manual: 17.12.1 Online DDL Operations](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-operations.html) — INSTANT/INPLACE/COPY 支持矩阵、INPLACE 三优势、行版本 64 上限、ALGORITHM/LOCK 子句
- [gh-ost: GitHub's Online Schema-migration Tool for MySQL](https://github.com/github/gh-ost) — binlog 流(非触发器)方案、ghost 表、cut-over、throttle/暂停语义
- [MySQL 8.0 Manual: 19.2.1 Replication Formats](https://dev.mysql.com/doc/refman/8.0/en/replication-formats.html) — gh-ost 依赖的 ROW 格式背景
