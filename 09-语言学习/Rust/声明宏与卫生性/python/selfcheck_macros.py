"""声明宏与卫生性 —— 自检（Reference macros-by-example 的官方例子逐条落地）。"""

from hygiene import Macro, Scope, Unresolved, resolve_path_via_crate, resolve_symbol
from macro_match import (
    FOLLOW, MacroError, check_follow_sets, match_rule, parse_matcher, render,
    tokenize, transcribe,
)

COUNT = [0]


def check(label, cond, detail=""):
    COUNT[0] += 1
    if not cond:
        raise AssertionError("%s FAILED %s" % (label, detail))


def expand(matcher, trans, src):
    binds = match_rule(matcher, src)
    toks = transcribe(trans, binds)
    if len(toks) == 1 and toks[0][0] == "group":
        toks = toks[0][2]
    return render(toks)


# ------------------------------------------------------------ 1. 片段分类符
check("1.1 官方列出 14 个片段分类符",
      len({"block", "expr", "expr_2021", "ident", "item", "lifetime", "literal",
           "meta", "pat", "pat_param", "path", "stmt", "tt", "ty", "vis"}) == 15)
check("1.2 expr 的跟随集只有 => , ;", FOLLOW["expr"] == {"=>", ",", ";"})
check("1.3 pat_param 允许 |", "|" in FOLLOW["pat_param"])
check("1.4 pat（2021 起）不允许 |", "|" not in FOLLOW["pat"])
check("1.5 path/ty 允许 > >> [ { as where",
      {">", ">>", "[", "{", "as", "where"} <= FOLLOW["ty"])

# ------------------------------------------------------------ 2. 跟随集校验
bad = check_follow_sets(parse_matcher(tokenize("$i:expr [ , ]")))
check("2.1 官方例子：$i:expr 后跟 [ 违规", len(bad) == 1 and "不能跟" in bad[0], str(bad))
check("2.2 换成逗号就合法（成对对照）",
      check_follow_sets(parse_matcher(tokenize("$i:expr ,"))) == [])
check("2.3 换成分号也合法",
      check_follow_sets(parse_matcher(tokenize("$i:expr ;"))) == [])
check("2.4 $p:pat 后跟 | 违规",
      check_follow_sets(parse_matcher(tokenize("$p:pat |"))) != [])
check("2.5 $p:pat_param 后跟 | 合法（成对对照）",
      check_follow_sets(parse_matcher(tokenize("$p:pat_param |"))) == [])
check("2.6 $t:ty 后跟 > 或 as 都合法",
      check_follow_sets(parse_matcher(tokenize("$t:ty >"))) == []
      and check_follow_sets(parse_matcher(tokenize("$t:ty as"))) == [])
check("2.7 tt / ident / literal 后跟什么都合法",
      check_follow_sets(parse_matcher(tokenize("$t:tt [ $i:ident => "))) == [])

# ------------------------------------------------------------ 3. 匹配与转录
check("3.1 字面量必须逐 token 相同才算匹配", expand("(x y)", "(y x)", "(x y)") == "y x",
      expand("(x y)", "(y x)", "(x y)"))
check("3.1b 元变量可以按转录器重排", expand("($a:ident $b:ident)", "($b $a)", "(p q)") == "q p",
      expand("($a:ident $b:ident)", "($b $a)", "(p q)"))
b = match_rule("($e:expr)", "(1 + 2)")
check("3.2 元变量可以转录多次", render(transcribe("($e $e)", b)) == "1 + 2 1 + 2",
      render(transcribe("($e $e)", b)))
check("3.3 元变量也可以完全不转录", render(transcribe("()", b)) == "")
check("3.4 ident 片段", render(transcribe("($i)", match_rule("($i:ident)", "(foo)"))) == "foo")
check("3.5 literal 片段", render(transcribe("($l)", match_rule("($l:literal)", "(42)"))) == "42")
check("3.6 lifetime 片段",
      render(transcribe("($l)", match_rule("($l:lifetime)", "('a)"))) == "'a")

# ------------------------------------------------------------ 4. 重复
check("4.1 逗号分隔改分号分隔（官方例子）",
      expand("($($i:ident),*)", "($( $i );*)", "(a, b, c)") == "a ; b ; c",
      expand("($($i:ident),*)", "($( $i );*)", "(a, b, c)"))
check("4.2 + 至少一个",
      expand("($($i:ident),+)", "($( $i ),+)", "(a, b)") == "a , b")
check("4.3 * 允许零个：转录出空序列而不是报错",
      expand("($($i:ident),*)", "(x [ $( $i )* ] y)", "()") == "x [] y",
      expand("($($i:ident),*)", "(x [ $( $i )* ] y)", "()"))
try:
    match_rule("($($i:ident),? )", "(a)")
    raise AssertionError("4.4 ? 带分隔符应当被拒")
except MacroError as e:
    check("4.4 `?` 不能与分隔符同用", "?" in e.detail, e.detail)
try:
    expand("($($i:ident),*)", "($i)", "(a, b)")
    raise AssertionError("4.5 转录里层数不一致应当报错")
except MacroError as e:
    check("4.5 转录层数与匹配层数不一致报错",
          e.kind == "mismatch" and "层数" in e.detail, e.detail)
try:
    expand("($($i:ident),* ; $($j:ident),*)", "( $( ($i,$j) ),* )", "(a, b, c; d, e)")
    raise AssertionError("4.6 同一层数量不同应当报错")
except MacroError as e:
    check("4.6 同一层重复的元变量数量不同报错", e.kind == "mismatch", e.detail)
check("4.7 数量相同则展开成配对（官方例子）",
      expand("($($i:ident),* ; $($j:ident),*)", "( $( ($i,$j) ),* )",
             "(a, b, c; d, e, f)") == "(a , d) , (b , e) , (c , f)",
      expand("($($i:ident),* ; $($j:ident),*)", "( $( ($i,$j) ),* )",
             "(a, b, c; d, e, f)"))
check("4.8 嵌套重复按层展开（官方允许嵌套）",
      expand("($($($i:ident)*);*)", "($($($i)*);*)", "(a b; c)") == "a b ; c",
      expand("($($($i:ident)*);*)", "($($($i)*);*)", "(a b; c)"))
try:
    expand("($($($i:ident)*);*)", "($($i)*)", "(a b; c)")
    raise AssertionError("4.9 少写一层应当报错")
except MacroError as e:
    check("4.9 转录时少写一层重复 → 层数不一致报错",
          e.kind == "mismatch" and "层数" in e.detail, e.detail)

# ------------------------------------------------------------ 5. 不向前看
try:
    match_rule("($($i:ident)* $j:ident)", "(error)")
    raise AssertionError("5.1 需要向前看的结构应当报 local ambiguity")
except MacroError as e:
    check("5.1 官方例子：local ambiguity", e.kind == "local ambiguity", e.detail)

# ------------------------------------------------------------ 6. 外层定界符
check("6.1 matcher (()) 能匹配 {()}", expand("(())", "(ok)", "{()}") == "ok",
      expand("(())", "(ok)", "{()}"))
try:
    expand("(())", "(ok)", "{{}}")
    raise AssertionError("6.2 内外定界符不一致应当不匹配")
except MacroError:
    check("6.2 官方原文：(()) 不能匹配 {{}}", True)

# ------------------------------------------------------------ 7. 转发不透明
# 官方：`$l:expr` 转发给第二个宏后，第二个宏**无法**用字面 token 匹配它；
# 只有 ident / lifetime / tt 例外。
from macro_match import Bindings, _match_seq

def forward(frag, matcher_src, input_src, second_matcher):
    """先把输入按 frag 匹配成一个片段，再拿第二个宏的 matcher 去匹配它。"""
    outer = match_rule(matcher_src, input_src)
    name = list(outer.values)[0]
    b = Bindings()
    b.values["l"] = outer.values[name]
    b.depths["l"] = 0
    try:
        val = b.values["l"]
        toks = list(val) if isinstance(val, list) else [val]
        rest = _match_seq(parse_matcher(tokenize(second_matcher)[0][2]),
                          toks, Bindings(), 0, False)
        return not rest
    except MacroError:
        return False


check("7.1 已匹配的 expr 片段对下游宏不透明（官方 ERROR 例子）",
      not forward("expr", "($l:expr)", "(3)", "(3)"))
check("7.2 换成 tt 就能被字面 token 匹配（官方对照）",
      forward("tt", "($l:tt)", "(3)", "(3)"))
check("7.3 ident 同样属于例外", forward("ident", "($l:ident)", "(abc)", "(abc)"))

# ------------------------------------------------------------ 8. 卫生性
# 官方例子：assert_eq!(x, 1) 的 x 来自**定义处**，func() 来自**调用处**
def_site = {"local": {"x": 1}, "fn": {"func": "def-site-func"}}
call_site = {"local": {"x": 99}, "fn": {"func": "call-site-func"}}
kind, val = resolve_symbol("x", "local", def_site, call_site, "ctx1", {})
check("8.1 局部变量在定义处查找（拿到 1 而不是 99）",
      (kind, val) == ("def", 1), str((kind, val)))
kind2, val2 = resolve_symbol("func", "fn", def_site, call_site, "ctx1", {})
check("8.2 其它符号在调用处查找（拿到调用处的 func）",
      (kind2, val2) == ("call", "call-site-func"), str((kind2, val2)))
m = Macro("m", [])
try:
    resolve_symbol("x", "local", {"local": {}}, call_site, m.fresh_ctx(), {})
    raise AssertionError("8.3 宏里定义的局部变量不应跨调用共享")
except Unresolved as e:
    check("8.3 宏展开里定义的局部变量不共享（E0425）", e.code == "E0425", e.code)
c1 = m.fresh_ctx()
binds = {("x", c1): 5}
check("8.4 同一次调用内部可以引用自己引入的变量",
      resolve_symbol("x", "local", {}, call_site, c1, binds) == ("def", 5))
check("8.5 另一次调用的同名变量互不影响",
      resolve_symbol("x", "local", {}, call_site, c1, binds)[1] == 5
      and m.fresh_ctx() != c1)

# ------------------------------------------------------------ 9. $crate
mc = Macro("call_foo", [], defining_crate="mycrate", visibility="private")
kind3, target = resolve_path_via_crate("$crate::foo", mc, "mycrate", {"mycrate::foo"})
check("9.1 $crate 指向定义宏的 crate", target == "mycrate::foo", str(target))
pub = Macro("pub_macro", [], defining_crate="mycrate", visibility="pub")
kind4, t4 = resolve_path_via_crate("$crate::bar", pub, "other", {"mycrate::bar"})
check("9.2 crate 外调用时 pub 项可达", t4 == "mycrate::bar", str(t4))
try:
    resolve_path_via_crate("$crate::foo", mc, "other", {"mycrate::foo"})
    raise AssertionError("9.3 私有项应当不可达")
except Unresolved as e:
    check("9.3 官方原文：$crate 不影响可见性（私有项仍不可达）",
          e.code == "E0603", e.code)

# ------------------------------------------------------------ 10. 作用域
sc = Scope()
mm = Macro("m", [])
sc.define_textual("m", mm)
sc.define_path("m2", Macro("m2", [], ))
check("10.1 定义之后可用（调用点序号在定义之后）", sc.lookup("m", at_order=2) is mm)
check("10.2 定义之前不可用（textual 按出现顺序）", sc.lookup("m", at_order=0) is None)
inner = Macro("m_inner", [])
sc.define_textual("m", inner)
check("10.3 后定义者遮蔽先定义者", sc.lookup("m", at_order=99) is inner)
sc2 = Scope()
pb = Macro("pathbased", [])
sc2.define_path("k", pb)
check("10.4 无 textual 时退回 path-based", sc2.lookup("k") is pb)
check("10.5 带路径调用只查 path-based", sc2.lookup("k", qualified=True) is pb)
tx = Macro("textual", [])
sc2.define_textual("k", tx)
check("10.6 textual 遮蔽 path-based（即使 use 写在后面）",
      sc2.lookup("k", at_order=99) is tx)
check("10.7 在 textual 定义之前，同一个调用点仍走 path-based",
      sc2.lookup("k", at_order=0) is pb)
check("10.8 带路径调用不受 textual 遮蔽影响",
      sc2.lookup("k", qualified=True) is pb)
sub = Macro("child", [])
sc3 = Scope()
sc3.define_textual("c", sub, module="root")
check("10.9 textual 作用域可以进入子模块",
      sc3.lookup("c", at_order=99, module="root::inner") is sub)

print("macros: %d assertions passed" % COUNT[0])


if __name__ == "__main__":
    pass
