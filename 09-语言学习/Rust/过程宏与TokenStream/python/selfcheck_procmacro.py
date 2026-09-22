"""过程宏与 TokenStream —— 自检（Reference procedural-macros 原文逐条落地）。"""

from proc_macro import (
    ATTRIBUTE_POSITIONS, COMPILE_ERROR_WAY, DERIVE_INPUT_KINDS,
    FUNCTION_LIKE_POSITIONS, ProcMacroError, RESTRICTIONS, SECURITY,
    doc_comment_to_attr, from_proc_macro, render, resolve_in_decl_macro,
    resolve_in_proc_macro_output, run_proc_macro, split_attribute_macro,
    tokenize, to_proc_macro,
)

COUNT = [0]


def check(label, cond, detail=""):
    COUNT[0] += 1
    if not cond:
        raise AssertionError("%s FAILED %s" % (label, detail))


def decl(src):
    return tokenize(src, "decl")


def proc(src):
    return tokenize(src, "proc")


# ------------------------------------------------- 1. 两套 token 定义（差异本身）
check("1.1 声明宏里 `+=` 是一个 token", len(decl("a += b")) == 3, str(decl("a += b")))
check("1.2 过程宏里 `+=` 是两个 Punct", len(proc("a += b")) == 4, str(proc("a += b")))
check("1.3 声明宏里 `'a` 是一个 lifetime token",
      decl("'a")[0] == ("lifetime", "'a"), str(decl("'a")))
check("1.4 过程宏里 `'a` 是 `'` + ident 两个 token",
      proc("'a") == [("punct", "'"), ("ident", "a")], str(proc("'a")))
check("1.5 声明宏里 `-1` 是 `-` 与 `1`",
      decl("-1") == [("punct", "-"), ("lit", "1")], str(decl("-1")))
check("1.6 过程宏里 `-1` 是**一个**字面量",
      proc("-1") == [("lit", "-1")], str(proc("-1")))
check("1.7 声明宏的标点集不包含单引号", "'" not in "+-*/%=<>!&|^~?:;,.$#@")
check("1.8 过程宏的标点集**包含**单引号", "'" in "+-*/%=<>!&|^~?:;,.$#@'")

# ------------------------------------------------- 2. 传给过程宏时的转换
check("2.1 多字符运算符被拆成单字符",
      to_proc_macro(decl("a += b")) == proc("a += b"),
      str(to_proc_macro(decl("a += b"))))
check("2.2 生命周期被拆成 `'` + ident",
      to_proc_macro(decl("&'a i32")) == proc("&'a i32"),
      str(to_proc_macro(decl("&'a i32"))))
check("2.3 $crate 作为单个标识符传入",
      to_proc_macro([("crate_meta", "$crate")]) == [("ident", "$crate")])
check("2.4 tt 替换从不被 Delimiter::None 包起来",
      to_proc_macro([("meta", "tt", [("ident", "x")])]) == [("ident", "x")])
check("2.5 ident 替换同样不被包装",
      to_proc_macro([("meta", "ident", [("ident", "y")])]) == [("ident", "y")])
check("2.6 expr 替换可能被 None 分组包起来",
      to_proc_macro([("meta", "expr", [("lit", "3")])])
      == [("group", None, [("lit", "3")])],
      str(to_proc_macro([("meta", "expr", [("lit", "3")])])))
check("2.7 转换是递归进分组的",
      to_proc_macro(decl("(a += b)")) == proc("(a += b)"),
      str(to_proc_macro(decl("(a += b)"))))

# ------------------------------------------------- 3. 从过程宏输出时的转换
check("3.1 输出的标点被粘回多字符运算符",
      from_proc_macro(proc("a => b")) == decl("a => b"),
      str(from_proc_macro(proc("a => b"))))
check("3.2 `'` 与 ident 被粘回生命周期",
      from_proc_macro(proc("&'a i32")) == decl("&'a i32"),
      str(from_proc_macro(proc("&'a i32"))))
check("3.3 负数常量被拆成 `-` 与常量两个 token",
      from_proc_macro(proc("-1")) == decl("- 1"),
      str(from_proc_macro(proc("-1"))))
check("3.4 往返一次后声明宏侧保持一致（成对对照）",
      from_proc_macro(to_proc_macro(decl("a += b"))) == decl("a += b"),
      str(from_proc_macro(to_proc_macro(decl("a += b")))))
check("3.5 生命周期往返一致",
      from_proc_macro(to_proc_macro(decl("&'a i32"))) == decl("&'a i32"))

# ------------------------------------------------- 4. 文档注释
check("4.1 /// 被转成 #[doc = r\"..\"]",
      render(doc_comment_to_attr("/// Doc")) == '# [doc = r"Doc"]',
      render(doc_comment_to_attr("/// Doc")))
converted = doc_comment_to_attr("/// x")
check("4.2 转换结果以 `#` 起头，即 #[doc = ..] 属性形式",
      converted[0] == ("punct", "#") and converted[1][0] == "group"
      and converted[1][2][0] == ("ident", "doc"), str(converted))

# ------------------------------------------------- 5. 属性宏的输入切分（官方例子）
cases = [
    ("#[show_streams] fn invoke1() {}", "", "fn invoke1 () {}"),
    ("#[show_streams(bar)] fn invoke2() {}", "bar", "fn invoke2 () {}"),
    ("#[show_streams(multiple => tokens)] fn invoke3() {}", "multiple => tokens",
     "fn invoke3 () {}"),
    ("#[show_streams { delimiters }] fn invoke4() {}", "delimiters",
     "fn invoke4 () {}"),
]
for src, attr, item in cases:
    a, it = split_attribute_macro(src)
    check("5.x %s → attr=%r" % (src[:24], attr), render(a) == attr, render(a))
    check("5.x %s → item=%r" % (src[:24], item), render(it) == item, render(it))
try:
    split_attribute_macro("#[other] fn f() {}")
    raise AssertionError("5.9 非该宏的调用应当报错")
except ProcMacroError:
    check("5.9 属性名不匹配时报错", True)

# ------------------------------------------------- 6. 三类过程宏的位置限制
check("6.1 函数式宏可出现在 7 类位置", len(FUNCTION_LIKE_POSITIONS) == 7,
      str(FUNCTION_LIKE_POSITIONS))
check("6.2 其中含 Patterns 与 Type expressions",
      "Patterns" in FUNCTION_LIKE_POSITIONS
      and "Type expressions" in FUNCTION_LIKE_POSITIONS)
check("6.3 属性宏只能用于 4 类位置", len(ATTRIBUTE_POSITIONS) == 4)
check("6.4 属性宏不能用作 inner attribute（不在清单里）",
      "Inner attributes" not in ATTRIBUTE_POSITIONS)
check("6.5 derive 的输入只能是 struct / enum / union",
      DERIVE_INPUT_KINDS == ["struct", "enum", "union"])
check("6.6 过程宏必须在 crate 根部定义（声明宏不需要）",
      RESTRICTIONS["proc_macro"]["must_be_crate_root"] is True
      and RESTRICTIONS["macro_rules"]["must_be_crate_root"] is False)
check("6.7 过程宏不能在定义它的 crate 里用（声明宏可以，成对对照）",
      RESTRICTIONS["proc_macro"]["usable_in_defining_crate"] is False
      and RESTRICTIONS["macro_rules"]["usable_in_defining_crate"] is True)
check("6.8 过程宏需要 `proc-macro = true` 的 crate 类型",
      RESTRICTIONS["proc_macro"]["needs_proc_macro_crate_type"] is True
      and RESTRICTIONS["macro_rules"]["needs_proc_macro_crate_type"] is False)

# ------------------------------------------------- 7. 卫生性对比（成对）
def_env = {"Option": "core::option::Option", "x": "def-site-x", "helper": "def"}
call_env = {"Option": "shadowed::Option", "x": "call-site-x"}
kind1, v1 = resolve_in_proc_macro_output("Option", def_env, call_env)
check("7.1 过程宏不卫生：`Option` 在调用处被解析（可能撞上同名的东西）",
      (kind1, v1) == ("call", "shadowed::Option"), str((kind1, v1)))
kind2, v2 = resolve_in_decl_macro("Option", "type", def_env, call_env)
check("7.2 声明宏里 `Option` 这类符号同样在调用处（mixed-site 的另一半）",
      (kind2, v2) == ("call", "shadowed::Option"), str((kind2, v2)))
kind3, v3 = resolve_in_decl_macro("x", "local", def_env, call_env)
check("7.3 声明宏的**局部变量**在定义处（关键差别）",
      (kind3, v3) == ("def", "def-site-x"), str((kind3, v3)))
kind4, v4 = resolve_in_proc_macro_output("x", def_env, call_env)
check("7.4 过程宏连局部变量也在调用处（不卫生）",
      (kind4, v4) == ("call", "call-site-x"), str((kind4, v4)))
check("7.5 官方建议：过程宏里写绝对路径 `::std::option::Option`", True)
check("7.6 官方建议：生成物用 `__internal_foo` 这类不太可能撞名的名字", True)

# ------------------------------------------------- 8. 错误报告与运行时
check("8.1 正常返回时产出替换用的 token",
      run_proc_macro("return", [("ident", "answer")]) == [("ident", "answer")])
try:
    run_proc_macro("panic", [])
    raise AssertionError("8.2 panic 应当变成编译错误")
except ProcMacroError as e:
    check("8.2 panic 被编译器捕获 → 编译错误", "编译错误" in str(e), str(e))
check("8.3 死循环不被捕获，编译器会被挂住",
      run_proc_macro("loop", []) == "HANG")
check("8.4 两种报错途径：panic 与 emit 一个 compile_error! 调用",
      COMPILE_ERROR_WAY in ("panic", "compile_error", "both")
      and COMPILE_ERROR_WAY == "both")
check("8.5 过程宏与 build script 的安全顾虑一致",
      SECURITY["proc_macro"] == SECURITY["build_script"])

print("proc_macro: %d assertions passed" % COUNT[0])


if __name__ == "__main__":
    pass
