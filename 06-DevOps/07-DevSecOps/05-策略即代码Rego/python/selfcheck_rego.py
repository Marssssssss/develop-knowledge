#!/usr/bin/env python3
"""demo575 自检：OPA/Rego 编译期的 safety 检查与 body 重排。

    python selfcheck_rego.py

V 段是**官方测试向量对拍**：逐条复现
``open-policy-agent/opa`` ``v1/ast/compile_test.go`` 的
``TestOutputVarsForNode``（safe 初值 = ``ReservedVars`` = {data, input}）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from terms import (  # noqa: E402
    Array, CallT, Const, Obj, Ref, SetT, Var,
)
from exprs import (  # noqa: E402
    Bare, Call, Every, Eq, Logical, Not, With,
)
from safety import output_vars_for_body, output_vars_for_expr  # noqa: E402
from main import check_every, compile_rule, reorder_body_for_safety  # noqa: E402

PASS = 0
FAILS = []


def ok(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILS.append(msg)
        print("FAIL: %s" % msg)


G = {"input", "data"}          # ReservedVars


def eq_(got, exp, note):
    ok(got == exp, "%s  期望 %s 实际 %s" % (note, sorted(exp), sorted(got)))


# ==========================================================================
# V. 官方测试向量对拍（TestOutputVarsForNode）
# ==========================================================================

def ovb(body, extra_safe=()):
    return output_vars_for_body(body, G | set(extra_safe))


eq_(ovb([Bare(Var("x"))]), set(), "V01 官方 `x` -> set()")
eq_(ovb([Not([Eq(Var("x"), Const(1))])]), set(), "V02 官方 `not x = 1` -> set()")
eq_(ovb([Eq(Array([Var("x"), Array([Const(1)])]),
                  Array([Const(1), Array([Var("y")])]))]), {"x", "y"},
    "V03 官方 `[x,[1]] = [1,[y]]` -> {x,y}")
eq_(ovb([Eq(SetT([Var("x"), Array([Const(1)])]),
                SetT([Const(1), Array([Var("y")])]))]), set(),
    "V04 官方 `{x,[1]} = {1,[y]}` -> set()（集合不参与 unify）")
eq_(ovb([Eq(Obj([("foo", Var("x")),
                       ("bar", Obj([("baz", Const(1))]))]),
                Obj([("foo", Const(1)),
                       ("bar", Obj([("baz", Var("y"))]))]))]), {"x", "y"},
    "V05 官方 嵌套对象值 -> {x,y}")
eq_(ovb([Eq(Obj([("foo", Var("x"))]), Obj([(Var("y"), Const(1))]))]), set(),
    "V06 官方 对象键是变量 -> set()")
eq_(ovb([Call("count", [Array([Const(1), Const(2), Const(3)]), Var("x")], 1)]), {"x"},
    "V07 官方 `count([1,2,3], x)` -> {x}")
eq_(ovb([Call("count", [Var("x")], -1)]), set(), "V08 官方 `count(x)` 无 arity -> set()")
eq_(ovb([Call("f", [Const(1), Var("x")], -1)]), set(), "V09 官方 `f(1,x)` arity=-1 -> set()")
eq_(ovb([Call("f", [Const(1), Var("x")], 1)]), {"x"}, "V10 官方 `f(1,x)` arity=1 -> {x}")
eq_(ovb([Call("f", [Const(1), Var("x")], 2)]), set(), "V11 官方 `f(1,x)` arity=2 -> set()")
eq_(ovb([Call("f", [Ref("data", ["p", Var("x")]), Var("y")], 1)]), {"x", "y"},
    "V12 官方 `f(data.p[x], y)` -> {x,y}")
eq_(ovb([Call("f", [Ref("x", [Const(1)])], 1)]), set(), "V13 官方 `f(x[1])` -> set()")
eq_(ovb([Call("f", [Const(1), SetT([Var("x")])], 1)]), set(), "V14 官方 `f(1,{x})` -> set()")
eq_(ovb([Call("f", [Const(1), Obj([("x", Const(1))])], 1)]), set(), "V15 官方 `f(1,{x:1})` -> set()")
eq_(ovb([Call("f", [Var("x"), Var("y")], 1)]), set(), "V16 官方 `f(x,y)` 输入不安全 -> set()")
eq_(ovb([With(Bare(Const(1)), Var("y"))]), set(), "V17 官方 `1 with input as y` -> set()")
eq_(ovb([With(Eq(Var("x"), Const(1)), Var("y"))]), set(), "V18 官方 `x=1 with input as y` -> set()")
eq_(ovb([With(Eq(Var("x"), Const(1)), Var("y"))], ("y",)), {"x"},
    "V19 官方 同上 + extraSafe{y} -> {x}")
eq_(ovb([Bare(Ref("data", ["p", Var("x")]))]), {"x"}, "V20 官方 `data.p[x]` -> {x}")
eq_(ovb([Bare(Ref("p", [Var("x")]))]), set(), "V21 官方 `p[x]` 头不安全 -> set()")
eq_(ovb([Bare(Ref("data", ["p", Ref("data", ["q", Var("x")])]))]), {"x"},
    "V22 官方 `data.p[data.q[x]]` -> {x}")
eq_(ovb([Eq(Var("x"), Const(1)), Eq(Var("y"), Var("x")), Eq(Var("z"), Var("y"))]),
    {"x", "y", "z"}, "V23 官方 `x=1; y=x; z=y` -> {x,y,z}")
eq_(ovb([Eq(Ref(SetT([Const(1), Const(2)]), [Const(1)]), Var("x"))]), {"x"},
    "V24 官方 `{1,2}[1] = x` -> {x}（复合头）")
eq_(ovb([Eq(Var("x"), Const(1)),
         Eq(Ref(SetT([Var("x"), Const(2)]), [Const(1)]), Var("y"))]), {"x", "y"},
    "V25 官方 `x=1; {x,2}[1] = y` -> {x,y}")
eq_(ovb([Eq(Ref(SetT([Var("x"), Const(2)]), [Const(1)]), Var("y"))]), set(),
    "V26 官方 `{x,2}[1] = y` -> set()")
eq_(ovb([Eq(Var("z"), Const("abc")),
         Eq(Var("x"), Ref(CallT("split", [Var("z"), Const("")]), [Var("y")]))]),
    {"x", "y", "z"}, "V27 官方 `z=\"abc\"; x = split(z,\"\")[y]` -> {x,y,z}")
eq_(ovb([Eq(Var("z"), Const("abc")),
         Eq(Var("x"), Ref(CallT("split", [Var("z"), Var("a")]), [Var("y")]))]), {"z"},
    "V28 官方 `...split(z,a)[y]` -> {z}")
eq_(ovb([Every(Array([Const(1), Const(2)]), ["k", "v"])]), set(),
    "V29 官方 `every k,v in [1,2]` -> set()")
eq_(ovb([Eq(Var("xs"), Array([])), Every(Ref("xs", [Var("i")]), ["k", "v"])]), {"xs", "i"},
    "V30 官方 `xs=[]; every k,v in xs[i]` -> {xs,i}")
eq_(ovb([Every(Array([]), ["k", "v"], [Eq(Var("i"), Const(1))])]), set(),
    "V31 官方 every body 里的变量不外泄 -> set()")

# ==========================================================================
# A. outputVarsForExpr 的单元性质
# ==========================================================================

eq_(output_vars_for_expr(Eq(Var("x"), Const(1)), G), {"x"}, "A1 x == 1 产出 {x}")
eq_(output_vars_for_expr(Eq(Var("x"), Var("y")), G), set(), "A2 x == y 两边都未绑定")
eq_(output_vars_for_expr(Eq(Var("x"), Var("y")), G | {"y"}), {"x"}, "A3 一侧安全即绑定另一侧")
eq_(output_vars_for_expr(Eq(Var("x"), Const(1), True), G), {"x"}, "A4 x := 1 产出 {x}")
# A5/A9 是一对：同样的项，只差 `:=`，产出从 {i} 变成空 —— 证明 LHS 被整体剔出安全基
eq_(output_vars_for_expr(Eq(Ref("input", ["a", Var("i")]), Const(1), True), G), set(),
    "A5 `input.a[i] := 1` 不产出（:= 的 LHS 被整体剔出安全基，issue #3546）")
eq_(output_vars_for_expr(Eq(Var("x"), Const(1)), G | {"x"}), set(), "A6 已安全不重复产出")
eq_(output_vars_for_expr(Eq(Const(1), Var("x")), G), {"x"}, "A7 常量在左侧也能绑定右侧")
eq_(output_vars_for_expr(Logical("and", [Eq(Var("x"), Const(1))]), G), set(), "A8 and 不产出")
eq_(output_vars_for_expr(Eq(Ref("input", ["a", Var("i")]), Const(1)), G), {"i"},
    "A9 引用下标可产出（isRefSafe 只看头）")
eq_(output_vars_for_expr(Eq(Ref("other", ["a", Var("i")]), Const(1)), G), set(),
    "A10 引用头不安全时不产出")
eq_(output_vars_for_expr(Bare(Ref("input", ["user"])), G), set(), "A11 ground 引用不产出")
eq_(output_vars_for_expr(Bare(Ref("input", ["users", Var("i")])), G), {"i"}, "A12 裸引用产出下标")
eq_(output_vars_for_expr(With(Call("count", [Ref("input", ["a"]), Var("n")], 1),
                              Const({"x": 1})), G), {"n"}, "A13 with 目标安全时正常产出")

# ==========================================================================
# B. 重排：不动点迭代
# ==========================================================================

body = [Eq(Var("a"), Ref("input", ["i"])), Eq(Var("b"), Var("a"))]
ordered, errs = compile_rule(G, body)
ok(errs == [], "B1 应无编译错误, 实际 %s" % errs)
ok([id(x) for x in ordered] == [id(x) for x in body], "B1b 已合法的顺序不动")

body = [Eq(Var("b"), Var("a")), Eq(Var("a"), Ref("input", ["i"]))]
ordered, errs = compile_rule(G, body)
ok(errs == [], "B2 逆序写法应被救回, 实际 %s" % errs)
ok([id(x) for x in ordered] == [id(x) for x in body[::-1]], "B2b 应被重排")

e1, e2, e3 = Eq(Var("c"), Var("b")), Eq(Var("b"), Ref("input", ["i"])), Eq(Var("a"), Ref("input", ["j"]))
ordered, errs = compile_rule(G, [e1, e2, e3])
ok([id(x) for x in ordered] == [id(e2), id(e3), id(e1)],
   "B3 同趟变安全的按原始顺序加入（minimal re-ordering）, 实际 %s" % [str(x) for x in ordered])

body = [Eq(Var("x"), Var("y"))]
ordered, errs = compile_rule(G, body)
ok(sorted(errs) == ["var x is unsafe", "var y is unsafe"], "B4 两侧都报 unsafe, 实际 %s" % errs)
ok([id(x) for x in ordered] == [id(x) for x in body],
   "B4b 报错时返回**原始** body（checkBodySafety）, 实际 %s" % [str(x) for x in ordered])

b0 = Not([Call("is_admin", [Var("x")], 1)])
b1 = Eq(Var("x"), Const(1))
ordered, errs = compile_rule(G, [b0, b1])
ok(errs == [], "B5 not 的变量由后一条绑定即可, 实际 %s" % errs)
ok([id(x) for x in ordered] == [id(b1), id(b0)], "B5b not 必须排在绑定之后")

ordered, errs = compile_rule(G, [Not([Call("is_admin", [Var("x")], 1)])])
ok(errs == ["var x is unsafe"], "B6 not 自己不产出变量, 实际 %s" % errs)

b = [Eq(Var("a"), Ref("input", ["i"])), Eq(Var("c"), Var("b")), Eq(Var("b"), Ref("input", ["j"]))]
ordered, errs = compile_rule(G, b)
ok(errs == [], "B7 链式中途断掉也能排, 实际 %s" % errs)
ok([id(x) for x in ordered] == [id(b[0]), id(b[2]), id(b[1])], "B7b 依赖 c 的排在最后")

# ==========================================================================
# C. 内置调用与 arity
# ==========================================================================

ordered, errs = compile_rule(G, [Call("count", [Var("x"), Var("n")], 1)])
ok(sorted(errs) == ["var n is unsafe", "var x is unsafe"],
   "C1 调用无法调度时输入与输出都被记为 unsafe, 实际 %s" % errs)

ordered, errs = compile_rule(G, [Eq(Var("x"), Ref("input", ["a"])),
                                 Call("count", [Var("x"), Var("n")], 1)])
ok(errs == [], "C2 输入安全后调用可排, 实际 %s" % errs)

# assignmentLHS 压制规则：:= 的 LHS 不报错，只报真正的病根
ordered, errs = compile_rule(G, [Eq(Var("n"), Call("count", [Var("x")], 1), True)])
ok(errs == ["var x is unsafe"],
   "C3 `n := count(x)` 只报 x，LHS 的 n 被 safetyErrorSlice 压掉, 实际 %s" % errs)

# ==========================================================================
# D. with 与 every
# ==========================================================================

w = With(Call("count", [Ref("input", ["a"]), Var("n")], 1), Var("t"))
ordered, errs = compile_rule(G, [w])
ok(errs == ["var n is unsafe", "var t is unsafe"], "D1 with 目标未绑定, 实际 %s" % errs)

ordered, errs = compile_rule(G, [Eq(Var("t"), Const({"a": 1})), w])
ok(errs == [], "D2 with 目标被绑定后可排, 实际 %s" % errs)

ev = Every(Ref("input", ["items"]), ["k", "v"], [Call("check", [Var("k"), Var("v")], 2)])
ok(check_every(G, ev) == [], "D3 every 体内 key/value 按声明变量处理, 实际 %s" % check_every(G, ev))

ev2 = Every(Ref("unknown", ["items"]), ["k", "v"], [])
ok(check_every(G, ev2) == ["var unknown is unsafe"],
   "D4 every 的 domain 必须已安全, 实际 %s" % check_every(G, ev2))

outer = [Every(Ref("input", ["items"]), ["k", "v"], []),
         Call("count", [Var("k"), Var("n")], 1)]
_, errs = compile_rule(G, outer)
ok("var k is unsafe" in errs, "D5 every 的 key 在体外不安全（负控）, 实际 %s" % errs)

# ==========================================================================
# E. 负控：不重排的朴素实现
# ==========================================================================

def naive_check(globals_, body_):
    safe = set(globals_)
    errs = []
    for e in body_:
        outs = output_vars_for_expr(e, safe)
        for v in e.vars():
            if v not in safe and v not in outs:
                errs.append("var %s is unsafe" % v)
        safe |= outs
    return errs


body = [Eq(Var("b"), Var("a")), Eq(Var("a"), Ref("input", ["i"]))]
ok(naive_check(G, body) != [], "E1 朴素实现会误报: %s" % naive_check(G, body))
ok(compile_rule(G, body)[1] == [], "E1b 带重排的实现不报错")

body = [Eq(Var("a"), Ref("input", ["i"])), Eq(Var("b"), Var("a")), Eq(Var("c"), Var("b"))]
ordered, _ = compile_rule(G, body)
ok([id(x) for x in ordered] == [id(x) for x in body], "E2 重排不是全排")

print("PASS=%d" % PASS)
if FAILS:
    print("FAILED=%d" % len(FAILS))
    sys.exit(1)
print("ALL OK")
