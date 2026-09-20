"""SearchIndexes 自检：谓词可解性、planner 选择、命名/幂等/复合收录。

官方来源（实读）：
  * Cypher Manual → Indexes → Search-performance indexes
    （四类索引；两个 token lookup 索引建库时自带；planner 自动挑 / USING 强制）
  * Cypher Manual → Indexes → Search-performance indexes → Create indexes
    （不指定类型得 Range；Range 的 5 类谓词；Text 的 5 类谓词 + trigram 索引
      示例 "developer"；Point 的 3 类谓词与 spatial.cartesian 默认值；
      名字唯一、IF NOT EXISTS 的通知文案、复合索引收录条件）
"""

from search_indexes import (RANGE, TEXT, POINT, TOKEN, NODE, REL, Schema,
                            SchemaError, Index, can_solve, planner,
                            plan_with_hint, trigrams, text_matches,
                            indexed_members, RANGE_PREDS, TEXT_PREDS,
                            POINT_PREDS, TOKEN_PREDS)

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
    except SchemaError:
        global OK
        OK += 1
        return
    FAIL.append("%s  未抛出 SchemaError" % name)


# ------------------------------------------------- 谓词可解性（官方四张表）
eq("Range 谓词集合", sorted(RANGE_PREDS),
   ["eq", "exists", "in", "range", "starts_with"])
eq("Text 谓词集合", sorted(TEXT_PREDS),
   ["contains", "ends_with", "eq", "in", "starts_with"])
eq("Point 谓词集合", sorted(POINT_PREDS),
   ["distance", "point_eq", "within_bbox"])
eq("Token lookup 谓词集合", sorted(TOKEN_PREDS), ["label", "rel_type"])

# 关键否定：Range **解不了** ENDS WITH / CONTAINS（这正是 Text 索引存在的理由）
eq("Range 不能解 contains", "contains" in RANGE_PREDS, False)
eq("Range 不能解 ends_with", "ends_with" in RANGE_PREDS, False)
eq("Text 能解 contains", "contains" in TEXT_PREDS, True)
eq("Token 不能解任何属性谓词", "eq" in TOKEN_PREDS, False)
eq("Point 不能解普通 range", "range" in POINT_PREDS, False)

# ------------------------------------------------- trigram（官方示例）
eq('trigrams("developer")', trigrams("developer"),
   ["dev", "eve", "vel", "elo", "lop", "ope", "per"])
eq("trigram 个数 = len-2", len(trigrams("developer")), 7)
eq("两字符切不出 trigram", trigrams("ab"), [])
eq("三字符恰好 1 个", trigrams("abc"), ["abc"])
ck("CONTAINS 'vel' 命中", text_matches("developer", "vel"))
ck("CONTAINS 'per' 命中（ENDS WITH 也走它）", text_matches("developer", "per"))
ck("CONTAINS 'xyz' 不命中", not text_matches("developer", "xyz"))
ck("短查询串退化为子串判定（口径）", text_matches("developer", "ev"))

# ------------------------------------------------- 建索引与默认类型
s = Schema()
s.default_token_indexes()
eq("建库自带 2 个 token 索引", len(s.indexes), 2)
eq("自带的是 token 类型", [i.itype for i in s.indexes], [TOKEN, TOKEN])

i1 = s.create_index("idx_surname", NODE, "Person", ("surname",))
eq("不指定类型 → Range", i1.itype, RANGE)
i2 = s.create_index("idx_nick", NODE, "Person", ("nickname",), itype=TEXT)
eq("显式 TEXT", i2.itype, TEXT)
i3 = s.create_index("idx_loc", NODE, "Person", ("sublocation",), itype=POINT)
eq("显式 POINT", i3.itype, POINT)
i4 = s.create_index("idx_knows", REL, "KNOWS", ("since",))
eq("关系索引 target=REL", i4.target, REL)

raises("token 索引不能带属性",
       lambda: s.create_index("bad", NODE, "Person", ("x",), itype=TOKEN))
raises("无属性的属性索引非法", lambda: s.create_index("bad2", NODE, "Person"))

# ------------------------------------------------- 命名唯一（索引 ∩ 约束）
raises("重名索引报错", lambda: s.create_index("idx_surname", NODE, "Person",
                                              ("other",)))
s.constraints.append("idx_surname2")
raises("名字被约束占用也报错",
       lambda: s.create_index("idx_surname2", NODE, "Person", ("x",)))

# ------------------------------------------------- 幂等：默认报错 vs IF NOT EXISTS
raises("默认重复创建报错",
       lambda: s.create_index("idx_surname3", NODE, "Person", ("surname",)))
n_before = len(s.indexes)
same = s.create_index("idx_surname3", NODE, "Person", ("surname",),
                      if_not_exists=True)
eq("IF NOT EXISTS 不新增索引", len(s.indexes), n_before)
eq("IF NOT EXISTS 返回既有索引", same.name, "idx_surname")
ck("IF NOT EXISTS 产生通知", len(s.notifications) >= 1,
   str(s.notifications))
ck("通知文案含 has no effect",
   any("has no effect" in n for n in s.notifications))

# ------------------------------------------------- planner 选择
s2 = Schema()
s2.default_token_indexes()
r = s2.create_index("range_name", NODE, "Person", ("name",))
t = s2.create_index("text_name", NODE, "Person", ("name",), itype=TEXT)
p = s2.create_index("point_loc", NODE, "Person", ("home",), itype=POINT)

eq("eq 选 Range（口径：多类可解时优先默认类型）",
   planner(s2.indexes, "eq", "name").name, "range_name")
eq("starts_with 选 Range", planner(s2.indexes, "starts_with", "name").name,
   "range_name")
eq("contains 只能选 Text", planner(s2.indexes, "contains", "name").name,
   "text_name")
eq("ends_with 只能选 Text", planner(s2.indexes, "ends_with", "name").name,
   "text_name")
eq("distance 只能选 Point", planner(s2.indexes, "distance", "home").name,
   "point_loc")
eq("within_bbox 只能选 Point",
   planner(s2.indexes, "within_bbox", "home").name, "point_loc")
eq("label 只能选 token",
   planner(s2.indexes, "label", target=NODE).itype, TOKEN)
eq("无属性覆盖 → 选不到",
   planner(s2.indexes, "eq", "unknown_prop"), None)
eq("没有任何索引能解 contains 时选不到",
   planner([r], "contains", "name"), None)

# USING 提示强制覆盖 planner 的选择
hint = plan_with_hint(s2.indexes, "text_name", "eq", "name")
eq("USING 强制走 Text（planner 本会选 Range）", hint.name, "text_name")
raises("USING 不存在的索引报错",
       lambda: plan_with_hint(s2.indexes, "nope", "eq", "name"))

# ------------------------------------------------- 复合索引收录条件
ck("标签不符不收录",
   not indexed_members({"age": 1, "country": "SE"}, ("age", "country"),
                       "Company", "Person"))
ck("属性不全不收录",
   not indexed_members({"age": 1}, ("age", "country"), "Person", "Person"))
ck("标签+全属性才收录",
   indexed_members({"age": 1, "country": "SE"}, ("age", "country"),
                   "Person", "Person"))

# ------------------------------------------------- can_solve 的属性覆盖
eq("索引覆盖属性才可解", can_solve(r, "eq", "name"), True)
eq("索引未覆盖属性不可解", can_solve(r, "eq", "other"), False)
eq("token 索引可解 label", can_solve(Index("t", NODE, "*", (), TOKEN),
                                     "label"), True)
eq("token 索引不可解 eq", can_solve(Index("t", NODE, "*", (), TOKEN), "eq",
                                   "name"), False)

print("断言通过: %d" % OK)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    raise SystemExit(1)
print("ALL OK")
