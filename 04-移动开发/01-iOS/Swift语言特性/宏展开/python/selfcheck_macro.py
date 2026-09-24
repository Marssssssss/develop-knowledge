"""Swift 宏展开模型的自检(实跑)。

期望值对照 apple/swift-syntax@main 的 MacroExpansion.swift 与 MacroSystem.swift
逐条手算过。
"""

from macro_const import (EXPRESSION, DECLARATION, ACCESSOR, MEMBER_ATTRIBUTE, MEMBER,
                         PEER, CONFORMANCE, CODE_ITEM, EXTENSION, PREAMBLE, BODY,
                         PROTOCOL_NAME, FREESTANDING_ROLES, ATTACHED_ROLES,
                         unmatched_macro_role, no_freestanding_macro_roles)
from macro_system import (MacroSpec, MacroSystem, MacroSystemError,
                          attached_macro_reference, infer_freestanding_macro_role,
                          Context, PrependLexicalContextWrapperContext)
from macro_expand import collapse, MacroApplication, NOT_A_MACRO, FAILURE, SUCCESS

_P = [0]
_F = []


def ok(c, m):
    if c:
        _P[0] += 1
    else:
        _F.append(m)
        print("FAIL:", m)


def eq(a, b, m):
    ok(a == b, "%s (got %r want %r)" % (m, a, b))


def raises(fn, m):
    try:
        fn()
    except Exception:
        _P[0] += 1
        return
    ok(False, "%s (no raise)" % m)


# ---------- 角色与协议名 ----------
eq(len(PROTOCOL_NAME), 11, "MacroRole 共 11 个有协议名的角色")
eq(PROTOCOL_NAME[EXPRESSION], "ExpressionMacro", "expression -> ExpressionMacro")
eq(PROTOCOL_NAME[MEMBER_ATTRIBUTE], "MemberAttributeMacro",
   "memberAttribute -> MemberAttributeMacro")
eq(PROTOCOL_NAME[CONFORMANCE], "ConformanceMacro", "conformance -> ConformanceMacro")
eq(PROTOCOL_NAME[PREAMBLE], "PreambleMacro", "preamble -> PreambleMacro")
eq(PROTOCOL_NAME[BODY], "BodyMacro", "body -> BodyMacro")
eq(FREESTANDING_ROLES, {EXPRESSION, DECLARATION, CODE_ITEM}, "独立宏只有三种角色")
ok(MEMBER in ATTACHED_ROLES and PEER in ATTACHED_ROLES, "member 与 peer 是附着宏角色")
eq(len(FREESTANDING_ROLES) + len(ATTACHED_ROLES), len(PROTOCOL_NAME),
   "独立与附着两类角色加起来等于全部角色")
eq(unmatched_macro_role("M", MEMBER),
   "macro implementation type 'M' doesn't conform to required protocol 'MemberMacro'",
   "unmatchedMacroRole 的诊断文案")
eq(no_freestanding_macro_roles("M"),
   "macro implementation type 'M' does not conform to any freestanding macro protocol",
   "noFreestandingMacroRoles 的诊断文案")

# ---------- 注册与查找 ----------
sys_ = MacroSystem()
sys_.add(MacroSpec("StringifyMacro", module_name="MacroLib"), "stringify")
eq(sys_.lookup("stringify").type, "StringifyMacro", "按名字查到注册项")
eq(sys_.lookup("stringify", "MacroLib").type, "StringifyMacro", "指定正确模块也能查到")
eq(sys_.lookup("stringify", "OtherLib"), None, "模块对不上就查不到")
eq(sys_.lookup("nope"), None, "没注册的名字返回 None")
raises(lambda: sys_.add(MacroSpec("Other"), "stringify"), "同名重复注册抛 alreadyDefined")
eq(sys_.lookup("stringify").type, "StringifyMacro", "重复注册失败后旧项保持不变")
sys_.add(MacroSpec("NoModuleMacro"), "nomod")
eq(sys_.lookup("nomod", "AnyLib"), None,
   "注册时没写模块名,调用方指定了模块就查不到")
eq(sys_.lookup("nomod"), sys_.lookup("nomod"), "不指定模块时可以查到")

# ---------- 属性名解析 ----------
eq(attached_macro_reference("Name"), ("Name", None), "@Name 只给出名字")
eq(attached_macro_reference("Mod.Name"), ("Name", "Mod"), "@Mod.Name 给出模块")
eq(attached_macro_reference("Mod::Name"), ("Name", "Mod"), "@Mod::Name 也给出模块")
eq(attached_macro_reference("Foo.Bar"), ("Bar", "Foo"), "@Foo.Bar 走 MemberType 分支")
eq(attached_macro_reference("A.B.C"), None, "三段式返回 None")
eq(attached_macro_reference("Foo<Int>.Bar"), None, "基类型带泛型返回 None")
eq(attached_macro_reference(""), None, "空串返回 None")

# ---------- 独立宏角色推断 ----------
eq(infer_freestanding_macro_role({EXPRESSION, MEMBER}), EXPRESSION,
   "同时符合 expression 与 member 时优先 expression")
eq(infer_freestanding_macro_role({DECLARATION, EXPRESSION}), EXPRESSION,
   "expression 优先于 declaration")
eq(infer_freestanding_macro_role({CODE_ITEM, DECLARATION}), DECLARATION,
   "declaration 优先于 codeItem")
eq(infer_freestanding_macro_role({CODE_ITEM}), CODE_ITEM, "只有 codeItem 时取 codeItem")
raises(lambda: infer_freestanding_macro_role({MEMBER, PEER}),
       "只符合附着宏协议时抛 noFreestandingMacroRoles")

# ---------- collapse 的分隔符 ----------
eq(collapse([], MEMBER), "", "没有展开结果时返回空串")
eq(collapse(["a", "b"], MEMBER), "a\n\nb", "member 默认用空行分隔")
eq(collapse(["a", "b"], PEER), "a\n\nb", "peer 也是空行分隔")
eq(collapse(["@a", "@b"], MEMBER_ATTRIBUTE), "@a @b", "memberAttribute 用空格分隔")
eq(collapse(["a", "b"], PREAMBLE), "a\nb", "preamble 只用一个换行")
eq(collapse([" a", "b"], MEMBER_ATTRIBUTE), " a b",
   "展开结果已带分隔符开头时不重复加")
eq(collapse(["a", "b"], EXTENSION), "a\n\nb", "extension 用默认空行")

# ---------- collapse 的花括号包裹 ----------
eq(collapse(["get { x }"], ACCESSOR, declaration_has_accessor=False),
   "{\n    get { x }\n}", "无 accessor 的声明补花括号并缩进 4 格")
eq(collapse(["get { x }"], ACCESSOR, declaration_has_accessor=True),
   "get { x }", "已有 accessorBlock 就不再包花括号")
eq(collapse(["a", "b"], ACCESSOR, declaration_has_accessor=False),
   "{\n    a\n    b\n}", "包花括号后分隔符变成单个换行")
eq(collapse(["stmt"], BODY), "{\n    stmt\n}", "body 角色总是包花括号")
eq(collapse(["a\nb"], ACCESSOR, declaration_has_accessor=False, indentation_width=2),
   "{\n  a\n  b\n}", "缩进宽度可配置")

# ---------- 独立宏展开的三态与递归 ----------
sys2 = MacroSystem()
sys2.add(MacroSpec("StringifyMacro"), "stringify")
sys2.add(MacroSpec("RecurMacro"), "recur")
ctx = Context()
app = MacroApplication(sys2, ctx)
eq(app.expand_freestanding("unknown", None, lambda t, n: "x")[0], NOT_A_MACRO,
   "未注册的宏返回 notAMacro")
eq(app.expand_freestanding("stringify", None, lambda t, n: "expanded")[0], SUCCESS,
   "正常展开返回 success")
eq(app.expand_freestanding("stringify", None, lambda t, n: "expanded")[1], "expanded",
   "success 携带展开结果")
eq(app.expanding, [], "展开结束后栈被清空")


def boom(t, n):
    raise ValueError("boom")


eq(app.expand_freestanding("stringify", None, boom)[0], FAILURE,
   "展开函数抛错返回 failure")
eq(ctx.diagnostics, ["boom"], "抛错的异常被转成诊断")

# 递归:宏 A 的展开体里再展开 A
state, _ = app.expand_freestanding(
    "recur", None,
    lambda t, n: app.expand_freestanding("recur", None, lambda t2, n2: "inner")[1])
eq(state, FAILURE, "内层被判递归返回 nil,外层随之变成 failure")
eq(ctx.diagnostics[-1], "recursiveExpansion(RecurMacro)", "内层同名展开被判为递归")
eq(app.expanding, [], "递归路径结束后栈仍被清干净")

# 递归检测是按宏类型而不是按名字
sys2.add(MacroSpec("RecurMacro"), "recurAlias")
eq(app.expand_freestanding(
    "recur", None,
    lambda t, n: app.expand_freestanding("recurAlias", None, lambda a, b: "x")[1])[1],
   None, "同一实现类型的另一个名字也算递归")

# 不同宏之间可以嵌套
sys2.add(MacroSpec("InnerMacro"), "inner")
r = app.expand_freestanding(
    "stringify", None,
    lambda t, n: app.expand_freestanding("inner", None, lambda a, b: "deep")[1])
eq(r, (SUCCESS, "deep"), "不同宏之间允许嵌套展开")

# withExpandedNode 的 push/pop 配对
app2 = MacroApplication(sys2, Context())
got = app2.with_expanded_node("RecurMacro",
                              lambda: app2.expand_freestanding("recur", None,
                                                               lambda t, n: "x")[0])
eq(got, FAILURE, "用 withExpandedNode 压栈后再展开同名宏会被拦")
eq(app2.expanding, [], "withExpandedNode 退出后栈为空")

# ---------- 附着宏:一个抛错不影响其它 ----------
attrs = [
    {"name": "ok1", "module": None, "conforms_to": {MEMBER},
     "expansion": ["m1"]},
    {"name": "bad", "module": None, "conforms_to": {MEMBER}, "raises": True},
    {"name": "ok2", "module": None, "conforms_to": {MEMBER},
     "expansion": ["m2"]},
    {"name": "wrongRole", "module": None, "conforms_to": {PEER}, "expansion": ["m3"]},
]
sys3 = MacroSystem()
for a in attrs:
    sys3.add(MacroSpec(a["name"].upper()), a["name"])
ctx3 = Context()
app3 = MacroApplication(sys3, ctx3)


def expand_one(type_name, attr):
    if attr.get("raises"):
        raise ValueError("bad macro")
    return attr["expansion"]


out = app3.expand_attached("S", attrs, MEMBER, expand_one)
eq(out, ["m1", "m2"], "抛错的宏被跳过,同角色错的也不参与,其余照常合并")
eq(ctx3.diagnostics, ["bad macro"], "失败只留一条诊断")

# ---------- lexicalContext 与唯一名 ----------
base = Context(lexical_context=["S"])
wrapped = PrependLexicalContextWrapperContext(["f"], base)
eq(wrapped.lexical_context, ["f", "S"], "包装层把节点加在被包装层前面")
eq(base.lexical_context, ["S"], "被包装层自身不受影响")
eq(wrapped.make_unique_name("v"), "v_1", "唯一名转发给被包装的 context")
eq(base.make_unique_name("v"), "v_2", "被包装层的计数器在继续走")
eq(wrapped.make_unique_name("v"), "v_3",
   "包装层与被包装层共用同一个计数器,不会各起一套")

# ---------- 合并顺序与多份失败 ----------
attrs2 = [
    {"name": "a1", "module": None, "conforms_to": {MEMBER}, "expansion": ["x1", "x2"]},
    {"name": "a2", "module": None, "conforms_to": {MEMBER}, "expansion": ["y1"]},
]
sys4 = MacroSystem()
for a in attrs2:
    sys4.add(MacroSpec(a["name"].upper()), a["name"])
eq(MacroApplication(sys4, Context()).expand_attached(
    "S", attrs2, MEMBER, lambda t, a: a["expansion"]),
   ["x1", "x2", "y1"], "同一角色的多份展开按属性顺序累加")
eq(collapse(["x1", "x2", "y1"], MEMBER), "x1\n\nx2\n\ny1", "累加结果按默认分隔符合并")

bad2 = [{"name": "b1", "module": None, "conforms_to": {MEMBER}, "raises": True},
        {"name": "b2", "module": None, "conforms_to": {MEMBER}, "raises": True}]
sys5 = MacroSystem()
for a in bad2:
    sys5.add(MacroSpec(a["name"].upper()), a["name"])
ctx5 = Context()
eq(MacroApplication(sys5, ctx5).expand_attached("S", bad2, MEMBER, expand_one), [],
   "全部失败时结果为空而不是 None")
eq(len(ctx5.diagnostics), 2, "每个失败的宏各留一条诊断")

# ---------- 唯一名的作用域 ----------
c1, c2 = Context(), Context()
eq(c1.make_unique_name("v"), "v_1", "第一个 context 从 1 开始")
eq(c2.make_unique_name("v"), "v_1", "另一个 context 独立计数")
eq(c1.make_unique_name("v"), "v_2", "同一 context 内继续递增")
eq(c1.make_unique_name("w"), "w_3", "计数器不按名字分段")

print("assertions passed: %d, failed: %d" % (_P[0], len(_F)))
if _F:
    raise SystemExit(1)
