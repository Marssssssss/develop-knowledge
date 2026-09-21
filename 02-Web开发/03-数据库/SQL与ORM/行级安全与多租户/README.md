# 行级安全（RLS）与多租户

## 一、简介

多租户系统的第一道防线通常是「每条 SQL 都记得带 `WHERE tenant_id = ?`」。这道防线的问题在于：**只要有一次忘记就全盘失守**。PostgreSQL 的行级安全（Row Level Security）把过滤条件下推到数据库，让「忘记」在语法上不可能发生。

但 RLS 自己也有一堆反直觉的规则：

- 开了 `ENABLE ROW LEVEL SECURITY` 却**没建策略**，不是「不过滤」，而是**一行都看不到**；
- **表属主默认绕过 RLS** —— 而应用账号往往就是表属主，于是策略形同虚设；
- `USING` 与 `WITH CHECK` 是两个不同的方向，只写 `USING` 时后者隐含相同，很多人以为只写了前者就万事大吉；
- 多条策略是 **OR** 还是 **AND**，取决于它是 permissive 还是 restrictive。

本 demo 按 PostgreSQL 18《5.9. Row Security Policies》与官方源码 `rowsecurity.c` 做成可执行模型，33 条断言逐条钉死。

## 二、原理详解

### 2.1 两个方向：USING 与 WITH CHECK

| 子句 | 作用对象 | 适用命令 |
| --- | --- | --- |
| `USING (...)` | **已存在的行**（可见性） | SELECT / UPDATE / DELETE |
| `WITH CHECK (...)` | **被写出的新行** | INSERT / UPDATE |

官方规则：**只写 `USING` 时，`WITH CHECK` 隐含与 `USING` 相同**。所以

```sql
CREATE POLICY p ON accounts USING (manager = current_user);
```

这条策略同时保证了「看得到」和「只能写成自己名下」。想让两者不同（例如允许读出历史行但不允许改写成别人的），必须显式写 `WITH CHECK`。

`INSERT` 只看 `WITH CHECK` —— 因为还没有「已存在的行」可过滤；`UPDATE` 则两个都要：先用 `USING` 挑出能改的行，再对改完的新行校验 `WITH CHECK`。

违反 `WITH CHECK` 的后果是**整句报错**（`ERROR: new row violates WITH CHECK OPTION`），不是「静默少改一行」。

### 2.2 default-deny

官方 `add_security_quals` 的注释写得很直白：

> If there are no policies controlling access to the table, then **all access is prohibited** --- i.e., an implicit default-deny policy is used.

所以 `ALTER TABLE t ENABLE ROW LEVEL SECURITY;` 之后不建策略，SELECT 返回空、INSERT 直接报错。整表操作（`TRUNCATE`、`REFERENCES`）不受 RLS 约束。

反过来，**没有 ENABLE 的表上建再多策略也不起作用** —— 这是最容易漏的一步。

### 2.3 谁的豁免

| 角色 | 是否绕过 |
| --- | --- |
| superuser | 是 |
| 带 `BYPASSRLS` 属性的角色 | 是 |
| **表属主** | **默认是**（这就是多租户的头号漏洞） |
| 表属主 + `ALTER TABLE ... FORCE ROW LEVEL SECURITY` | 否 |

应用连接账号通常就是表属主，所以**多租户场景必须显式 `FORCE ROW LEVEL SECURITY`**，否则策略一行都不会生效（本 demo G5 断言）。

### 2.4 多策略的组合

官方：`When multiple policies apply to a given query, they are combined using either OR (for permissive policies, which are the default) or using AND (for restrictive policies)`。

组合方式是：

```
最终结果 = (permissive₁ OR permissive₂ OR ...) AND restrictive₁ AND restrictive₂ ...
```

一个例外：如果**只有** restrictive 策略，那么基础是「全部可见」，再被它们收紧。

`rowsecurity.c` 里就是这么实现的：

```c
rowsec_expr = makeBoolExpr(OR_EXPR, permissive_quals, -1);
```

restrictive 策略的典型用法是「先放开，再加一条硬约束」，例如管理员可以看到所有记录，但**必须来自本地 Unix socket**。

### 2.5 参照完整性绕过 RLS（隐蔽信道）

官方明确：唯一/主键/外键这类参照完整性检查**总是绕过** RLS。这带来一个副作用：即使某个租户看不见某行，插入同键值时得到的唯一约束冲突**会泄露该值已存在**。官方原文把这称为需要小心的 “covert channel”。

## 三、对比：三种租户隔离方案

| 方案 | 隔离强度 | 成本 | 典型问题 |
| --- | --- | --- | --- |
| 应用层 `WHERE tenant_id = ?` | 弱（漏一次就穿） | 低 | 忘记加条件、JOIN 时漏条件 |
| 每租户一个 schema | 强 | 高（迁移、连接池、元数据膨胀） | 上千租户后 catalog 压力大 |
| **RLS（shared schema + policy）** | 强 | 中 | 属主绕过、策略表达式里的函数每次求值、索引要带租户列 |

## 四、环境

- Python 3.8+（仅标准库）
- Go 1.21（无外部依赖）

## 五、运行方式

```bash
cd python && python selfcheck_rls.py    # 33 条断言
cd go     && go run .
```

## 六、关键代码

**组合：permissive 取 OR，restrictive 取 AND**（`python/main.py`）：

```python
if permissive:
    visible = any(_using(p, row, role) for p in permissive)
else:
    visible = True                      # 只有 restrictive 时先全放行
if visible:
    visible = all(_using(p, row, role) for p in restrictive)
```

**default-deny**（`python/main.py`）：

```python
policies = self._applicable(role, cmd)
if not policies:
    return []                           # 没有策略 = 禁止一切访问
```

## 七、性能边界

- 策略表达式会被**注入到查询计划里**，等于给每条 SQL 额外加一个 WHERE。表达式里调用 `current_user` 这类稳定函数没问题，但如果写成子查询（官方文档示例里就有 `USING (group_id <= (SELECT ...))`），就成了**每行一次相关子查询**，数据量一大必然慢。
- **RLS 不能替代索引**：`(tenant_id, ...)` 的复合索引仍然必须建，否则策略过滤等于全表扫描后再过滤。
- 策略的 `USING` 会被优化器下推，但 **`WITH CHECK` 无法下推**（它发生在写出时），批量导入时每行都要算一次。
- 分区表 + RLS 时，策略在**每个分区上各自求值**，分区数多会放大这部分开销。
- 多租户下建议让策略表达式尽量简单（直接比较 `tenant_id = current_setting('app.tenant')`），把租户上下文放在连接级 `SET` 上而不是每次传参。

## 八、注意事项与常见坑

1. **忘记 `ENABLE ROW LEVEL SECURITY`**：策略建了一堆却完全不生效，看起来「RLS 没用」。
2. **忘记 `FORCE ROW LEVEL SECURITY`**：应用账号是属主，于是完全绕过 —— 多租户最常见的失效原因。
3. **开了 RLS 但没建策略 = 全表不可见**，不是「不过滤」。
4. **`USING` 不等于 `WITH CHECK`**：只写 `USING` 时后者隐含相同，但显式写 `WITH CHECK` 时两者可以完全不同，别默认它们一致。
5. **违反 `WITH CHECK` 是整句报错**，不是跳过该行；批量写入要预先过滤。
6. **`FOR SELECT` 的策略不保护 `DELETE`**：命令必须匹配（或用 `ALL`）。
7. **角色要用 `TO` 指定或留空（PUBLIC）**；角色继承算数，成员也会命中。
8. **视图默认以属主权限执行**：想让视图也受 RLS 约束，需要 `security_invoker` 之类的设置，否则视图会「穿透」策略。
9. **唯一约束冲突会泄露存在性**（参照完整性绕过 RLS）。
10. **Go 版把租户上下文显式放进 Role 结构体**：Python 里可以运行时打补丁，Go 里必须声明 —— 这正好提醒我们上下文传递要显式设计。

## 九、参考资料（本轮实际读过）

- PostgreSQL 18 官方文档 — 5.9. Row Security Policies
  <https://www.postgresql.org/docs/18/ddl-rowsecurity.html>
- PostgreSQL 源码 `src/backend/rewrite/rowsecurity.c`（`add_security_quals`、默认拒绝、`OR_EXPR` 组合）
  <https://github.com/postgres/postgres/blob/master/src/backend/rewrite/rowsecurity.c>
