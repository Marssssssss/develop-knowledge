"""演示:注册几个宏,走一遍查找、合并、递归检测与唯一名。

运行: python main.py
"""

from macro_const import (EXPRESSION, MEMBER, MEMBER_ATTRIBUTE, ACCESSOR, BODY,
                         PREAMBLE, PROTOCOL_NAME)
from macro_system import (MacroSpec, MacroSystem, attached_macro_reference,
                          infer_freestanding_macro_role, Context,
                          PrependLexicalContextWrapperContext)
from macro_expand import collapse, MacroApplication

ROLES = [("expression", EXPRESSION), ("memberAttribute", MEMBER_ATTRIBUTE),
         ("member", MEMBER), ("accessor", ACCESSOR), ("body", BODY),
         ("preamble", PREAMBLE)]


def main():
    system = MacroSystem()
    system.add(MacroSpec("StringifyMacro", module_name="MacroLib"), "stringify")
    system.add(MacroSpec("PeerMacroImpl"), "makePeer")
    system.add(MacroSpec("RecurMacro"), "recur")

    print("== 属性名 -> (名字, 模块) ==")
    for text in ("stringify", "MacroLib.stringify", "MacroLib::stringify",
                 "Outer.Inner", "A.B.C", "Gen<Int>.Name"):
        print("  @%-22s -> %s" % (text, attached_macro_reference(text)))

    print("\n== 查找:模块必须对得上 ==")
    for name, mod in (("stringify", None), ("stringify", "MacroLib"),
                      ("stringify", "OtherLib")):
        spec = system.lookup(name, mod)
        print("  lookup(%r, %r) -> %s" % (name, mod,
                                          spec.type if spec else "nil"))

    print("\n== 独立宏角色推断 ==")
    print("  同时符合 expression 与 member ->",
          infer_freestanding_macro_role({EXPRESSION, MEMBER}))
    print("  同时符合 codeItem 与 declaration ->",
          infer_freestanding_macro_role({"codeItem", "declaration"}))

    print("\n== collapse:按角色决定分隔符 ==")
    for label, role in ROLES:
        print("  %-16s -> %r" % (label, collapse(["a", "b"], role)))
    print("  %-16s -> %r" % ("accessor(已有)", collapse(["a", "b"], ACCESSOR, True)))

    print("\n== 独立宏展开的三态 ==")
    ctx = Context()
    app = MacroApplication(system, ctx)
    print("  未注册     ->", app.expand_freestanding("nope", None, lambda t, n: "x")[0])
    print("  正常       ->", app.expand_freestanding("stringify", None,
                                                    lambda t, n: "expanded"))

    def raise_it(t, n):
        raise ValueError("macro threw")

    print("  抛错       ->", app.expand_freestanding("stringify", None, raise_it)[0],
          "诊断:", ctx.diagnostics)

    ctx2 = Context()
    app2 = MacroApplication(system, ctx2)
    st, _ = app2.expand_freestanding(
        "recur", None,
        lambda t, n: app2.expand_freestanding("recur", None, lambda a, b: "inner")[1])
    print("  自我递归   ->", st, "诊断:", ctx2.diagnostics)

    print("\n== 唯一名 ==")
    base = Context(lexical_context=["S"])
    outer = PrependLexicalContextWrapperContext(["f"], base)
    print("  被包装层 lexicalContext =", base.lexical_context)
    print("  包装层   lexicalContext =", outer.lexical_context)
    print("  包装层取唯一名 =", outer.make_unique_name("value"))
    print("  被包装层取唯一名 =", base.make_unique_name("value"))
    print("  (两者共用同一个计数器,lexicalContext 不参与唯一名的生成)")

    print("\n== 角色 -> 协议名 ==")
    for role in sorted(PROTOCOL_NAME, key=lambda r: PROTOCOL_NAME[r]):
        print("  %-16s -> %s" % (role, PROTOCOL_NAME[role]))


if __name__ == "__main__":
    main()
