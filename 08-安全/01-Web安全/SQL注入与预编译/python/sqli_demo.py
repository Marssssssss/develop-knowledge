"""SQL 注入与预编译查询演示(OWASP A03 注入)
Python sqlite3 标准库,6 demo:
- Demo 1: 字符串拼接 vs 参数化('OR '1'='1 攻击)
- Demo 2: UNION 注入阻断
- Demo 3: 时间盲注原理(简化模拟)
- Demo 4: Allow-list 白名单(表名/列名/排序方向)
- Demo 5: Second-order SQLi(入库安全→出库危险)
- Demo 6: Least Privilege(视图限制字段)
"""
import sqlite3
import time
import re

# ─────────────── 内存数据库 + 测试数据 ───────────────

def make_db():
    conn = sqlite3.connect(':memory:')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE users (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        password_hash TEXT NOT NULL
    )''')
    cur.execute("INSERT INTO users (name, role, password_hash) VALUES (?, ?, ?)",
                ('alice', 'user',  'hash_alice'))
    cur.execute("INSERT INTO users (name, role, password_hash) VALUES (?, ?, ?)",
                ('bob',   'admin', 'hash_bob'))
    conn.commit()
    return conn


# ─────────────── Demo 1: 拼接 vs 参数化 ───────────────

def demo_string_concat_vs_param(conn):
    print('─' * 65)
    print('[Demo 1] 字符串拼接 vs 参数化(经典 \' OR \'1\'=\'1 攻击)')
    print('─' * 65)

    user_input = "' OR '1'='1"

    # ❌ 不安全:f-string 拼接
    sql_naive = f"SELECT id, name, role FROM users WHERE name='{user_input}'"
    rows_naive = conn.execute(sql_naive).fetchall()
    print(f"  [UNSAFE] sql = {sql_naive}")
    print(f"           返回 {len(rows_naive)} 行: {rows_naive}")
    print(f"           → 攻击者拿到所有用户(包括 admin)")
    print()

    # ✅ 安全:占位符 ?
    sql_safe = "SELECT id, name, role FROM users WHERE name=?"
    rows_safe = conn.execute(sql_safe, (user_input,)).fetchall()
    print(f"  [SAFE]   sql = {sql_safe}")
    print(f"           参数 = ({user_input!r},)")
    print(f"           返回 {len(rows_safe)} 行: {rows_safe}")
    print(f"           → 数据库把整段当作 name 字面量,查不到用户")
    print()
    # 校验:正常输入两种方式结果一致
    rows_naive_normal = conn.execute(f"SELECT id, name FROM users WHERE name='alice'").fetchall()
    rows_safe_normal  = conn.execute("SELECT id, name FROM users WHERE name=?", ('alice',)).fetchall()
    assert rows_naive_normal == rows_safe_normal, '正常输入两种方式应一致'
    print('  ✓ 正常用户查询两种方式结果一致')
    print()


# ─────────────── Demo 2: UNION 注入阻断 ───────────────

def demo_union_block(conn):
    print('─' * 65)
    print('[Demo 2] UNION SELECT 攻击阻断')
    print('─' * 65)

    payload = "' UNION SELECT id, password_hash, role FROM users--"

    sql_naive = f"SELECT id, name, role FROM users WHERE name='{payload}'"
    rows = conn.execute(sql_naive).fetchall()
    print(f"  [UNSAFE] sql = ...WHERE name='{payload}'")
    print(f"           返回 {len(rows)} 行:")
    for r in rows:
        print(f"             {r}  ← ⚠️ password_hash 已泄露!")
    print()

    sql_safe = "SELECT id, name, role FROM users WHERE name=?"
    rows = conn.execute(sql_safe, (payload,)).fetchall()
    print(f"  [SAFE]   参数 = ({payload!r},)")
    print(f"           返回 {len(rows)} 行: {rows}")
    print(f"           → 整段 UNION SELECT 当字面量,UNION 语法未被解析")
    print()


# ─────────────── Demo 3: 时间盲注原理(简化) ───────────────

def demo_time_based_blind(conn):
    print('─' * 65)
    print('[Demo 3] 时间盲注(Boolean/Time-based)原理 + 防御')
    print('─' * 65)

    # 演示:参数化查询 + 数据库 sqlite3 不支持 SLEEP(),这里演示原理性代码骨架
    payload = "alice' AND (SELECT CASE WHEN substr(password_hash,1,1)='a' THEN 0 ELSE 1000000 END FROM users WHERE name='alice')=0--"

    sql_naive = f"SELECT id FROM users WHERE name='{payload}'"
    t0 = time.perf_counter()
    rows = conn.execute(sql_naive).fetchall()
    elapsed_naive = (time.perf_counter() - t0) * 1000
    print(f"  [UNSAFE] sql = ...WHERE name='{payload[:60]}...'")
    print(f"           返回 {len(rows)} 行,耗时 {elapsed_naive:.3f} ms")
    print(f"           → 攻击者通过响应时间差(无 SLEEP 时也可用布尔差异)逐字符爆破")
    print()

    sql_safe = "SELECT id FROM users WHERE name=?"
    t0 = time.perf_counter()
    rows = conn.execute(sql_safe, (payload,)).fetchall()
    elapsed_safe = (time.perf_counter() - t0) * 1000
    print(f"  [SAFE]   参数 = ({payload[:60]!r}...)")
    print(f"           返回 {len(rows)} 行,耗时 {elapsed_safe:.3f} ms")
    print(f"           → payload 是 name 字面量,无 AND 解析,无 CASE 解析,无法盲注")
    print()


# ─────────────── Demo 4: Allow-list 白名单 ───────────────

# 表名/列名/排序方向都无法用占位符,必须 Allow-list
ALLOWED_TABLES = {'users', 'orders', 'products'}
ALLOWED_COLUMNS = {'id', 'name', 'role', 'created_at'}
ALLOWED_DIRECTIONS = {'ASC', 'DESC'}


def safe_select(table, column, direction):
    # 三重白名单
    if table not in ALLOWED_TABLES:
        raise ValueError(f'Invalid table: {table!r}')
    if column not in ALLOWED_COLUMNS:
        raise ValueError(f'Invalid column: {column!r}')
    if direction not in ALLOWED_DIRECTIONS:
        raise ValueError(f'Invalid direction: {direction!r}')
    # 表/列/direction 必须是字符串字面量(白名单后),拼接安全
    sql = f'SELECT {column} FROM {table} ORDER BY id {direction} LIMIT 3'
    return sql


def demo_allowlist():
    print('─' * 65)
    print('[Demo 4] Allow-list 白名单(表名/列名/排序方向)')
    print('─' * 65)

    # 合法
    print('  [合法] table=users, column=name, direction=ASC')
    print(f'    sql = {safe_select("users", "name", "ASC")}')
    print()

    # 攻击:恶意表名
    try:
        sql = safe_select('users; DROP TABLE users;--', 'name', 'ASC')
        print(f'  🛑 阻断? sql = {sql}')
    except ValueError as e:
        print(f'  🛑 BLOCK  {e}')

    # 攻击:恶意排序方向(SLEEP 注入)
    try:
        sql = safe_select('users', 'name', "ASC; DROP TABLE users--")
        print(f'  🛑 阻断? sql = {sql}')
    except ValueError as e:
        print(f'  🛑 BLOCK  {e}')

    # 攻击:恶意列名
    try:
        sql = safe_select('users', 'name UNION SELECT password_hash FROM users--', 'ASC')
        print(f'  🛑 阻断? sql = {sql}')
    except ValueError as e:
        print(f'  🛑 BLOCK  {e}')
    print()
    print('  → 表名/列名/direction 必须经白名单映射为字面量,不能直接拼用户输入')
    print()


# ─────────────── Demo 5: Second-order SQLi ───────────────

def demo_second_order():
    print('─' * 65)
    print('[Demo 5] Second-order SQLi(入库安全 → 出库危险)')
    print('─' * 65)

    print('  场景:评论系统,用户输入"alice\' OR \'1\'=\'1"')
    print('  1) 入库:用参数化(安全)')
    safe_input = "alice' OR '1'='1"
    print(f'     cur.execute("INSERT INTO comments (user) VALUES (?)", ({safe_input!r},))')
    print('     → 数据库按字面量存储,无注入')
    print()
    print('  2) 取出评论 → 渲染到 "查询 alice\'s comments" 页面:')
    print()
    print('     ❌ 不安全(再次读出后拼接):')
    bad_sql = f"SELECT * FROM comments WHERE user='{safe_input}'"
    print(f'        sql = {bad_sql}')
    print(f'        → 重新变成 SQLi(尽管入库时安全)')
    print()
    print('     ✅ 安全(再次读出后仍走参数化):')
    print('        cur.execute("SELECT * FROM comments WHERE user=?", (comment["user"],))')
    print()
    print('  关键:**每次**拼接前都必须参数化/转义,不能依赖"输入已安全入库"')
    print()


# ─────────────── Demo 6: Least Privilege 视图 ───────────────

def demo_least_privilege(conn):
    print('─' * 65)
    print('[Demo 6] Least Privilege — 视图限制可访问字段')
    print('─' * 65)

    # 创建视图:只暴露 username + password_hash(去掉 role / id 等敏感字段)
    conn.execute('''
        CREATE VIEW user_login_view AS
        SELECT name AS username, password_hash FROM users
    ''')

    print('  业务表 users(id, name, role, password_hash) — 全字段')
    print('  视图 user_login_view(username, password_hash) — 仅登录所需字段')
    print()
    print('  应用账户授权:SELECT on user_login_view')
    print('  应用账户拒绝:SELECT on users')
    print()

    # 模拟:即便 SQLi 成功,也只能拿到 password_hash,拿不到 role
    payload = "' UNION SELECT username, password_hash, 'x' FROM user_login_view--"
    sql_naive = f"SELECT id, name, role FROM users WHERE name='{payload}'"
    rows = conn.execute(sql_naive).fetchall()
    print(f'  [UNSAFE 拼接] sql = ...WHERE name=\'{payload}\'')
    print(f'  返回 {len(rows)} 行:')
    for r in rows:
        print(f'    {r}')
    print()
    print('  即便攻击成功也只能拿到 username + password_hash')
    print('  → 真正的 role 列被视图屏蔽,需 DBA 直接登录数据库才能获取')
    print()
    print('  进一步:应用账户用 bcrypt(password_hash) 校验登录,' * 0, end='')
    print('即便 hash 泄露也无法反向求明文密码')
    print()


# ─────────────── main ───────────────

def main():
    print('=' * 65)
    print('SQL 注入与预编译查询 — OWASP A03 注入防御')
    print('=' * 65)
    print()
    conn = make_db()
    demo_string_concat_vs_param(conn)
    demo_union_block(conn)
    demo_time_based_blind(conn)
    demo_allowlist()
    demo_second_order()
    demo_least_privilege(conn)


if __name__ == '__main__':
    main()