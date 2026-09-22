"""trait 高级形态 —— 自检（The Book ch20-02 + Reference traits/associated-items）。"""

from traits import (
    ASYNC_FN_TRAITS, Impl, Item, Registry, Trait, conflicting, dyn_compatible,
    dyn_object_name, needs_annotation,
)

COUNT = [0]


def check(label, cond, detail=""):
    COUNT[0] += 1
    if not cond:
        raise AssertionError("%s FAILED %s" % (label, detail))


def reg_with(tr):
    reg = Registry()
    reg.add(tr)
    return reg


# ---------------------------------------------------- 1. 基线：普通 trait 可 dyn 兼容
base = Trait("Shape", items=[Item("fn", "area", self_kind="&self")])
r = reg_with(base)
ok, why = dyn_compatible(r, "Shape")
check("1.1 只有 &self 方法的 trait 是 dyn 兼容", ok, str(why))

# ---------------------------------------------------- 2. 关联常量 → 不兼容（成对）
with_const = Trait("WithConst", items=[
    Item("fn", "area", self_kind="&self"), Item("const", "MAX")])
r2 = reg_with(with_const)
ok2, why2 = dyn_compatible(r2, "WithConst")
check("2.1 带关联常量的 trait 不 dyn 兼容", not ok2)
check("2.2 原因指向关联常量", any("关联常量" in w for w in why2), str(why2))
check("2.3 去掉关联常量就恢复（成对对照）",
      dyn_compatible(reg_with(Trait("NoConst", items=[Item("fn", "area", self_kind="&self")])),
                     "NoConst")[0])

# ------------------------------------------------ 3. 关联类型 vs GAT：关键区分
assoc_only = Trait("Iter", items=[Item("type", "Item"),
                                  Item("fn", "next", self_kind="&mut self")])
ok3, why3 = dyn_compatible(reg_with(assoc_only), "Iter")
check("3.1 **不带**泛型的关联类型仍然 dyn 兼容（`dyn Iter<Item=u32>` 合法）",
      ok3, str(why3))
gat = Trait("Lend", items=[Item("type", "Lender", lifetimes=("'a",)),
                           Item("fn", "lend", self_kind="&mut self")])
ok4, why4 = dyn_compatible(reg_with(gat), "Lend")
check("3.2 关联类型带泛型参数（GAT）→ 不 dyn 兼容", not ok4)
check("3.3 原因点名 GAT", any("GAT" in w for w in why4), str(why4))
tygat = Trait("Lend2", items=[Item("type", "Lender", generics=("T",))])
check("3.4 类型参数版的 GAT 同样不兼容（成对对照）",
      not dyn_compatible(reg_with(tygat), "Lend2")[0])

# ---------------------------------------------------- 4. supertrait 的传递与限制
r5 = Registry()
r5.add(Trait("Shape", items=[Item("fn", "area", self_kind="&self")]))
r5.add(Trait("Circle", supertraits=["Shape"], items=[Item("fn", "r", self_kind="&self")]))
check("4.1 子 trait 继承 supertrait 的 dyn 兼容性", dyn_compatible(r5, "Circle")[0])
r5.add(Trait("Bad", supertraits=["Sized"], items=[Item("fn", "f", self_kind="&self")]))
ok6, why6 = dyn_compatible(r5, "Bad")
check("4.2 Sized 作为 supertrait → 不兼容", not ok6)
check("4.3 原因点名 Sized", any("Sized" in w for w in why6), str(why6))
r5.add(Trait("WithConst2", items=[Item("const", "C")]))
r5.add(Trait("Sub", supertraits=["WithConst2"], items=[Item("fn", "g", self_kind="&self")]))
check("4.4 supertrait 不兼容会传染给子 trait", not dyn_compatible(r5, "Sub")[0])
check("4.5 supertrait 闭包是传递的", r5.closure_of("Circle") == ["Circle", "Shape"],
      str(r5.closure_of("Circle")))
names = sorted({i.name for i in r5.items_in_scope("Circle")})
check("4.6 bound 一个 trait 就拿到 supertrait 的关联项", names == ["area", "r"], str(names))
self_super = Registry()
self_super.add(Trait("Loop", supertraits=["Loop"]))
check("4.7 自己是自己的 supertrait 属于错误", self_super.supertrait_cycle("Loop"))
check("4.8 正常 trait 不成环", not r5.supertrait_cycle("Circle"))

# ------------------------------------------------ 5. 方法的可分派性（官方清单）
def fn_trait(**kw):
    return Trait("T", items=[Item("fn", "m", **kw)])


for recv in ["&self", "&mut self", "Box<Self>", "Rc<Self>", "Arc<Self>", "Pin<&Self>"]:
    check("5.x 接收者 %s 可分派" % recv,
          dyn_compatible(reg_with(fn_trait(self_kind=recv)), "T")[0])
ok7, why7 = dyn_compatible(reg_with(fn_trait(self_kind="self")), "T")
check("5.1 按值 self 接收者：trait 仍 dyn 兼容（显式不可分派）", ok7, str(why7))
ok8, why8 = dyn_compatible(reg_with(fn_trait(self_kind="&self", generics=("X",))), "T")
check("5.2 方法带类型参数且无 where Self: Sized → 不兼容", not ok8)
ok9, _ = dyn_compatible(
    reg_with(fn_trait(self_kind="&self", generics=("X",), where_sized=True)), "T")
check("5.3 加上 where Self: Sized 就恢复（成对对照）", ok9)
ok10, why10 = dyn_compatible(
    reg_with(fn_trait(self_kind="&self", uses_self_other=True)), "T")
check("5.4 Self 出现在接收者以外 → 不兼容", not ok10)
ok11, _ = dyn_compatible(
    reg_with(fn_trait(self_kind="&self", uses_self_other=True, where_sized=True)), "T")
check("5.5 同样加 where Self: Sized 恢复（成对对照）", ok11)
check("5.6 async fn → 不兼容",
      not dyn_compatible(reg_with(fn_trait(self_kind="&self", is_async=True)), "T")[0])
check("5.7 返回位置 impl Trait → 不兼容",
      not dyn_compatible(reg_with(fn_trait(self_kind="&self", rpit=True)), "T")[0])
check("5.8 C 可变参数 → 不兼容",
      not dyn_compatible(reg_with(fn_trait(self_kind="&self", c_variadic=True)), "T")[0])
check("5.9 生命周期参数不算类型参数，仍然兼容",
      dyn_compatible(reg_with(fn_trait(self_kind="&self", lifetimes=("'a",))), "T")[0])

# ------------------------------------------------------ 6. AsyncFn* 被官方点名
for n in sorted(ASYNC_FN_TRAITS):
    check("6.x %s 不 dyn 兼容" % n, not dyn_compatible(Registry(), n)[0])

# --------------------------------------- 7. 关联类型 vs 泛型参数：实现份数
# The Book：泛型参数版可以对同一类型实现多次，关联类型版只能一次。
impls = [Impl("Add", "Counter", args=("i32",)), Impl("Add", "Counter", args=("u32",))]
check("7.1 泛型参数 trait 可对同一类型实现多份",
      len(needs_annotation(impls, "Add", "Counter")) == 2)
check("7.2 参数不同不算冲突", not conflicting(impls, "Add", "Counter", ("i64",)))
check("7.3 参数完全相同才算 E0119", conflicting(impls, "Add", "Counter", ("i32",)))
assoc_impls = [Impl("Iterator", "Counter", args=(), assoc={"Item": "u32"})]
check("7.4 关联类型版参数为空，故第二次实现必然冲突（E0119）",
      conflicting(assoc_impls, "Iterator", "Counter", ()))
check("7.5 关联类型版只有一份实现，调用方无需标注",
      len(needs_annotation(assoc_impls, "Iterator", "Counter")) == 1)

# ------------------------------------------------- 8. 构造 trait object 的写法要求
r8 = Registry()
r8.add(Trait("Iter", params=("T",), items=[Item("type", "Item"),
                                           Item("fn", "next", self_kind="&mut self")]))
check("8.1 泛型参数与关联类型都写全时没有缺失项",
      dyn_object_name(r8, "Iter", {"T": "u32"}, {"Item": "u32"}) == [],
      str(dyn_object_name(r8, "Iter", {"T": "u32"}, {"Item": "u32"})))
check("8.2 漏写关联类型会要求补上（E0191）",
      dyn_object_name(r8, "Iter", {"T": "u32"}, {}) == ["Item=?"],
      str(dyn_object_name(r8, "Iter", {"T": "u32"}, {})))
check("8.3 漏写泛型参数也算缺失",
      dyn_object_name(r8, "Iter", {}, {"Item": "u32"}) == ["T"],
      str(dyn_object_name(r8, "Iter", {}, {"Item": "u32"})))

# ------------------------------------------------- 9. GAT 的写法（Reference 原文）
check("9.1 GAT 的关联类型可以带 where 子句",
      Item("type", "Lender", lifetimes=("'a",)).lifetimes == ["'a"])
r9 = Registry()
r9.add(gat)
check("9.2 `<T as Lend>::Lender<'a>` 的命名方式依赖 lifetimes 字段",
      any(i.kind == "type" and i.lifetimes for i in r9.traits["Lend"].items))

print("traits: %d assertions passed" % COUNT[0])


if __name__ == "__main__":
    pass
