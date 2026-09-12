# SQL 注入与预编译查询(Parameterised Query)

## 简介

SQL 注入是 OWASP Top 10 **A03 Injection**(2021)/合并后仍属注入类榜首(CWE-89)。成因是**数据与代码边界模糊**:开发者把用户输入直接拼入 SQL 字符串,数据库解析器无法区分哪部分是"命令"哪部分是"数据",攻击者借此窃取/篡改/删除数据,极端情况下获得 OS shell(MS-SQL `xp_cmdshell`、PostgreSQL `COPY ... FROM PROGRAM`)。**首选防御**是 OWASP 推荐的**参数化查询**(Prepared Statements):数据库协议层把"语句结构"和"数据值"分开传输,**即便数据中含 SQL 关键字也只会被当作字符串字面量**。

## 关键概念清单

| 术语 | 一句话 |
| --- | --- |
| **SQL Injection** | 用户输入改变 SQL 语句语义,执行非预期查询 |
| **Prepared Statement** | SQL 语句模板预编译,参数占位符 `?`/`$1`/`@x` 单独绑定 |
| **Parameterised Query** | 同上,OWASP 首选防御 |
| **Stored Procedure 风险** | 若 SP 内部仍拼接用户输入,则失效(PL/SQL `EXECUTE IMMEDIATE`) |
| **Second-order SQLi** | 攻击载荷先入库(经正确转义),再次读出拼入查询时触发 |
| **Boolean/Time-based Blind SQLi** | 不直接返回数据,用 `AND 1=1` 条件差异 / `SLEEP(5)` 推断 |
| **UNION-based** | `UNION SELECT` 拼接另一查询泄露字段 |
| **Stacked Queries** | `;DROP TABLE users;` 多语句执行(MySQL 默认禁用,MS-SQL 默认允许) |
| **Allow-list Validation** | 表名/列名/排序方向等"无法绑定"位置的兜底 |

## 历史背景

- 1998 年 Phrack 49 "NT Web Technology Vulnerabilities" 是最早公开讨论之一
- 1999 年 Allaire ColdFusion 漏洞引发大规模网站被黑(Rain Forest Puppy)
- 2004 年 *SQL Injection Attacks and Defense* (Syngress) 出版
- 2007 年 MS-SQL 注入(Asprox botnet)用 `CAST`/`CONVERT` 编码绕过签名
- 2011 年 OWASP 把 SQLi 列独立类别,2017/2021 仍居 A03 首位
- 现代 ORM(Hibernate, SQLAlchemy, Django ORM)**默认参数化**,仅在 raw SQL/字符串拼接时失效

## 原理详解

### 攻击向量

```
应用代码(不安全):
  String sql = "SELECT * FROM users WHERE name='" + req.getParameter("name") + "'";
  //                                          ↑ 用户可控

攻击载荷:
  name = ' OR '1'='1
  → SQL: SELECT * FROM users WHERE name='' OR '1'='1'   ← WHERE 永远为真
  → 返回全表所有用户

进阶(UNION):
  name = ' UNION SELECT password_hash FROM users-- 
  → 拼接查询泄露密码字段

时间盲注:
  name = ' OR IF(SUBSTRING(password,1,1)='a', SLEEP(5), 0)-- 
  → 通过响应时间逐字符爆破
```

### 数据库占位符差异(迁移时易踩坑)

| 数据库 | 占位符 | 命名参数 |
| --- | --- | --- |
| MySQL | `?` | ❌ 不支持 |
| PostgreSQL | `$1, $2...` | ❌ (libpq 高阶接口除外) |
| SQLite | `?, ?` | `:name` / `@name` / `$name` |
| SQL Server | `@name` | `@name` |
| Oracle | `:name` | `:name` |
| DB2 | `?` | ❌ 罕用 |

### Parameterised Query 的本质

```
不安全(字符串拼接):
  客户端: SELECT * FROM users WHERE name='alice' OR '1'='1'
  服务端: 把整串发给 DB 解析器 → DB 把 OR '1'='1' 当作 SQL 语法

参数化(协议层分离):
  Step 1: 客户端: PREPARE sel := SELECT * FROM users WHERE name=?
  Step 2: 服务端返回 PREPARE OK
  Step 3: 客户端: EXECUTE sel USING 'alice' OR \'1\'=\'1'   ← 数据永远当作字符串字面量
```

关键:**MySQL 协议层**允许二进制协议(Prepared Statement)把数据用单独 frame 发送,DB 解析器不重新词法分析数据。

### 跨语言对照(OWASP cheat sheet 摘要)

| 语言 | 推荐用法 |
| --- | --- |
| Java JDBC | `PreparedStatement.setString(1, userInput)` |
| Python sqlite3 | `cur.execute("SELECT ... WHERE name=?", (userInput,))` |
| Python psycopg2 | `cur.execute("SELECT ... WHERE name=%s", (userInput,))` |
| Go database/sql | `db.Query("SELECT ... WHERE name=?", userInput)` |
| Node.js pg | `client.query("SELECT ... WHERE name=$1", [userInput])` |
| Node.js mysql2 | `connection.execute("SELECT ... WHERE name=?", [userInput])` |
| PHP PDO | `$stmt = $pdo->prepare("... WHERE name=?"); $stmt->execute([$userInput])` |
| .NET | `command.Parameters.Add(new OleDbParameter("name", userInput))` |
| Ruby PG | `conn.exec_params("... WHERE name=$1", [userInput])` |
| Hibernate HQL | `query.setParameter("name", userInput)`(命名参数) |
| Django ORM | `User.objects.filter(name=userInput)`(自动参数化) |

### 兜底:Allow-list 验证(用于表/列名等无法绑定的场景)

```python
# 表名不能用占位符,必须白名单
ALLOWED_TABLES = {'users', 'orders', 'products'}
table = user_input
if table not in ALLOWED_TABLES:
    abort(400, 'invalid table name')

# 排序方向同理
direction = 'DESC' if sort_order else 'ASC'  # 布尔值转字符串,不接受用户原样
```

### Stored Procedure 风险(OWASP 警告)

```
安全(SP 内部参数化):
  CREATE PROCEDURE get_user(@name NVARCHAR(100)) AS
  BEGIN
    SELECT * FROM users WHERE name = @name;   -- SP 内部仍用绑定变量
  END

不安全(SP 内部拼接):
  CREATE PROCEDURE find_user(@name NVARCHAR(100)) AS
  BEGIN
    DECLARE @sql NVARCHAR(MAX);
    SET @sql = 'SELECT * FROM users WHERE name=''' + @name + '''';
    EXECUTE sp_executesql @sql;   -- ⚠️ 拼接,SQLi 重新出现
  END
```

### 纵深防御 — Least Privilege

即便参数化查询+白名单失败,数据库账户也只授予应用必需的权限:
- 登录模块 → `SELECT(username, password_hash)` on `user_login_view`
- 注册模块 → `INSERT` on `users`
- 数据导出 → `SELECT` (受控字段)
- ❌ 禁止应用账户 `db_owner` / `DBA` / `sysadmin`

## 演示

### Demo 1: 字符串拼接被注入

```python
sql_naive = f"SELECT * FROM users WHERE name='{user_input}'"
# 输入: ' OR '1'='1
# SQL:   SELECT * FROM users WHERE name='' OR '1'='1'   ← 全表
```

### Demo 2: 参数化查询免疫

```python
cur.execute("SELECT * FROM users WHERE name=?", (user_input,))
# 即便输入 ' OR '1'='1,数据库把整段当作 name 字面值(查不到用户)
```

### Demo 3: UNION 攻击阻断

```python
# 同样代码,UNION SELECT password_hash 不会成功
# 数据库把 "1=1' UNION SELECT..." 当作字面值
```

### Demo 4: 排序方向白名单

```python
# 表名/列名无法绑定 — 必须显式白名单
direction = 'DESC' if bool(user_input) else 'ASC'
```

## 环境准备

- Python 3.8+(纯标准库 `sqlite3`,无需 MySQL/PostgreSQL)
- Go 1.18+ (运行 Go demo)

## 运行方式

```bash
cd SQL注入与预编译/python
python3 sqli_demo.py
# 输出 6 demo:拼接 vs 参数化 / UNION 阻断 / 时间盲注模拟 / 白名单 /
# Second-order 风险 / Least Privilege 演示
```

```bash
cd SQL注入与预编译/go
go run sqli_demo.go
```

## 关键代码片段(`sqli_demo.py`)

```python
import sqlite3

# ❌ 不安全:字符串拼接
def login_naive(conn, name):
    sql = f"SELECT id,name,role FROM users WHERE name='{name}'"
    return conn.execute(sql).fetchall()

# ✅ 安全:参数化
def login_safe(conn, name):
    sql = "SELECT id,name,role FROM users WHERE name=?"
    return conn.execute(sql, (name,)).fetchall()

# 攻击载荷
naive_result = login_naive(conn, "' OR '1'='1")        # ← 返回所有用户
safe_result  = login_safe(conn,  "' OR '1'='1")        # ← 返回空(name 字面量查不到)
```

## 性能与边界
- **Prepared Statement 缓存**:服务端 PREPARE 后缓存执行计划,后续 EXECUTE 跳过解析(MySQL 默认缓存 16k statements)
- **占位符限制**:MySQL 5.5- 不支持 `LIMIT ?`,需用 `LIMIT %d` 整数插值
- **批量插入**:PostgreSQL `execute_values` 比逐条 EXECUTE 快 10-100x
- **Hibernate HQL**:命名参数 + `setParameter()` 编译时类型校验,优于字符串拼接

## 注意事项与常见坑
| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `PreparedStatement` 内拼字符串 | 用了 Prepare 仍可拼接 | 占位符必须真用于用户数据 |
| 表名/列名用户输入 | 这些**无法**用占位符 | Allow-list 验证(`switch-case` 映射) |
| `ORDER BY ... ASC/DESC` 接收用户输入 | 同上 | 转布尔值 → 枚举常量 |
| `LIMIT ?` 在 MySQL 5.5- | 不支持占位符 | `int()` 转 int 后 f-string,或 MySQL 5.5+ |
| `LIKE '%${input}%'` 拼接 | `%` 仍逃逸不出占位符保护 | `LIKE ?` 绑定 `'%' + input + '%'` |
| Second-order SQLi | 入库安全,再次读出时拼接 | 每次输出都再次走参数化 |
| ORM `raw(f"SELECT ... {input}")` | raw 退化为拼接 | `raw("... WHERE id=%s", [input])` |

## 参考资料(实际阅读)
- [OWASP SQL Injection Prevention Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html) — Parameterised Query / Stored Procedure / Whitelist / Least Privilege
- [OWASP Top 10 2025 A03 Injection](https://owasp.org/Top10/2025/A05_2025-Injection) — 最新分类(CWE-89 占 14k CVEs)
- [OWASP Query Parameterization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Query_Parameterization_Cheat_Sheet.html) — 跨语言占位符语法
- [OWASP Injection Prevention in Java](https://cheatsheetseries.owasp.org/cheatsheets/Injection_Prevention_in_Java.html) — Java 专项
- [bobby-tables.com](http://bobby-tables.com/) — 跨语言参数化查询示例
- [CWE-89: SQL Injection](https://cwe.mitre.org/data/definitions/89.html) — 漏洞定义
- [PortSwigger Web Security Academy - SQL injection](https://portswigger.net/web-security/sql-injection) — 教学互动实验