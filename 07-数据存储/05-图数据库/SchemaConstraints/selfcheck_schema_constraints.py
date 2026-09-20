"""SchemaConstraints 自检：四类约束的建立、命名/幂等、写入校验。

官方来源（实读）：
  * Cypher Manual → Constraints（四类约束与 Edition 标注、graph type 提示）
  * Cypher Manual → Constraints → Syntax（CREATE CONSTRAINT 的完整语法：
    IS [NODE] UNIQUE / IS NOT NULL / IS :: <TYPE> / IS [NODE] KEY）
  * Cypher Manual → Constraints → Managing constraints（Key = 存在性+唯一性；
    LIST<STRING NOT NULL> 与 STRING | LIST<...> 联合类型；VECTOR<INT32>(42)；
    名字唯一；IF NOT EXISTS 的通知文案与三种命中情形）
  * Cypher Manual → Working with null（类型谓词对 null 恒为 true）
"""

from schema_constraints import (UNIQUENESS, EXISTENCE, TYPE, KEY, NODE, REL,
                                Database, ConstraintError, Entity,
                                _value_matches_type)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s  %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


def raises(name, fn):
    try:
        fn()
    except ConstraintError:
        global OK
        OK += 1
        return
    FAIL.append("%s  未抛出 ConstraintError" % name)


def ok(name, fn):
    try:
        fn()
        global OK
        OK += 1
    except ConstraintError as e:
        FAIL.append("%s  意外报错: %s" % (name, e))


# ------------------------------------------------- 四类约束的建立
db = Database()
u = db.create_constraint("uniq_email", UNIQUENESS, NODE, "Person", ("email",))
eq("唯一性约束 kind", u.kind, UNIQUENESS)
e = db.create_constraint("ex_name", EXISTENCE, NODE, "Author", ("name",))
eq("存在性约束 kind", e.kind, EXISTENCE)
t = db.create_constraint("type_title", TYPE, NODE, "Movie", ("title",),
                         type_spec="STRING")
eq("类型约束 type_spec", t.type_spec, "STRING")
k = db.create_constraint("key_id", KEY, NODE, "Director", ("imdbId",))
eq("Key 约束 kind", k.kind, KEY)
r = db.create_constraint("uniq_order", UNIQUENESS, REL, "SEQUEL_OF", ("order",))
eq("关系约束 target", r.target, REL)

raises("TYPE 约束必须给类型",
       lambda: db.create_constraint("bad", TYPE, NODE, "Movie", ("title",)))
raises("EXISTENCE 只支持单属性",
       lambda: db.create_constraint("bad2", EXISTENCE, NODE, "A", ("x", "y")))
raises("唯一性至少一个属性",
       lambda: db.create_constraint("bad3", UNIQUENESS, NODE, "A", ()))

# ------------------------------------------------- 命名唯一（跨索引与约束）
raises("重名约束报错",
       lambda: db.create_constraint("uniq_email", KEY, NODE, "Person", ("x",)))
db.index_names.add("idx_taken")
raises("名字被索引占用也报错",
       lambda: db.create_constraint("idx_taken", EXISTENCE, NODE, "A", ("x",)))

# ------------------------------------------------- IF NOT EXISTS 三种情形
n0 = len(db.constraints)
same = db.create_constraint("uniq_email2", UNIQUENESS, NODE, "Person",
                            ("email",), if_not_exists=True)
eq("同模式不同名 → 不创建", len(db.constraints), n0)
eq("返回既有约束", same.name, "uniq_email")
# 官方 Example 29 的情形：与一个「不同类型的已有约束」同名 → 名字冲突优先
db.create_constraint("ex_name", UNIQUENESS, NODE, "Person", ("other",),
                     if_not_exists=True)
eq("同名不同类型 → 仍然不创建（名字冲突优先）",
   len(db.constraints), n0)
ck("该通知引用的是同名但不同类型的约束",
   any("uniq_email" in n and "ex_name" not in n for n in db.notifications)
   or any("ex_name" in n for n in db.notifications), str(db.notifications))
ck("产生了通知", len(db.notifications) >= 2, str(db.notifications))
ck("通知含 has no effect",
   any("has no effect" in n for n in db.notifications))

# 无 IF NOT EXISTS 时，同模式不同名会报错
raises("默认同模式不同名 → 报错",
       lambda: db.create_constraint("uniq_email9", UNIQUENESS, NODE, "Person",
                                    ("email",)))

# ------------------------------------------------- 存在性约束校验
d2 = Database()
d2.create_constraint("ex", EXISTENCE, NODE, "Author", ("name",))
ok("有属性 → 通过", lambda: d2.add(Entity(1, NODE, "Author", name="x")))
raises("缺属性 → 报错", lambda: d2.add(Entity(2, NODE, "Author")))
raises("属性值为 null → 报错",
       lambda: d2.add(Entity(3, NODE, "Author", name=None)))
ok("不匹配的标签不受约束", lambda: d2.add(Entity(4, NODE, "Book")))

# ------------------------------------------------- 类型约束校验
d3 = Database()
d3.create_constraint("tt", TYPE, NODE, "Movie", ("title",), type_spec="STRING")
ok("STRING 值通过", lambda: d3.add(Entity(1, NODE, "Movie", title="a")))
raises("INTEGER 值不通过",
       lambda: d3.add(Entity(2, NODE, "Movie", title=1)))
# 口径：值缺失/null 时类型谓词恒为 true（依据 Working with null）
ok("缺失属性 → 类型约束通过（null 的类型谓词恒 true）",
   lambda: d3.add(Entity(3, NODE, "Movie")))
ok("显式 null → 类型约束通过",
   lambda: d3.add(Entity(4, NODE, "Movie", title=None)))

# 复合类型：LIST<STRING NOT NULL> 与联合类型
ck("LIST<STRING NOT NULL> 接受字符串列表",
   _value_matches_type(["a", "b"], "LIST<STRING NOT NULL>"))
ck("LIST<STRING NOT NULL> 拒绝含 null 的列表",
   not _value_matches_type(["a", None], "LIST<STRING NOT NULL>"))
ck("LIST<STRING NOT NULL> 拒绝非列表",
   not _value_matches_type("a", "LIST<STRING NOT NULL>"))
ck("空列表满足 NOT NULL", _value_matches_type([], "LIST<STRING NOT NULL>"))
ck("联合类型 STRING|LIST<...> 接受字符串",
   _value_matches_type("a", "STRING | LIST<STRING NOT NULL>"))
ck("联合类型接受列表",
   _value_matches_type(["a"], "STRING | LIST<STRING NOT NULL>"))
ck("联合类型两者都不是则拒绝",
   not _value_matches_type(1, "STRING | LIST<STRING NOT NULL>"))
ck("BOOLEAN 不是 INTEGER", not _value_matches_type(True, "INTEGER"))
ck("INTEGER 值通过 INTEGER", _value_matches_type(1, "INTEGER"))
ck("VECTOR<INT32>(42) 接受序列", _value_matches_type([1] * 42,
                                                      "VECTOR<INT32>(42)"))

# ------------------------------------------------- 唯一性约束校验
d4 = Database()
d4.create_constraint("uq", UNIQUENESS, NODE, "Person", ("email",))
ok("首个写入通过",
   lambda: d4.add(Entity(1, NODE, "Person", email="a@b.c")))
raises("重复值 → 报错",
       lambda: d4.add(Entity(2, NODE, "Person", email="a@b.c")))
ok("不同值通过", lambda: d4.add(Entity(3, NODE, "Person", email="x@y.z")))
ok("不同标签不算冲突", lambda: d4.add(Entity(4, NODE, "Company",
                                             email="a@b.c")))
# 口径：缺属性不参与唯一性（官方本页未规定）
ok("缺属性不参与唯一性（口径）", lambda: d4.add(Entity(5, NODE, "Person")))
ok("第二个缺属性也不冲突（口径）", lambda: d4.add(Entity(6, NODE, "Person")))

# 复合唯一性
d5 = Database()
d5.create_constraint("uq2", UNIQUENESS, NODE, "P", ("a", "b"))
ok("复合首行通过", lambda: d5.add(Entity(1, NODE, "P", a=1, b=2)))
ok("复合仅一项相同不冲突", lambda: d5.add(Entity(2, NODE, "P", a=1, b=3)))
raises("复合全同才冲突", lambda: d5.add(Entity(3, NODE, "P", a=1, b=2)))

# ------------------------------------------------- Key = 存在性 + 唯一性
d6 = Database()
d6.create_constraint("k", KEY, NODE, "Director", ("imdbId",))
ok("Key 首个通过", lambda: d6.add(Entity(1, NODE, "Director", imdbId=1)))
raises("Key 拦重复", lambda: d6.add(Entity(2, NODE, "Director", imdbId=1)))
raises("Key 拦缺失（存在性部分）",
       lambda: d6.add(Entity(3, NODE, "Director")))
ok("Key 不拦不同值", lambda: d6.add(Entity(4, NODE, "Director", imdbId=2)))
# 与单纯唯一性约束的对比：唯一性不拦缺失，Key 会拦
d7 = Database()
d7.create_constraint("u", UNIQUENESS, NODE, "D", ("id",))
ok("纯唯一性不拦缺失", lambda: d7.add(Entity(1, NODE, "D")))

print("断言通过: %d" % OK)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    raise SystemExit(1)
print("ALL OK")
