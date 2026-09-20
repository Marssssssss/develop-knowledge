"""CypherNullLogic 自检：官方真值表 9 行 × 4 运算符 + IN/[]/ORDER BY 等。

官方来源（实读）：
  * Cypher Manual → Values and types → Working with null
    （三值逻辑真值表、IN 的 8 个例子、[] 的 4 个例子、IS NULL/IS NOT NULL、
     「类型谓词对 null 一律 true」）
  * Cypher Manual → Clauses → ORDER BY → Null values
    （"null values appear last in ascending order and first in descending order"）
"""

from cypher_null import (TRUTH_TABLE, cy_and, cy_or, cy_xor, cy_not, cy_eq,
                         cy_ne, cy_in, cy_get, cy_slice, cy_all, cy_any,
                         cy_none, cy_single, coalesce, type_predicate,
                         order_by, where, is_null, is_not_null,
                         cy_arith, cy_lt, cy_sin, INT, STRING, Node, head)

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


# ------------------------------------------------- 三值逻辑真值表（官方 9 行）
for a, b, w_and, w_or, w_xor, w_not in TRUTH_TABLE:
    tag = "(%r,%r)" % (a, b)
    eq("AND " + tag, cy_and(a, b), w_and)
    eq("OR  " + tag, cy_or(a, b), w_or)
    eq("XOR " + tag, cy_xor(a, b), w_xor)
    eq("NOT a" + tag, cy_not(a), w_not)

# ------------------------------------------------- null 不等于 null
eq("null = null 得到 null", cy_eq(None, None), None)
ck("null = null 不是 True", cy_eq(None, None) is not True)
eq("null <> null 也是 null", cy_ne(None, None), None)
eq("1 = 1 → true", cy_eq(1, 1), True)
eq("1 = null → null", cy_eq(1, None), None)
eq("IS NULL(null) → true", is_null(None), True)
eq("IS NOT NULL(1) → true", is_not_null(1), True)
eq("IS NOT NULL(null) → false", is_not_null(None), False)
# 属性存在性：n.prop IS NOT NULL 同时要求「属性存在」且「值非 null」
n = Node(name="Tom", age=None)
eq("属性存在但值为 null → IS NULL 为真", is_null(n.prop("age")), True)
eq("属性根本不存在 → 同样是 null", is_null(n.prop("missing")), True)
eq("属性存在且有值 → IS NOT NULL", is_not_null(n.prop("name")), True)

# ------------------------------------------------- WHERE 的二值化
eq("WHERE true 通过", where(True), True)
eq("WHERE false 过滤", where(False), False)
eq("WHERE null 过滤", where(None), False)
# 鉴别力：2 IN [1,null,3] 是 null，但 WHERE 里按 false 处理 → 该行被过滤
eq("IN 结果为 null", cy_in(2, [1, None, 3]), None)
eq("WHERE 把该 null 当 false", where(cy_in(2, [1, None, 3])), False)

# ------------------------------------------------- IN 的官方 8 例
eq("2 IN [1,2,3]", cy_in(2, [1, 2, 3]), True)
eq("2 IN [1,null,3]", cy_in(2, [1, None, 3]), None)
eq("2 IN [1,2,null]", cy_in(2, [1, 2, None]), True)
eq("2 IN [1]", cy_in(2, [1]), False)
eq("2 IN []", cy_in(2, []), False)
eq("null IN [1,2,3]", cy_in(None, [1, 2, 3]), None)
eq("null IN [1,null,3]", cy_in(None, [1, None, 3]), None)
eq("null IN []", cy_in(None, []), False)
# 反直觉点：null IN [] 是 false（空列表可确定无匹配），
# 而 null IN [1,2,3] 是 null —— 空列表把「未知」变成了「确定」
ck("null IN [] 与 null IN [1,2,3] 不同",
   cy_in(None, []) != cy_in(None, [1, 2, 3]))

# ------------------------------------------------- [] 取值与区间
eq("[1,2,3][null]", cy_get([1, 2, 3], None), None)
eq("[1,2,3,4][null..2]", cy_slice([1, 2, 3, 4], None, 2), None)
eq("[1,2,3][1..null]", cy_slice([1, 2, 3], 1, None), None)
eq("{age:25}[null]", cy_get({"age": 25}, None), None)
eq("[1,2,3][0..2] 正常切片", cy_slice([1, 2, 3], 0, 2), [1, 2])
# 官方给出的规避写法：给上下界兜底
lo, hi = None, None
a = [1, 2, 3]
eq("coalesce 兜底后区间可用", cy_slice(a, coalesce(lo, 0),
                                        coalesce(hi, len(a))), [1, 2, 3])

# ------------------------------------------------- 产出 null 的表达式
eq("[][0]", cy_get([], 0), None)
eq("head([])", head([]), None)
eq("head([1])", head([1]), 1)
eq("n.missingProperty", Node(name="x").prop("age"), None)
# 官方列举的「产出 null 的表达式」，逐个用真实函数验证
eq("1 < null", cy_lt(1, None), None)
eq("null < 1", cy_lt(None, 1), None)
eq("1 < 2", cy_lt(1, 2), True)
eq("1 + null", cy_arith(1, None), None)
eq("1 + 2", cy_arith(1, 2), 3)
eq("sin(null)", cy_sin(None), None)
ck("sin(0) 有实值", cy_sin(0) == 0.0, "got=%r" % cy_sin(0))

# ------------------------------------------------- all / any / none / single
eq("any([true,null]) → true（OR 吸收）", cy_any([True, None]), True)
eq("all([false,null]) → false（AND 吸收）", cy_all([False, None]), False)
eq("all([true,null]) → null", cy_all([True, None]), None)
eq("any([false,null]) → null", cy_any([False, None]), None)
eq("none([true,null]) → false", cy_none([True, None]), False)
eq("none([false,null]) → null", cy_none([False, None]), None)
eq("single([true]) → true", cy_single([True]), True)
eq("single([true,null]) → null（null 可能也是 true）",
   cy_single([True, None]), None)
eq("single([true,true,null]) → false（确定不止一个）",
   cy_single([True, True, None]), False)

# ------------------------------------------------- 类型谓词
# 反直觉点：类型谓词对 null **恒为 true**，所以 IS :: INTEGER 挡不住 null
eq("null IS :: INTEGER → true", type_predicate(None, INT), True)
eq("null IS :: STRING → true", type_predicate(None, STRING), True)
eq("1 IS :: INTEGER → true", type_predicate(1, INT), True)
eq('"a" IS :: INTEGER → false', type_predicate("a", INT), False)
eq('"a" IS :: STRING → true', type_predicate("a", STRING), True)

# ------------------------------------------------- ORDER BY 的 null 位置
rows = [None, "shipped", "pending", None, "shipped"]
asc = order_by(rows)
desc = order_by(rows, desc=True)
eq("升序 null 最后", asc[-1], None)
eq("升序 null 都在尾部",
   [x is None for x in asc], [False, False, False, True, True])
eq("降序 null 最前",
   [x is None for x in desc], [True, True, False, False, False])
eq("升序非 null 有序", [x for x in asc if x is not None],
   ["pending", "shipped", "shipped"])
eq("降序非 null 有序", [x for x in desc if x is not None],
   ["shipped", "shipped", "pending"])

# ------------------------------------------------- coalesce
eq("coalesce(null,null,3)", coalesce(None, None, 3), 3)
eq("coalesce(null,null)", coalesce(None, None), None)
eq("coalesce(1,null)", coalesce(1, None), 1)

print("断言通过: %d" % OK)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    raise SystemExit(1)
print("ALL OK")
