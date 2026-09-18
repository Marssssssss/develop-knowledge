# N+1 查询与预取策略

## 简介

- **N+1 查询问题**：取回 N 个父对象用 1 条 SQL，随后逐个访问它们的懒加载关联属性，又各发 1 条 SQL —— 总共 **1 + N 条**。SQLAlchemy 文档的定义原话是 "for any N objects loaded, accessing their lazy-loaded attributes means there will be N+1 SELECT statements emitted"。
- 关键概念：
  - **懒加载（lazy / select）**：`relationship.lazy` 的**默认值就是 `"select"`**，属性首次访问时才发 SELECT；
  - **预取（eager loading）**：把关联行在基查询里一次取回，策略有 `selectin` / `joined` / `subquery` / `immediate` 等；
  - **Select IN 加载**：第二条 SQL 把父对象主键拼进 `IN` 子句，文档明确 "up to 500 parent primary key values at a time"；
  - **身份映射（identity map）**：同一主键只保留一份对象实例，因此关联对象已在会话中时懒加载**不需要**发 SQL；
  - **raiseload**：用异常替掉懒加载，把"意外触发的 N+1"变成显式错误。
- 历史背景：N+1 由 ORM 的"对象图导航"与"每次导航一条查询"的默认实现共同造成；GORM 在 2.x 直接**移除**了懒加载，只保留 `Preload` / `Joins` 两种显式预取，是另一种取舍。

## 原理详解

1. 基查询只取父表：`SELECT users.* FROM users`（1 条）。
2. 访问 `user.addresses` 时，ORM 发现该集合**未加载**，按当前策略决定动作：`select` 发 `WHERE user_id = ?`；`selectin` 由批量加载器接手；`raise` 直接抛 `InvalidRequestError`。
3. 对 N 个父对象逐个访问，就得到 N 条逐主键查询 → **1 + N**。把 N 从 5 放大到 5000，语句数线性增长，这就是 N+1 的杀伤力。
4. `selectin` 把第 2 步合并：先取全部父主键，再发 `WHERE user_id IN (?, ?, ...)`，每条 SQL **最多 500 个主键**（超出则分批），语句数 = `1 + ceil(N/500)`。
5. `joined` 用 `LEFT OUTER JOIN` 把关联行并进同一结果集，语句数=1；代价是**行数被放大**（一个父对象有 k 条子行就出现 k 行），所以对集合关联必须再调 `Result.unique()` 按主键去重，否则 ORM 报错。
6. `subquery` 用第二条 SQL，把**原查询原样嵌进子查询**再 JOIN 关联表，同样只有 2 条。
7. `raiseload` 不改变语句数，只是把"该加载却没预取"的路径变成异常；文档同时注明它在 **unit of work flush 流程中不生效**。

### 语句数对照（本 demo 实测，5 个父对象、集合关联）

| 策略 | 发出的 SQL | 语句数 | 备注 |
| --- | --- | --- | --- |
| `select`（默认） | `SELECT users.*` + 5×`WHERE user_id = ?` | **6** | N+1 |
| `selectin` | `SELECT users.*` + 1×`WHERE user_id IN (...)` | **2** | 500/批 |
| `subquery` | `SELECT users.*` + 1×子查询 JOIN | **2** | |
| `joined` | 1×`LEFT OUTER JOIN` | **1** | 必须 `unique()` |

## 环境准备

- Python 3.9+（仅标准库，无第三方依赖）；Go 1.18+（可选对照实现）。

## 运行方式

### Python

```bash
python3 python/n_plus_one.py
```

### Go

```bash
cd go && go run n_plus_one.go
```

## 关键代码片段

```python
# 懒加载：属性访问时才发 SQL —— N+1 的唯一来源
def _lazy(self, owner, rel):
    if (id(owner), rel) in self._loaded:        # 命中已加载/身份映射
        return self._loaded[(id(owner), rel)]
    strategy = self._options.get(rel, "select")
    if strategy in ("raise", "raise_on_sql"):   # raiseload：异常代替查询
        raise InvalidRequestError(...)
    self.db.emit(f"SELECT {table}.* FROM {table} WHERE {fk} = {owner['id']}")
```

```python
# selectin：主键拼进 IN，每批最多 500 个
for i in range(0, len(ids), SELECT_IN_BATCH):        # SELECT_IN_BATCH = 500
    chunk = ids[i:i + SELECT_IN_BATCH]
    self.db.emit(f"SELECT {table}.* FROM {table} WHERE {fk} IN ({placeholders})")
```

```go
// GORM：Preload 走独立第二条 SQL，Joins 走 LEFT JOIN（后者只支持单值关联）
func (d *db) Joins(assoc string) (*db, error) {
	if m := tableOf(assoc); m.hasMany {
		return d, fmt.Errorf("join preload works with one-to-one relation (has one / belongs to), got has-many: %s", m.table)
	}
	...
}
```

## 性能与边界

- 语句数：`select` = 1+N；`selectin` = 1+ceil(N/500)；`subquery` = 2；`joined` = 1。**结果集内容完全相同**，文档原话："only the number of SQL statements required to fully load related objects and collections changes"。
- 网络往返才是 N+1 的真实成本：语句数从 6 降到 2 在小数据量下收益不明显，但当 N 是 5000（排行榜、列表页）时，往返次数从 5001 降到 11。
- `joined` 的行放大在 `LIMIT`/`OFFSET`/`DISTINCT` 场景会污染语义：文档说明此时 SQLAlchemy 会把语句包进子查询，再把 eager join 施加到子查询上。
- 分批常数 500 是**文档写死的实现细节**，不是可调参数；超大批量时它把单条 SQL 的长度与解析开销限制住。
- GORM 侧：`clause.Associations` 只覆盖**第一层**，且 "won't preload nested associations"；嵌套路径 `"Orders.OrderItems.Product"` 每层各发一条 SQL，因此语句数随**路径深度**线性增长，与行数无关。

## 注意事项与常见坑

- **默认即懒加载**：不写 `lazy=` 就是 `select`，所以在循环里访问关联属性必然 N+1；开发期建议对关键关联配 `raiseload` 或 `lazy="raise_on_sql"` 让它在测试里直接炸掉。
- **`joined` 忘了 `unique()`**：集合关联用 joined 而不调 `Result.unique()` 会直接抛错（本 demo 断言了这一条），不是静默返回重复行。
- **`joins` 不是 `preload` 的同义词**：GORM 官方 NOTE 明确 Join Preload 只对 `has one` / `belongs to` 生效；对 `has many` 用它取不回完整集合（Go 版实现按此约束报错）。
- **`selectin` 的 IN 有上限**：父对象超过 500 个时分批，语句数是 `ceil(N/500)` 而不是常数 2 —— 别在压测里看到"有时 3 条"就以为出了 bug。
- **身份映射会让语句数“看起来不规律”**：关联对象若已在会话中（同主键），懒加载**不发 SQL**，所以同一段代码在不同调用顺序下语句数可能不同；统计语句数要用固定的会话边界。
- **`raiseload` 不拦 flush**：文档写明它在 unit of work 的 flush 流程中不生效，不要指望它兜住所有隐式加载。

## 参考资料（实际阅读过的权威来源）

- [SQLAlchemy 2.0 — Relationship Loading Techniques](https://docs.sqlalchemy.org/en/20/orm/queryguide/relationships.html) — `lazy` 默认值 `"select"`、N+1 定义、selectin 500 分批、joined 必须 `unique()`、raiseload 不作用于 flush。
- [GORM — Preloading (Eager Loading)](https://gorm.io/docs/preload.html) — `Preload` 独立 SQL vs `Joins` LEFT JOIN、Join Preload 仅支持单值关联、`clause.Associations` 不递归、嵌套预加载逐层发 SQL。
