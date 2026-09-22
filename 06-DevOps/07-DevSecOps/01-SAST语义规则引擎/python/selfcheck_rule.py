#!/usr/bin/env python3
"""demo571 自检：Semgrep 通用匹配引擎转写的断言集。

每条断言都对应 semgrep 源码里的一处具体分支；运行：

    python selfcheck_rule.py

输出 PASS 计数，全部断言通过时以 exit code 0 结束。
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    Call, DottedName, Ellipsis, FileName, Id, IdInfo, ImportAs, ImportFrom,
    Lit, MatchConfig, Metavar, MetavarEllipsis, Text,
    check_and_add_metavar_binding, equal_ast_bound_code,
    full_module_names, inits_and_rest_of_list_empty_ok, m_list_in_any_order,
    m_list_with_dots, match_any, match_args, normalize_import, render_module,
)

PASS = 0
FAILS = []


def ok(cond, msg):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILS.append(msg)
        print("FAIL: %s" % msg)


def call(name, *args):
    return Call(name, list(args))


CFG = MatchConfig()
L = Lit

# ==========================================================================
# A. `...` 的匹配与 [..., P, ...] 优化
#    源码: m_list_with_dots 的 "Optimization: [..., PAT, ...]" 分支
# ==========================================================================

# A1 两端都是 dots 时走优化分支：PAT 对目标每个元素试一遍
r = match_args([Ellipsis(), L(3), Ellipsis()], [L(1), L(2), L(3), L(4)])
ok(len(r) == 1, "A1 [...,3,...] 对 f(1,2,3,4) 应恰好匹配 1 次, 实际 %d" % len(r))

# A2 目标里没有 3 → 不匹配
r = match_args([Ellipsis(), L(3), Ellipsis()], [L(1), L(2), L(4)])
ok(len(r) == 0, "A2 [...,3,...] 对 f(1,2,4) 不应匹配, 实际 %d" % len(r))

# A3 优化分支下 $X 会把每个元素各绑定一次（fold 的 >||> 是并集，不是首个即停）
r = match_args([Ellipsis(), Metavar("X"), Ellipsis()], [L(1), L(2), L(3), L(4)])
ok(len(r) == 4, "A3 [...,$X,...] 应产生 4 个环境, 实际 %d" % len(r))
ok(sorted(e["X"].value for e in r) == [1, 2, 3, 4],
   "A3b $X 的取值集合应为 {1,2,3,4}, 实际 %s" % sorted(e["X"].value for e in r))

# A4 `...` 可以匹配 0 个元素
r = match_args([L(1), Ellipsis()], [L(1)])
ok(len(r) == 1, "A4 f(1,...) 应匹配 f(1), 实际 %d" % len(r))

# A5 但前置元素必须存在
r = match_args([L(1), Ellipsis()], [])
ok(len(r) == 0, "A5 f(1,...) 不应匹配 f(), 实际 %d" % len(r))

# A6 吃掉多个
r = match_args([L(1), Ellipsis()], [L(1), L(2), L(3)])
ok(len(r) == 1, "A6 f(1,...) 应匹配 f(1,2,3), 实际 %d" % len(r))

# ==========================================================================
# B. less_is_ok：空模式列表能否匹配非空目标
#    源码: `| [], _ :: _ when less_is_ok -> return ()`
# ==========================================================================

ok(len(m_list_with_dots([], [L(1)], {}, True, match_any)) == 1,
   "B1 less_is_ok=true 时 [] 可匹配 [1]")
ok(len(m_list_with_dots([], [L(1)], {}, False, match_any)) == 0,
   "B2 less_is_ok=false 时 [] 不能匹配 [1]")

# B3/B4 `$...ARGS` 的切分枚举数量直接受 less_is_ok 控制
tgt = [L(1), L(2)]
from main import m_list_with_dots_and_metavar_ellipsis as m_mv  # noqa: E402

r0 = m_mv([MetavarEllipsis("ARGS")], tgt, {}, False, match_any, CFG)
ok(len(r0) == 1, "B3 less_is_ok=false 时 f($...ARGS) 只有 1 个绑定, 实际 %d" % len(r0))
ok(r0 and r0[0]["ARGS"].value == [L(1), L(2)],
   "B3b ARGS 必须是完整列表 [1,2], 实际 %r" % (r0[0]["ARGS"].value if r0 else None))

r1 = m_mv([MetavarEllipsis("ARGS")], tgt, {}, True, match_any, CFG)
ok(len(r1) == 3, "B4 less_is_ok=true 时 f($...ARGS) 有 3 个绑定, 实际 %d" % len(r1))
ok([e["ARGS"].value for e in r1] == [[], [L(1)], [L(1), L(2)]],
   "B4b 绑定顺序应是 [] / [1] / [1,2], 实际 %s" % [e["ARGS"].value for e in r1])

# B5 `$...A, 3`：尾部锚点把切分钉死
r = m_mv([MetavarEllipsis("A"), L(3)], [L(1), L(2), L(3)], {}, False, match_any, CFG)
ok(len(r) == 1 and r[0]["A"].value == [L(1), L(2)],
   "B5 f($...A,3) 对 f(1,2,3) 应得 A=[1,2], 实际 %s" % (r[0]["A"].value if r else None))

# B6 尾部锚点不满足 → 无匹配
r = m_mv([MetavarEllipsis("A"), L(9)], [L(1), L(2), L(3)], {}, False, match_any, CFG)
ok(len(r) == 0, "B6 f($...A,9) 对 f(1,2,3) 不应匹配, 实际 %d" % len(r))

# ==========================================================================
# C. 元变量绑定一致性（check_and_add_metavar_binding / equal_ast_bound_code）
# ==========================================================================

# C1 同名重复绑定 → 通过
a1 = Id("a", IdInfo(resolved="Local", sid=1))
r = match_args([Metavar("X"), Metavar("X")], [a1, a1])
ok(len(r) == 1, "C1 f($X,$X) 对 f(a,a) 应匹配, 实际 %d" % len(r))

# C2 不同名 → 失败
b1 = Id("b", IdInfo(resolved="Local", sid=2))
r = match_args([Metavar("X"), Metavar("X")], [a1, b1])
ok(len(r) == 0, "C2 f($X,$X) 对 f(a,b) 不应匹配, 实际 %d" % len(r))

# C3 匿名元变量 $_ 不进环境 → 不参与合一
r = match_args([Metavar("_"), Metavar("_")], [a1, b1])
ok(len(r) == 1, "C3 f($_,$_) 应匹配 f(a,b), 实际 %d" % len(r))
ok(r and r[0] == {}, "C3b 匿名元变量不应写入环境, 实际 %r" % (r[0] if r else None))

# C4 `self.$FOO = $FOO` 能匹配 `self.foo = foo`：未解析的 id 一律放行
u1 = Id("foo", IdInfo(resolved=None, sid=0))
u2 = Id("foo", IdInfo(resolved=None, sid=0))
r = match_args([Metavar("FOO"), Metavar("FOO")], [u1, u2])
ok(len(r) == 1, "C4 未解析 id 之间应放行（self.$FOO=$FOO 匹配 self.foo=foo）")

# C5 同名不同 sid：默认（unify_ids_strictly=false）放行，严格模式拒绝
x1 = Id("x", IdInfo(resolved="Local", sid=1))
x2 = Id("x", IdInfo(resolved="Local", sid=2))
r = match_args([Metavar("X"), Metavar("X")], [x1, x2], cfg=MatchConfig(False))
ok(len(r) == 1, "C5 默认非严格：同名不同作用域的 id 仍相等")
r = match_args([Metavar("X"), Metavar("X")], [x1, x2], cfg=MatchConfig(True))
ok(len(r) == 0, "C5b 严格模式：同名不同 sid 应不等")

# C6 源码里显式的不对称：Some resolved vs 无 id_info
resolved = Id("x", IdInfo(resolved="Local", sid=7))
noinfo = Id("x", None)
ok(equal_ast_bound_code(resolved, noinfo, CFG) is False,
   "C6 已解析 id 与无 id_info 的 id 比较应为 False（Some _, None -> false）")
ok(equal_ast_bound_code(noinfo, resolved, CFG) is True,
   "C6b 反过来（None, _ -> true）却为 True —— 源码判据不对称")
# 这个不对称在匹配中体现为「参数顺序敏感」
r = match_args([Metavar("X"), Metavar("X")], [resolved, noinfo])
ok(len(r) == 0, "C6c 先绑定已解析 id 再遇无 info id → 不匹配")
r = match_args([Metavar("X"), Metavar("X")], [noinfo, resolved])
ok(len(r) == 1, "C6d 顺序反过来则匹配 —— 同一对实参，结果不同")

# C7 大小写：必须两侧都标记为 case-insensitive 才忽略大小写
ci1 = Id("ABC", IdInfo(resolved="Local", sid=1, case_insensitive=True))
ci2 = Id("abc", IdInfo(resolved="Local", sid=1, case_insensitive=True))
ok(equal_ast_bound_code(ci1, ci2, CFG) is True, "C7 两侧均为 CI 时大小写不敏感")
ok(equal_ast_bound_code(ci1, Id("abc", IdInfo(resolved="Local", sid=1)), CFG) is False,
   "C7b 只有一侧是 CI 时仍按精确比较")

# C8 Text（Ruby atom）与「未解析 Id」可互换，与已解析 Id 不可
ok(equal_ast_bound_code(u1, Text("foo"), CFG) is True, "C8 未解析 Id 与 Text 可比")
ok(equal_ast_bound_code(resolved, Text("x"), CFG) is False, "C8b 已解析 Id 与 Text 不可比")

# C9 见证值保持第一次绑定的那个
w = check_and_add_metavar_binding({}, "X", ci1, CFG)
w2 = check_and_add_metavar_binding(w, "X", ci2, CFG)
ok(w2 is not None and w2["X"].name == "ABC",
   "C9 重复绑定后见证值仍是首次绑定的 ABC, 实际 %r" % (w2["X"].name if w2 else None))

# ==========================================================================
# D. AC（结合律/交换律）匹配：m_list_in_any_order
# ==========================================================================

r = m_list_in_any_order([Metavar("A"), Metavar("B")], [L(1), L(2)], {}, False, match_any)
ok(len(r) == 2, "D1 两个元变量的 AC 匹配应给出 2 种绑定, 实际 %d" % len(r))
ok(sorted((e["A"].value, e["B"].value) for e in r) == [(1, 2), (2, 1)],
   "D1b 绑定集合应为 (1,2) 与 (2,1)")

r = m_list_in_any_order([L(1)], [L(1), L(2)], {}, True, match_any)
ok(len(r) == 1, "D2 less_is_ok=true 时单元素模式可匹配两元素目标, 实际 %d" % len(r))
r = m_list_in_any_order([L(1)], [L(1), L(2)], {}, False, match_any)
ok(len(r) == 0, "D3 less_is_ok=false 时单元素模式不能匹配两元素目标")

# ==========================================================================
# E. import 归一化（Normalize_generic）
# ==========================================================================

m = normalize_import(True, ImportFrom(DottedName(["foo"]), ["bar"]))
ok([render_module(x) for x in m] == ["foo.bar"],
   "E1 from foo import bar -> foo.bar, 实际 %s" % (m and [render_module(x) for x in m]))

m = normalize_import(True, ImportFrom(DottedName(["foo", "bar"]), ["baz"]))
ok([render_module(x) for x in m] == ["foo.bar.baz"], "E2 from foo.bar import baz -> foo.bar.baz")

m = normalize_import(True, ImportFrom(DottedName(["foo"]), ["bar", "baz"]))
ok([render_module(x) for x in m] == ["foo.bar", "foo.baz"], "E3 多名字逐个展开")

m = normalize_import(True, ImportAs(DottedName(["x"])))
ok([render_module(x) for x in m] == ["x"], "E4 import x as y 丢掉别名后是 x")

# E5/E6: FileName + 有 imports 时，模式侧返回 None（不参与匹配），
#        非模式侧（JS 的 import x from "path"）按 bugfix 返回 FileName
ok(normalize_import(True, ImportFrom(FileName("path"), ["x"])) is None,
   "E5 模式侧 from \"path\" import x -> None")
m = normalize_import(False, ImportFrom(FileName("path"), ["x"]))
ok(m is not None and render_module(m[0]) == "FileName(path)",
   "E6 非模式侧（JS）from \"path\" import x -> FileName(path)")
m = normalize_import(True, ImportAs(FileName("path")))
ok(m is not None and render_module(m[0]) == "FileName(path)",
   "E7 import \"path\"（无 imports）两侧都是 FileName(path)")

# ==========================================================================
# F. 切分枚举的形状（inits_and_rest_of_list_empty_ok）
# ==========================================================================

ok(inits_and_rest_of_list_empty_ok([]) == [([], [])], "F1 空列表切分是 [([],[])]")
s = inits_and_rest_of_list_empty_ok([1, 2, 3])
ok(len(s) == 4, "F2 长度 n 的列表有 n+1 个切分, 实际 %d" % len(s))
ok(s[0] == ([], [1, 2, 3]), "F2b 首个切分是空前缀")
ok(s[-1] == ([1, 2, 3], []), "F2c 末个切分是空剩余")

# ==========================================================================
# G. 组合用例：$...ARGS 与元变量在同一条规则里共存
# ==========================================================================

# f($...A, $X, $X) 应同时要求尾部两个实参相同
r = m_mv([MetavarEllipsis("A"), Metavar("X"), Metavar("X")],
          [L(1), L(2), L(3), L(3)], {}, False, match_any, CFG)
ok(len(r) == 1, "G1 f($...A,$X,$X) 对 f(1,2,3,3) 应匹配, 实际 %d" % len(r))
ok(r and r[0]["A"].value == [L(1), L(2)] and r[0]["X"].value == 3,
   "G1b A=[1,2] 且 X=3, 实际 %r" % (r[0] if r else None))

r = m_mv([MetavarEllipsis("A"), Metavar("X"), Metavar("X")],
          [L(1), L(2), L(3), L(4)], {}, False, match_any, CFG)
ok(len(r) == 0, "G2 尾部两个实参不同则整条规则不匹配")

print("PASS=%d" % PASS)
if FAILS:
    print("FAILED=%d" % len(FAILS))
    sys.exit(1)
print("ALL OK")
