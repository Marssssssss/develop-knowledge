# -*- coding: utf-8 -*-
"""esm_modules.py 自检：逐条对照 ECMA-262 模块记录的 Link/Evaluate 算法。"""
from esm_modules import Module, Loader, Binding, ImportBinding

ok = 0
fails = []


def check(label, cond, detail=""):
    global ok
    if cond:
        ok += 1
    else:
        fails.append("%s %s" % (label, detail))


def let(name, expr):
    """let name = expr(rec)"""
    return ("let", name, lambda L, r: r.env[name].set(expr(r)))


# --- 1. live binding：导出方后来修改，导入侧立刻可见 ---
b = Module("b", body=[let("x", lambda r: 1)], exports=["x"])
a = Module("a", imports=[("b", ["x"])],
           body=[let("y", lambda r: r.env["x"].get() + 1)], exports=["y"])
L = Loader([b, a])
L.link("a").evaluate("a")
check("A1 求值顺序是依赖优先", L.eval_order == ["b", "a"], L.eval_order)
check("A2 导入侧初值正确", L.get("a", "y") == 2)
check("A3 导出方改值后导入侧可见（live）", (L.set("b", "x", 10), L.get("a", "x"))[1] == 10)
check("A4 但派生出来的 y 仍是求值时刻的快照", L.get("a", "y") == 2, str(L.get("a", "y")))
check("A5 导入绑定是视图而非本体", isinstance(L.records["a"].env["x"], ImportBinding))

# --- 2. 导入侧不可写 ---
try:
    L.set("a", "x", 99)
    check("B1 导入侧赋值抛 TypeError", False, "未抛异常")
except TypeError:
    check("B1 导入侧赋值抛 TypeError", True)
check("B2 导出方仍可写", (L.set("b", "x", 7), L.get("b", "x"))[1] == 7)

# --- 3. TDZ：let 在 InitializeBinding 之前访问 ---
t = Module("t", body=[
    let("q", lambda r: r.env["p"].get() + 1),      # 读尚未初始化的 p
    let("p", lambda r: 1),
])
L2 = Loader([t])
L2.link("t")
check("C1 link 阶段只建绑定不初始化", L2.records["t"].env["p"].initialized is False)
L2.evaluate("t")
check("C2 访问未初始化绑定抛 ReferenceError",
      isinstance(L2.records["t"].evaluation_error, ReferenceError),
      repr(L2.records["t"].evaluation_error))

# --- 4. 函数声明在 InitializeEnvironment 阶段就完成（提升） ---
h = Module("h", body=[
    ("let", "v", lambda L, r: r.env["v"].set(r.env["f"].get()(L, r))),
    ("function", "f", lambda L, r: 42),
])
L3 = Loader([h])
L3.link("h")
check("D1 link 后函数绑定已初始化", L3.records["h"].env["f"].initialized is True)
check("D2 link 后 let 绑定尚未初始化", L3.records["h"].env["v"].initialized is False)
L3.evaluate("h")
check("D3 声明前调用成功（提升）", L3.get("h", "v") == 42)

# --- 5. 循环依赖：函数可以互相调用 ---
ca = Module("ca", imports=[("cb", ["fb"])],
            body=[("function", "fa", lambda L, r: r.env["fb"].get()(L, r)),
                  let("va", lambda r: 1)],
            exports=["fa", "va"])
cb = Module("cb", imports=[("ca", ["fa"])],
            body=[("function", "fb", lambda L, r: 42)], exports=["fb"])
L4 = Loader([ca, cb])
L4.link("ca").evaluate("ca")
check("E1 循环依赖能完成链接", L4.records["ca"].status == "evaluated")
check("E2 跨循环调用成功", L4.records["ca"].env["fa"].get()(L4, L4.records["ca"]) == 42)

# --- 6. 循环依赖里读未初始化的 let：cb 先求值，ca 的 va 还在 TDZ ---
ca2 = Module("ca2", imports=[("cb2", ["vb"])],
             body=[let("va", lambda r: 1)], exports=["va"])
cb2 = Module("cb2", imports=[("ca2", ["va"])],
             body=[let("vb", lambda r: r.env["va"].get())], exports=["vb"])
L5 = Loader([ca2, cb2])
L5.link("ca2").evaluate("ca2")
check("F1 出错时没有任何模块走完求值", L5.eval_order == [], L5.eval_order)
check("F1b 但栈上模块的状态都变成 evaluated",
      L5.records["cb2"].status == "evaluated" and L5.records["ca2"].status == "evaluated",
      "%s/%s" % (L5.records["cb2"].status, L5.records["ca2"].status))
check("F2 循环读未初始化绑定抛 ReferenceError",
      isinstance(L5.records["cb2"].evaluation_error, ReferenceError),
      repr(L5.records["cb2"].evaluation_error))
check("F3 入口模块同样记录该错误",
      isinstance(L5.records["ca2"].evaluation_error, ReferenceError))

# --- 7. 求值错误的记录范围（规范：A、B 有 error，C 没有） ---
g = Module("g", body=[let("z", lambda r: 1)], exports=["z"])
f = Module("f", imports=[("g", ["z"])], body=[let("y", lambda r: 1)],
           exports=["y"], throws=RuntimeError("boom"))
e = Module("e", imports=[("f", ["y"])], body=[let("x", lambda r: 1)])
L6 = Loader([g, f, e])
L6.link("e").evaluate("e")
check("G1 入口记录错误", isinstance(L6.records["e"].evaluation_error, RuntimeError))
check("G2 直接依赖记录错误", isinstance(L6.records["f"].evaluation_error, RuntimeError))
check("G3 更下游的模块没有 EvaluationError", L6.records["g"].evaluation_error is None)
check("G4 更下游的模块仍然 evaluated", L6.records["g"].status == "evaluated",
      L6.records["g"].status)

# --- 8. 模块只求值一次 ---
p = Module("p", body=[let("n", lambda r: 1)], exports=["n"])
q = Module("q", imports=[("p", ["n"])], body=[let("a", lambda r: 1)])
r_ = Module("r", imports=[("p", ["n"])], body=[let("b", lambda r: 1)])
s = Module("s", imports=[("q", []), ("r", [])], body=[let("c", lambda r: 1)])
L7 = Loader([p, q, r_, s])
L7.link("s").evaluate("s")
check("H1 共享依赖只求值一次", L7.eval_order.count("p") == 1, L7.eval_order)
check("H2 全部模块都求值了", len(L7.eval_order) == 4, L7.eval_order)

# --- 9. export * 歧义 ---
s1 = Module("s1", body=[let("n", lambda r: 1)], exports=["n"])
s2 = Module("s2", body=[let("n", lambda r: 2)], exports=["n"])
m = Module("m", star_exports=["s1", "s2"])
L8 = Loader([s1, s2, m])
L8.link("m")
check("I1 两个 star 提供同名导出 → ambiguous",
      L8.records["m"].resolve_export("n") == "ambiguous",
      repr(L8.records["m"].resolve_export("n")))

# --- 10. 链接错误：请求了不存在的导出 ---
bad = Module("bad", imports=[("good", ["nope"])], body=[])
good = Module("good", body=[let("z", lambda r: 1)], exports=["z"])
L9 = Loader([good, bad])
try:
    L9.link("bad")
    check("J1 import 不存在的导出抛 SyntaxError", False, "未抛异常")
except SyntaxError:
    check("J1 import 不存在的导出抛 SyntaxError", True)
check("J2 入口回到 unlinked", L9.records["bad"].status == "unlinked", L9.records["bad"].status)
check("J3 已 linked 的依赖不受影响", L9.records["good"].status == "linked",
      L9.records["good"].status)

print("esm_modules: %d/%d assertions passed" % (ok, ok + len(fails)))
for f in fails:
    print("  FAIL", f)
raise SystemExit(1 if fails else 0)
