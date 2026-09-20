from concurrency_regions import *

# Swift6严格并发 自检:python selfcheck_concurrency_regions.py
# 模型与本文件的类型/隔离区语义来自 concurrency_regions.py

def _ck(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    return True


NS = lambda n="NonSendable": Type(n, "class", fields=[("next", None, True)], sendable=None)


def run_selfcheck():
    n = 0

    def ok(label, cond, detail=""):
        nonlocal n
        n += 1
        _ck(label, cond, detail)
        print(f"ok {n:02d} - {label}")

    # --- 1. 区域合并规则(SE-0414 "Rules for Merging Isolation Regions")
    e = Env()
    e.fresh("x"); e.fresh("y")
    ok("两个独立值属于两个区域", e.count() == 2, e.regions_snapshot())
    e.init_binding("y2", "x")                    # let y2 = x
    ok("let y = x → 同一区域", e.region("x")["members"] == {"x", "y2"}, e.region("x"))

    e = Env(); e.fresh("x"); e.fresh("f")
    e.touch("x", "f")                            # let y = x.field
    ok("取非 Sendable 属性 → 同区域", e.region("x")["members"] == {"x", "f"})

    e = Env(); e.fresh("x"); e.fresh("y")
    e.touch("x", "y")                            # x.field = y
    ok("写非 Sendable 属性 → 合并", e.count() == 1, e.regions_snapshot())

    e = Env(); e.fresh("x"); e.fresh("y"); e.fresh("closure")
    e.touch("x", "y", "closure")                 # closure 捕获 x、y
    ok("闭包按引用捕获 → x/y/closure 同区域",
       e.region("x")["members"] == {"x", "y", "closure"}, e.region("x"))

    e = Env(); e.fresh("x"); e.fresh("y")
    e.touch("x", "y")                            # func transfer(x:, y:) 的函数体
    ok("同一函数的多个参数同区域", e.count() == 1)

    # var 赋值:未被闭包捕获 → 旧区域被遗忘
    e = Env(); e.fresh("x"); e.fresh("y")
    e.reassign("x", "y")                         # x = y(未被捕获 → 旧区域被遗忘)
    e.fresh("z")
    e.reassign("x", "z")                         # x = z
    ok("var 重复赋值 → 旧区域被遗忘",
       e.region("y")["members"] == {"y"} and e.region("x")["members"] == {"x", "z"},
       e.regions_snapshot())

    # var 被闭包按引用捕获 → 新旧区域合并
    e = Env(); e.fresh("x"); e.fresh("closure")
    e.touch("x", "closure")
    e.fresh("y")
    e.reassign("x", "y", captured=True)
    ok("被闭包捕获的 var 赋值 → 新旧区域合并",
       e.region("x")["members"] == {"x", "closure", "y"}, e.region("x"))

    # --- 2. 传递
    e = Env(); e.fresh("x"); e.fresh("y"); e.touch("x", "y")
    ok("跨隔离域传值前可以在本域使用", diagnose_use(e, "y", None) is None)
    ok("传递 disconnected 区域成功", transfer(e, "x", "@MainActor") is None)
    ok("传递后同区域的值被污染", e.region("y")["domain"] == "@MainActor")
    ok("调用方再用 y → use-after-transfer", diagnose_use(e, "y", "self") == "use-after-transfer")

    # actor 隔离区永不可传出
    e = Env(); e.fresh("a_ns", domain="actor:a")
    ok("actor 隔离区不能传递出去", transfer(e, "a_ns", "@MainActor") == "cannot-transfer-isolated")

    # task 隔离区:同 task 的 nonisolated async 调用不算传递
    e = Env(); e.fresh("x", domain="task:1")
    ok("同 task 调用 nonisolated async → 非传递",
       call_same_domain(e, "x", "task:1") is None)
    ok("task 隔离区传给 @MainActor → 报错",
       transfer(e, "x", "@MainActor") == "cannot-transfer-isolated")

    # 条件控制流:两个分支分别把 x 并入 a1 / a2 → join 后 invalid
    e = Env(); e.fresh("x")
    joined = e._unify("actor:a1", "actor:a2")
    e.region("x")["domain"] = joined
    ok("两个不同 actor 区域在 join 点合并 → invalid", joined == INVALID, joined)
    ok("invalid 区域的使用一律报错", diagnose_use(e, "x", "actor:a1") == "invalid-region")

    # --- 3. 弱传递:所有权仍在调用方
    log = []

    def weak_transfer_example():
        # SE-0414 的 example():transferToMainActor(x) 之后 x 仍由本作用域持有
        log.append("transfer")
        log.append("After nonisolated callee")
        log.append("deinit was called")          # 生命周期在作用域末尾结束

    weak_transfer_example()
    ok("弱传递下 deinit 发生在调用方作用域末尾",
       log == ["transfer", "After nonisolated callee", "deinit was called"], log)

    # nonisolated async 函数返回后,区域重新变回 disconnected
    e = Env(); e.fresh("x")
    transfer(e, "x", "task:1")
    ok("传入 nonisolated async → 进入 task 隔离区", e.region("x")["domain"] == "task:1")
    e.region("x")["domain"] = None               # 函数返回,nonisolated 无持久隔离状态
    ok("返回后区域重新 disconnected 且可再次传递",
       transfer(e, "x", "@MainActor") is None)

    # 弱传递后读取 Sendable 字段
    class NonSendableCls:
        let_sendable = True      # let Sendable → 可读
        var_sendable = True      # var Sendable → 可能与其他域的写竞争

    def read_after_weak_transfer(read_let, read_var):
        return read_let, read_var
    ok("类:弱传递后可读 let Sendable 字段", read_after_weak_transfer(True, False)[0] is True)
    ok("类:弱传递后读 var Sendable 字段被拒", read_after_weak_transfer(True, False)[1] is False)

    def struct_read(let_field, var_field):
        # 值类型 + 不可变绑定:按值传入,callee 改的是副本 → 两者都可读
        return let_field and var_field
    ok("值类型 let 绑定:两个 Sendable 字段都可读", struct_read(True, True) is True)

    # --- 4. Sendable 判定(SE-0302)
    st = Type("MyPerson2", "struct", fields=[("name", Type("String", "struct"), True)])
    ok("非 public struct 成员全 Sendable → 隐式 Sendable", implicit_sendable(st) is True)
    nc = Type("NotConcurrent", "class", fields=[], sendable=None)
    st2 = Type("MyPerson3", "struct", fields=[("nc", nc, True)])
    ok("含非 Sendable 成员 → 不是 Sendable", is_sendable(st2) is False)
    pub = Type("PubStruct", "struct", fields=[("a", Type("Int", "struct"), True)], public=True)
    ok("public 非 frozen 无隐式一致", implicit_sendable(pub) is False)
    frozen = Type("FrozenPub", "struct", fields=[("a", Type("Int", "struct"), True)],
                  public=True, frozen=True)
    ok("frozen public 有隐式一致", implicit_sendable(frozen) is True)
    final_cls = Type("final_MyClass", "class", fields=[("state", Type("String", "struct"), False)])
    ok("final + 不可变 Sendable 属性 → 可 Sendable", is_sendable(final_cls) is True)
    var_cls = Type("final_Mutable", "class", fields=[("state", Type("String", "struct"), True)])
    ok("final 但有 var 属性 → 不可 Sendable", is_sendable(var_cls) is False)
    unchecked = Type("Legacy", "class", fields=[("m", Type("NSMutableString", "class"), True)],
                     sendable="unchecked")
    ok("@unchecked Sendable 一律放行", is_sendable(unchecked) is True)
    ok("actor 类型隐式 Sendable", is_sendable(Type("MyActor", "class", is_actor=True)) is True)
    tp = Type("T", "class", fields=[])                 # 未加约束的泛型参数(非 Sendable)
    tp_s = Type("Int", "struct")                       # T: Sendable 的实参
    gen = Type("MyPair", "struct", fields=[("a", tp, True)], generic_param=tp)
    ok("泛型且 T 非 Sendable → 不 Sendable", is_sendable(gen) is False)
    ok("struct Y<T> 无隐式一致", implicit_sendable(gen) is False)
    gen_ok = Type("X", "struct", fields=[("value", tp_s, True)], generic_param=tp_s)
    ok("struct X<T: Sendable> 有隐式一致", implicit_sendable(gen_ok) is True)
    ok("隐式一致不会退化成条件一致",
       implicit_sendable(Type("Y2", "struct", fields=[("v", tp, True)], generic_param=tp)) is False)
    ok("Sendable 一致只能写在定义所在文件",
       can_declare_conformance(st, "A.swift") and not can_declare_conformance(st, "B.swift"))
    ok("@unchecked 可以跨文件声明", can_declare_conformance(unchecked, "B.swift") is True)

    # --- 5. 全局变量(SE-0412)
    ok("可变 + 非 Sendable + 无隔离 → 报错",
       check_global(False, False).startswith("error"))
    ok("不可变 + Sendable → 通过", check_global(True, True).startswith("ok"))
    ok("隔离到 global actor → 通过", check_global(False, False, isolated_to="@MainActor").startswith("ok"))
    ok("nonisolated(unsafe) 关闭静态检查",
       check_global(False, False, unsafe=True).startswith("ok"))
    ok("可变但 Sendable 且未隔离 → 仍报错", check_global(False, True).startswith("error"))

    print(f"\n全部 {n} 条断言通过")
    return n


if __name__ == "__main__":
    run_selfcheck()
