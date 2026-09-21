# 批量写入与 Upsert（PostgreSQL ON CONFLICT / SQLite UPSERT）

## 一、简介

「存在就更新、不存在就插入」是业务代码里最常见的写操作之一。听起来简单，但落到 SQL 上有几个反复踩的坑：

- 一条批量 INSERT 里**两行命中同一条记录**，PostgreSQL 直接报错而不是「后到的覆盖先到的」；
- `ON CONFLICT DO UPDATE ... WHERE` 里的 WHERE **不满足时行照样被锁**，而且 `RETURNING` 里什么也拿不到;
- SQLite 的 `REPLACE` 和 `ON CONFLICT DO UPDATE` 完全不是一回事：前者是「先删后插」，会顺手触发 DELETE 触发器和外键的 `ON DELETE`。

本 demo 把 PostgreSQL 18《INSERT》的 ON CONFLICT 一节与 SQLite《UPSERT》文档做成可执行模型，35 条断言逐条钉死语义。

## 二、原理详解

### 2.1 arbiter index 与唯一索引推断

`conflict_target` 的作用是选出一个或多个 **arbiter（仲裁）索引**。官方的推断规则是：

> All table_name unique indexes that, **without regard to order**, contain **exactly** the conflict_target-specified columns/expressions are inferred.

- 列集合必须**完全相同**（多一列、少一列都不算）；
- **与书写顺序无关**：`ON CONFLICT (b, a)` 能命中 `(a, b)` 上的复合唯一索引；
- **非唯一索引不能当 arbiter**；
- 带 `index_predicate` 时，**非部分索引也能被推断命中**（这是官方明说的，容易想反）；
- 推断失败**直接报错**，不会退化成「忽略这个 ON CONFLICT」。

`DO UPDATE` **必须**给 conflict_target；`DO NOTHING` 可以省略，省略后对**所有**可用唯一约束/索引生效。

### 2.2 excluded 与求值顺序

`DO UPDATE SET` 与 `WHERE` 里能同时访问两个行：

| 名字 | 含义 |
| --- | --- |
| 表名（或别名） | 库里**已存在**的那一行 |
| `excluded` | **拟插入**的那一行（且已反映所有 BEFORE INSERT 触发器的效果） |

求值顺序是：先识别冲突 → 再算 SET → **最后**算 WHERE。所以：

- WHERE 不满足时，行**不被更新**；
- 但**仍然被锁**（官方：*all rows will be locked*）；
- `RETURNING` **不返回**这种行（官方：*Only rows that were successfully inserted or updated will be returned*）。

### 2.3 cardinality violation：deterministic 的代价

> INSERT with an ON CONFLICT DO UPDATE clause is a "deterministic" statement. This means that the command will not be allowed to affect any single existing row more than once.

批量写入时如果 VALUES 里有两行命中同一个已有行，**整条语句报 cardinality violation**。这不是 bug，是官方为了避免「结果依赖行顺序」而做的限制。`DO NOTHING` 不受此限制。

### 2.4 权限与边界

- `INSERT` 权限必备；`DO UPDATE` 额外需要 `UPDATE` 权限；
- **任何形式的 ON CONFLICT 都要求被读到列的 `SELECT` 权限**（包括 conflict_target 里的列、DO UPDATE 表达式与 WHERE 里读到的列）；
- 分区表上 `ON CONFLICT DO UPDATE` **不允许把冲突行的分区键改到另一个分区**；
- `CREATE INDEX CONCURRENTLY` 跑在同表唯一索引上时，同期的 `INSERT ... ON CONFLICT` 可能**意外报唯一冲突**；
- 想要更标准的写法，官方指向 `MERGE`。

### 2.5 SQLite 的差别

| 行为 | PostgreSQL | SQLite UPSERT |
| --- | --- | --- |
| conflict target | DO UPDATE 必填 | 可省略，省略则任一唯一约束触发 |
| 作用范围 | 冲突的那一行 | 同样只作用于冲突行，**不需要**额外加 WHERE 限定 |
| DO UPDATE 内的冲突处理 | 可带 `OR ...` | **恒为 ABORT**（哪怕外层写了 `INSERT OR REPLACE`） |
| 解析 | — | `INSERT ... SELECT` 后紧跟 `ON CONFLICT` 有**解析歧义**，必须补一个 `WHERE`（通常写 `WHERE true`） |
| 「替换」语义 | `ON CONFLICT DO UPDATE` | `REPLACE` 是**先删后插**，会触发 DELETE 触发器与外键 ON DELETE |

## 三、对比：三种「存在即更新」的写法

| 写法 | 原子性 | 触发 DELETE 触发器 / 外键 ON DELETE | 批量同键 | 备注 |
| --- | --- | --- | --- | --- |
| `SELECT` 再 `INSERT/UPDATE`（应用层判断） | 否（有 TOCTOU 竞态） | 否 | 依代码 | 高并发下会撞唯一约束 |
| `INSERT ... ON CONFLICT DO UPDATE` | 是（官方保证二选一） | 否 | 报 cardinality violation | 需要唯一索引 |
| `REPLACE INTO`（SQLite/MySQL） | 是 | **是** | 后者覆盖前者 | 会丢掉未列出的列、影响自增与外键 |

## 四、环境

- Python 3.8+（仅标准库）
- Go 1.21（无外部依赖）

## 五、运行方式

```bash
cd python && python selfcheck_upsert.py    # 35 条断言
cd go     && go run .
```

## 六、关键代码

**推断：列集合相同、与顺序无关**（`python/main.py`）：

```python
def infer_arbiters(self, target, index_predicate=None, ...):
    want = set(target)
    out = [idx for idx in self.indexes
           if idx.unique and set(idx.cols) == want]
    if not out:
        raise NoUniqueIndexError(...)   # 推断失败是硬错误
    return out
```

**WHERE 最后求值、不满足仍锁行**（`python/main.py`）：

```python
if self.where is not None and not self.where(conflicting, proposed):
    locked_not_updated.append(conflicting)   # 行被锁但没改
    continue
```

## 七、性能边界

- 批量合并的收益主要来自**减少往返**：`n` 行按 `batch_size` 合并后语句数是 `ceil(n / batch_size)`（本 demo G1–G3 断言）。但单条语句过大又会撞上参数上限与 WAL 单次写入放大，工程上常见区间是 100~1000 行/批。
- 每条 `ON CONFLICT` 都要先做一次**索引探查**。批量里冲突比例越高，越接近「N 次索引查找 + N 次更新」，与逐条执行相比省下的只有往返。
- `DO NOTHING` 比 `DO UPDATE` 便宜：前者不写堆元组、不产生新版本，也就**不做 HOT 更新判断**。
- 长事务里批量 upsert 会大量产生死元组（`DO UPDATE` 本质是 UPDATE），需要关注 autovacuum 是否能跟上。
- 官方明确：`ON CONFLICT DO UPDATE` 在**高并发**下虽然保证原子二选一，但仍推荐外面套重试循环。

## 八、注意事项与常见坑

1. **同批两行命中同一条记录会报错**，不是「后到覆盖」—— 批量导入前先按冲突键去重。
2. **WHERE 不满足的行仍然被锁**，容易在批量任务里造成锁等待却被误判为「没更新成功」。
3. **`RETURNING` 拿不到被跳过的行**：想拿已有行得再查一次，或用 CTE（`WITH ... INSERT ... RETURNING`）。
4. **`excluded` 是拟插入值**，且已经过 BEFORE INSERT 触发器；在触发器里改了值却指望 `excluded` 是原始输入，是典型误解。
5. **`DO UPDATE` 需要 UPDATE 权限 + 被读列的 SELECT 权限**，只读从库上跑 upsert 会以权限错误失败。
6. **SQLite 里 `REPLACE` ≠ upsert**：它会把未列出的列写成默认值，还会触发删除侧的外键动作。
7. **`INSERT ... SELECT ... ON CONFLICT` 记得补 `WHERE true`**，否则 SQLite 解析器可能把 `ON CONFLICT` 当成表约束。
8. **Go 版没有 `id(obj)`**：行标识改用主键值拼接的字符串，语义等价但**只在本 demo 的单表模型内成立**。

## 九、参考资料（本轮实际读过）

- PostgreSQL 18 官方文档 — INSERT（ON CONFLICT Clause）
  <https://www.postgresql.org/docs/18/sql-insert.html>
- SQLite 官方文档 — UPSERT
  <https://www.sqlite.org/lang_upsert.html>
- MySQL 8.0 官方文档 — INSERT ... ON DUPLICATE KEY UPDATE
  <https://dev.mysql.com/doc/refman/8.0/en/insert-on-duplicate.html>
  （本轮 dev.mysql.com 返回 “Technical Difficulties”，三条通道均未取到正文，故本 demo 的 MySQL 侧只作文献著录、未参与断言）
