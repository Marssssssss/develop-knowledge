"""trait 高级形态 —— 演示入口。

与 `selfcheck_traits.py` 共用 `traits` 模型；打印五件事：
dyn 兼容性是「逐条否决」、supertrait 的传染性与成环、可分派接收者集合、
`dyn Trait` 必须把参数与关联类型写全（E0191）、
以及「关联类型 vs 泛型参数」在实现次数上的分水岭（E0119 的来源）。
"""

from traits import (
    DISPATCHABLE_RECEIVERS,
    Impl,
    Item,
    Registry,
    Trait,
    conflicting,
    dyn_compatible,
    dyn_object_name,
    needs_annotation,
)


def show(label, reg, name):
    ok, why = dyn_compatible(reg, name)
    print("    %-30s %-8s %s" % (label, "OK" if ok else "不兼容",
                                 "" if ok else "; ".join(why)))


def main() -> None:
    print("[1] dyn 兼容性：六条规则逐条否决")
    reg = Registry()
    reg.add(Trait("Shape", items=[Item("fn", "area", self_kind="&self")]))
    reg.add(Trait("WithConst", items=[Item("const", "C")]))
    reg.add(Trait("WithGAT", items=[Item("type", "Item", generics=("T",))]))
    reg.add(Trait("Bare", items=[Item("fn", "f")]))
    reg.add(Trait("ByValue", items=[Item("fn", "g", self_kind="self")]))
    reg.add(Trait("Generic", items=[Item("fn", "h", self_kind="&self",
                                         generics=("T",))]))
    reg.add(Trait("Rescued", items=[Item("fn", "k", self_kind="&self",
                                         generics=("T",), where_sized=True)]))
    show("只有 &self 方法", reg, "Shape")
    show("带关联常量", reg, "WithConst")
    show("带 GAT", reg, "WithGAT")
    show("没有 self 接收者", reg, "Bare")
    show("按值 self（隐含 Sized）", reg, "ByValue")
    show("方法带泛型参数", reg, "Generic")
    show("同一个方法加 where Self: Sized", reg, "Rescued")

    print("\n[2] supertrait：不兼容会传染，且成环是错误")
    r5 = Registry()
    r5.add(Trait("Shape", items=[Item("fn", "area", self_kind="&self")]))
    r5.add(Trait("Circle", supertraits=["Shape"],
                 items=[Item("fn", "r", self_kind="&self")]))
    r5.add(Trait("WithConst2", items=[Item("const", "C")]))
    r5.add(Trait("Sub", supertraits=["WithConst2"],
                 items=[Item("fn", "g", self_kind="&self")]))
    show("子 trait Circle（super=Shape）", r5, "Circle")
    show("子 trait Sub（super 带 const）", r5, "Sub")
    print("    Circle 的 supertrait 闭包：%s" % sorted(r5.closure_of("Circle")))
    loop = Registry()
    loop.add(Trait("Loop", supertraits=["Loop"]))
    print("    自己是自己的 supertrait：%s" % loop.supertrait_cycle("Loop"))

    print("\n[3] 可分派的接收者（官方清单）")
    print("    %s" % ", ".join(sorted(DISPATCHABLE_RECEIVERS)))

    print("\n[4] dyn Trait 必须写全（E0191）")
    r6 = Registry()
    r6.add(Trait("Container", params=("T",), items=[Item("type", "Item")]))
    print("    什么都不写，缺：%s" % dyn_object_name(r6, "Container"))
    print("    写全后缺：%s"
          % dyn_object_name(r6, "Container", {"T": "u32"}, {"Item": "u8"}))

    print("\n[5] 关联类型 vs 泛型参数：同一类型能实现几次")
    impls = [Impl("Graph", "MyType", ("A",)), Impl("Graph", "MyType", ("B",))]
    print("    同一 trait 同一类型同一参数 → 冲突：%s"
          % conflicting(impls, "Graph", "MyType", ("A",)))
    print("    换个参数再实现一次 → 冲突：%s"
          % conflicting(impls, "Graph", "MyType", ("C",)))
    print("    但调用方要区分，得显式标注的候选有 %d 份"
          % len(needs_annotation(impls, "Graph", "MyType")))


if __name__ == "__main__":
    main()
