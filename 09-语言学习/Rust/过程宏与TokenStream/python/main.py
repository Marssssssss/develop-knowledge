"""过程宏与 TokenStream —— 演示入口。

与 `selfcheck_procmacro.py` 共用 `proc_macro` 模型；打印五件事：
两套 token 定义的跨界转换、doc 注释就是 `#[doc]` 属性、属性宏输入的四种切分、
三类过程宏各自能出现在哪里、以及过程宏**完全不卫生**这件事。
"""

from proc_macro import (
    ATTRIBUTE_POSITIONS,
    COMPILE_ERROR_WAY,
    CRATE_TYPE,
    DERIVE_INPUT_KINDS,
    FUNCTION_LIKE_POSITIONS,
    RESTRICTIONS,
    SECURITY,
    ProcMacroError,
    doc_comment_to_attr,
    from_proc_macro,
    render,
    resolve_in_decl_macro,
    resolve_in_proc_macro_output,
    run_proc_macro,
    split_attribute_macro,
    tokenize,
    to_proc_macro,
)


def decl(src):
    return tokenize(src, "decl")


def proc(src):
    return tokenize(src, "proc")


def main() -> None:
    print("[1] 两套 token 定义：声明宏 vs 过程宏")
    for src in ["a += b", "&'a i32", "- 1"]:
        d, p = decl(src), proc(src)
        print("    %-8s 声明宏 %-2d 个 token / 过程宏 %-2d 个 token"
              % (src, len(d), len(p)))
    print("    双向转换往返一致：%s"
          % (from_proc_macro(to_proc_macro(decl("a += b"))) == decl("a += b")))
    print("    过程宏的 `-1` 回到声明宏侧变成 `%s`"
          % render(from_proc_macro(proc("-1"))))

    print("\n[2] doc 注释就是属性：%s" % render(doc_comment_to_attr("/// Doc")))

    print("\n[3] 属性宏的输入切分（官方四例）")
    for src in ["#[show_streams] fn invoke1() {}",
                "#[show_streams(bar)] fn invoke2() {}",
                "#[show_streams(multiple => tokens)] fn invoke3() {}",
                "#[show_streams { delimiters }] fn invoke4() {}"]:
        attr, item = split_attribute_macro(src)
        print("    %-46s attr=`%s`  item=`%s`"
              % (src[:46], render(attr), render(item)))
    try:
        split_attribute_macro("#[other] fn f() {}")
    except ProcMacroError as e:
        print("    属性名不匹配 → %s" % e)

    print("\n[4] 三类过程宏各自能出现的位置")
    print("    函数式（%d 处）：%s"
          % (len(FUNCTION_LIKE_POSITIONS), ", ".join(FUNCTION_LIKE_POSITIONS)))
    print("    属性（%d 处）：%s"
          % (len(ATTRIBUTE_POSITIONS), ", ".join(ATTRIBUTE_POSITIONS)))
    print("    derive 的输入只能是：%s" % ", ".join(DERIVE_INPUT_KINDS))

    print("\n[5] 过程宏完全不卫生：生成的符号一律在调用处解析")
    def_env = {"Option": "def_Option", "x": "def_x"}
    call_env = {"Option": "call_Option"}
    print("    过程宏输出的 Option（两边都有）→ %s"
          % (resolve_in_proc_macro_output("Option", def_env, call_env),))
    print("    声明宏的局部变量 x（两边都有）→ %s"
          % (resolve_in_decl_macro("x", "local", def_env, call_env),))
    print("    声明宏的 item Option        → %s"
          % (resolve_in_decl_macro("Option", "item", def_env, call_env),))

    print("\n[6] 过程宏的结构性限制（与声明宏成对对照）")
    pm, mbe = RESTRICTIONS["proc_macro"], RESTRICTIONS["macro_rules"]
    print("    crate-type = %s（过程宏 %s / 声明宏 %s）"
          % (CRATE_TYPE, pm["needs_proc_macro_crate_type"],
             mbe["needs_proc_macro_crate_type"]))
    print("    必须在 crate 根：过程宏 %s / 声明宏 %s"
          % (pm["must_be_crate_root"], mbe["must_be_crate_root"]))
    print("    定义它的 crate 自己能用：过程宏 %s / 声明宏 %s"
          % (pm["usable_in_defining_crate"], mbe["usable_in_defining_crate"]))
    print("    安全边界：%s" % SECURITY["proc_macro"])
    print("    报错途径：%s" % COMPILE_ERROR_WAY)
    print("    原样返回输入：%s" % render(run_proc_macro("return", [("ident", "a")])))
    try:
        run_proc_macro("panic", [])
    except ProcMacroError as e:
        print("    panic 式报错：%s" % e)
    print("    死循环式：%s（编译器不会被解救）" % run_proc_macro("loop", []))


if __name__ == "__main__":
    main()
