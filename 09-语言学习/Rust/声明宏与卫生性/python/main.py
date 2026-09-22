"""声明宏与卫生性 —— 演示入口。

与 `selfcheck_macros.py` 共用模型；打印四件事：
跟随集是怎么卡的、宏是一台「不向前看」的解析器、重复（$()*）怎么绑定、
以及 mixed-site hygiene —— 局部变量走定义处、其它符号走调用处。
"""

from hygiene import Macro, Scope, Unresolved, resolve_path_via_crate, resolve_symbol
from macro_match import (
    FOLLOW,
    check_follow_sets,
    match_rule,
    parse_matcher,
    render,
    tokenize,
    transcribe,
)


def expand(matcher, trans, src):
    """一次完整的宏展开：匹配 → 转录 → 还原成源码文本。"""
    binds = match_rule(matcher, src)
    toks = transcribe(trans, binds)
    if len(toks) == 1 and toks[0][0] == "group":
        toks = toks[0][2]
    return render(toks)


def bound(binds, name):
    """把一个元变量绑定还原成源码文本（片段是不透明的 Opaque，要取其 toks）。"""
    v = binds.values.get(name)
    if hasattr(v, "toks"):
        return render(v.toks)
    return render(v)


def main() -> None:
    mac = Macro("m", [], defining_crate="mycrate")

    print("[1] 跟随集：片段后面能跟什么是写死的")
    print("    expr   只能跟 %s" % sorted(FOLLOW["expr"]))
    print("    stmt   只能跟 %s" % sorted(FOLLOW["stmt"]))
    bad = check_follow_sets(parse_matcher(tokenize("$i:expr [ , ]")))
    print("    `$i:expr` 后跟 `[` → %s" % (bad[0] if bad else "合法"))
    print("    `$i:expr` 后跟 `,` → %s"
          % (check_follow_sets(parse_matcher(tokenize("$i:expr ,"))) or "合法"))

    print("\n[2] 匹配是逐 token、不向前看的")
    ok = match_rule("( $x:expr , $y:expr )", "1 + 2 , 3")
    print("    ( $x:expr , $y:expr ) 吃 `1 + 2 , 3` → 命中，x/y 各绑到 `%s` / `%s`"
          % (bound(ok, "x"), bound(ok, "y")))
    try:
        match_rule("( $x:expr , $y:expr )", "1 , 2 , 3")
        print("    （竟然匹配成功）")
    except Exception as e:                       # noqa: BLE001 - 演示用
        print("    吃 `1 , 2 , 3` → 失败：%s" % type(e).__name__)

    print("\n[3] 展开：重复与转录")
    for src in ["1 , 2 , 3", "1", ""]:
        binds = match_rule("( $( $x:expr ),* )", src)
        print("    `%s` -> [%s]" % (src or "(空)",
                                    render(transcribe("[ $( $x ),* ]", binds))))

    print("\n[4] mixed-site hygiene")
    ctx = mac.fresh_ctx()
    def_site = {"local": {"tmp": "def_tmp"}, "fn": {"helper": "def_helper"}}
    call_site = {"local": {"outer": "call_outer"}, "fn": {"helper": "call_helper"}}
    print("    局部变量 tmp（宏内定义）→ %s"
          % (resolve_symbol("tmp", "local", def_site, call_site, ctx,
                            {("tmp", ctx): "call1_tmp"}),))
    print("    函数 helper（两边都有）→ %s"
          % (resolve_symbol("helper", "fn", def_site, call_site, ctx, {}),))
    try:
        resolve_symbol("outer", "local", def_site, call_site, ctx, {})
    except Unresolved as e:
        print("    调用处的局部变量 outer 在宏里不可见 → %s" % e)

    print("\n[5] $crate 指定义宏的 crate，但不绕过可见性")
    visible = ["mycrate::macros::m", "mycrate::util"]
    print("    同 crate 内：$crate::util → %s"
          % (resolve_path_via_crate("$crate::util", mac, "mycrate", visible),))
    try:
        resolve_path_via_crate("$crate::util", mac, "other", visible)
    except Unresolved as e:
        print("    跨 crate 且宏是私有的 → %s" % e)

    print("\n[6] textual scope vs path-based scope")
    scope = Scope()
    scope.define_textual("m", Macro("m1", []))
    after = scope.define_textual("m", Macro("m2", []))
    print("    第二个定义之后调用命中：%s"
          % scope.lookup("m", at_order=after + 1).name)
    print("    只看到第一个定义时命中：%s" % scope.lookup("m", at_order=after).name)


if __name__ == "__main__":
    main()
