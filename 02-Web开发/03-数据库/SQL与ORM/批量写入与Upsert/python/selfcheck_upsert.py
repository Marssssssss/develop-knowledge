"""批量写入与 Upsert 自检 —— 断言全部来自 PG/SQLite 官方文档语义。

运行： python selfcheck_upsert.py
"""

import sys

from main import (
    CardinalityViolationError, CheckViolationError, Index, Insert, NoUniqueIndexError,
    SQLiteTable, Table, batch_statement_count, needs_where_true,
)

PASS = FAIL = 0


def ok(c, m):
    global PASS, FAIL
    if c:
        PASS += 1
    else:
        FAIL += 1
        print("FAIL:", m)


def eq(g, w, m):
    ok(g == w, "%s (got=%r want=%r)" % (m, g, w))


def raises(fn, exc, m):
    try:
        fn()
    except exc:
        ok(True, m)
        return
    except Exception as e:  # 类型不符也算失败
        ok(False, "%s (raised %r)" % (m, e))
        return
    ok(False, "%s (no exception)" % m)


def distributors():
    """PG 文档里的 distributors 例子：did 上有唯一索引。"""
    t = Table("distributors", ["did"])
    t.add_index(Index("did_idx", ["did"], unique=True))
    t.rows[(1,)] = {"did": 1, "dname": "old"}
    return t


# ------------------------------------------------- A 唯一索引推断（PG）
def t_inference():
    t = distributors()
    # A1 列集合相同即命中，与书写顺序无关（官方：without regard to order）
    eq([i.name for i in t.infer_arbiters(["did"])], ["did_idx"], "A1 单列 target 命中")
    # A2 推断失败直接报错，不退化成「忽略」
    raises(lambda: t.infer_arbiters(["dname"]), NoUniqueIndexError, "A2 推断失败报错")

    # A3 复合唯一索引：列集合相同即可，顺序无关
    t2 = Table("m", ["a"])
    t2.add_index(Index("ab", ["a", "b"], unique=True))
    eq([i.name for i in t2.infer_arbiters(["b", "a"])], ["ab"], "A3 顺序无关")

    # A4 非唯一索引不会被推断为 arbiter
    t3 = Table("m", ["a"])
    t3.add_index(Index("a_nonuniq", ["a"], unique=False))
    raises(lambda: t3.infer_arbiters(["a"]), NoUniqueIndexError, "A4 非唯一索引不能当 arbiter")

    # A5 非部分索引也能被带 index_predicate 的 target 推断命中
    def pred(row):
        "partial"
        return row.get("active", False)

    eq([i.name for i in t.infer_arbiters(["did"], index_predicate=pred)], ["did_idx"],
       "A5 非部分索引可被带谓词的 target 推断")


# ---------------------------------------------------- B DO NOTHING / DO UPDATE
def t_actions():
    t = distributors()
    r = Insert(t, [{"did": 1, "dname": "new"}], conflict_target=["did"]).run()
    eq(len(r["skipped"]), 1, "B1 DO NOTHING 冲突时跳过")
    eq(t.rows[(1,)]["dname"], "old", "B1b DO NOTHING 不改原行")

    r2 = Insert(t, [{"did": 1, "dname": "new"}], conflict_target=["did"],
                action="update", set_clause={"dname": "excluded.dname"}).run()
    eq(t.rows[(1,)]["dname"], "new", "B2 DO UPDATE 用 excluded 取拟插入值")
    eq(len(r2["updated"]), 1, "B2b 记为更新")

    # B3 省略 conflict_target 的 DO NOTHING：对所有唯一约束生效
    t3 = Table("t", ["id"])
    t3.add_index(Index("e", ["email"], unique=True))
    t3.rows[(1,)] = {"id": 1, "email": "a@b.c"}
    r3 = Insert(t3, [{"id": 2, "email": "a@b.c"}]).run()   # 无 target
    eq(len(r3["skipped"]), 1, "B3 无 target 的 DO NOTHING 对所有唯一索引生效")

    # B4 DO UPDATE 必须给 conflict_target
    raises(lambda: Insert(t, [{"did": 9}], action="update").run(),
           NoUniqueIndexError, "B4 DO UPDATE 缺 target 报错")

    # B5 ON CONSTRAINT 走命名约束
    t5 = distributors()
    r5 = Insert(t5, [{"did": 1, "dname": "x"}], constraint_name="did_idx").run()
    eq(len(r5["skipped"]), 1, "B5 ON CONSTRAINT 按名字选 arbiter")


# ------------------------------------------- C WHERE 在冲突识别之后求值
def t_where():
    t = distributors()
    # WHERE 不满足 → 行不被更新，但仍然被锁（官方：all rows will be locked）
    r = Insert(t, [{"did": 1, "dname": "new"}], conflict_target=["did"],
               action="update", set_clause={"dname": "excluded.dname"},
               where=lambda ex, pr: False, returning=True).run()
    eq(t.rows[(1,)]["dname"], "old", "C1 WHERE 不满足则不更新")
    eq(len(r["locked_not_updated"]), 1, "C1b 但仍然被锁")
    eq(len(r["returning"]), 0, "C2 RETURNING 不返回未被更新的行")

    r2 = Insert(t, [{"did": 1, "dname": "new"}], conflict_target=["did"],
                action="update", set_clause={"dname": "excluded.dname"},
                where=lambda ex, pr: True, returning=True).run()
    eq(len(r2["returning"]), 1, "C3 真正更新才进 RETURNING")


# ------------------------------------------------- D cardinality violation
def t_cardinality():
    t = distributors()
    # 同一批里两行都命中 did=1 → 同一行被影响两次
    raises(lambda: Insert(t, [{"did": 1, "dname": "a"}, {"did": 1, "dname": "b"}],
                          conflict_target=["did"], action="update",
                          set_clause={"dname": "excluded.dname"}).run(),
           CardinalityViolationError, "D1 同一行被影响两次报 cardinality violation")

    # DO NOTHING 不受此限制
    t2 = distributors()
    r = Insert(t2, [{"did": 1, "dname": "a"}, {"did": 1, "dname": "b"}],
               conflict_target=["did"]).run()
    eq(len(r["skipped"]), 2, "D2 DO NOTHING 不触发该错误")


# ------------------------------------------------------------ E 权限
def t_privileges():
    t = distributors()
    raises(lambda: Insert(t, [{"did": 5}], conflict_target=["did"]).run(
        {"INSERT": False, "UPDATE": True, "SELECT": True}),
        PermissionError, "E1 缺 INSERT 权限报错")
    raises(lambda: Insert(t, [{"did": 1}], conflict_target=["did"], action="update",
                          set_clause={"dname": "x"}).run(
        {"INSERT": True, "UPDATE": False, "SELECT": True}),
        PermissionError, "E2 DO UPDATE 还需要 UPDATE 权限")
    raises(lambda: Insert(t, [{"did": 1}], conflict_target=["did"]).run(
        {"INSERT": True, "UPDATE": True, "SELECT": False}),
        PermissionError, "E3 任何 ON CONFLICT 都要求被读列的 SELECT 权限")


# -------------------------------------------------------------- F SQLite
def t_sqlite():
    s = SQLiteTable("phonebook", [["name"]])
    s.rows.append({"name": "alice", "phonenumber": "1"})
    # F1 DO UPDATE 只作用于冲突的那一行
    ins, upd = s.upsert([{"name": "alice", "phonenumber": "2"}], target=["name"],
                        action="update", set_clause={"phonenumber": "excluded.phonenumber"})
    eq((ins, upd), (0, 1), "F1 冲突行走 DO UPDATE")
    eq(s.rows[0]["phonenumber"], "2", "F1b 值被更新")

    # F2 省略 conflict target：任一唯一约束触发
    s2 = SQLiteTable("t", [["a"], ["b"]])
    s2.rows.append({"a": 1, "b": 2})
    ins2, upd2 = s2.upsert([{"a": 1, "b": 9}], action="nothing")
    eq((ins2, upd2), (0, 0), "F2 无 target 时任一唯一约束都能触发")

    # F3 DO UPDATE 末尾的 WHERE 不满足 → 变成 no-op（不报错）
    s3 = SQLiteTable("phonebook2", [["name"]])
    s3.rows.append({"name": "a", "validDate": 5})
    ins3, upd3 = s3.upsert([{"name": "a", "validDate": 3}], target=["name"],
                           action="update", set_clause={"validDate": "excluded.validDate"},
                           where=lambda ex, pr: pr["validDate"] > ex["validDate"])
    eq((ins3, upd3), (0, 0), "F3 WHERE 不满足时 DO UPDATE 是 no-op")

    # F4 REPLACE 是「先删后插」
    s4 = SQLiteTable("t", [["k"]])
    s4.rows.append({"k": 1, "v": "old"})
    eq(s4.replace_into({"k": 1, "v": "new"}, ["k"]), "DELETE+INSERT",
       "F4 REPLACE 先删后插（会触发 DELETE 触发器与外键动作）")
    eq(len(s4.rows), 1, "F4b 行数不变")

    # F5 解析歧义：INSERT ... SELECT 后直接跟 ON CONFLICT 需要 WHERE
    ok(needs_where_true("INSERT INTO t1 SELECT * FROM t2 ON CONFLICT(x) DO UPDATE SET y=excluded.y"),
       "F5a SELECT 后无 WHERE 需要补 WHERE true")
    ok(not needs_where_true("INSERT INTO t1 SELECT * FROM t2 WHERE true ON CONFLICT(x) DO UPDATE SET y=excluded.y"),
       "F5b 补了 WHERE true 就无歧义")
    ok(not needs_where_true("INSERT INTO t1(a) VALUES(1) ON CONFLICT(a) DO NOTHING"),
       "F5c VALUES 形式没有这个歧义")


# ---------------------------------------------------------- G 批量写入
def t_batch():
    eq(batch_statement_count(1000, 1), 1000, "G1 单行单语句")
    eq(batch_statement_count(1000, 100), 10, "G2 100 行合批是 10 条语句")
    eq(batch_statement_count(1001, 100), 11, "G3 向上取整")
    raises(lambda: batch_statement_count(10, 0), ValueError, "G4 batch_size 必须为正")

    # G5 批量 upsert：多行一次下发，命中同一行会 cardinality violation
    t = distributors()
    r = Insert(t, [{"did": 2, "dname": "a"}, {"did": 3, "dname": "b"}],
               conflict_target=["did"], action="update",
               set_clause={"dname": "excluded.dname"}).run()
    eq(len(r["inserted"]), 2, "G5 批量 insert 一次下发多行")


def main():
    t_inference(); t_actions(); t_where(); t_cardinality()
    t_privileges(); t_sqlite(); t_batch()
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
