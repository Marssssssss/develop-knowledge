"""型变与 PhantomData —— 自检（对应 Reference subtyping + Nomicon phantom-data）。"""

from variance import (
    BIV, COV, CONTRA, INV, NAME, PHANTOM_TABLE, Struct, compose, declare,
    equal_types, hrtb_subtype, instantiate, is_subtype, join, outlives,
    phantom_variance, tuple_variance, variance_of,
)

COUNT = [0]


def check(label, cond, detail=""):
    COUNT[0] += 1
    if not cond:
        raise AssertionError("%s FAILED %s" % (label, detail))


T = ("param", "T")
U = ("param", "U")
UNIT = ("unit",)

# ------------------------------------------------------------ 1. 型变代数
check("1.1 逆变∘逆变 = 协变", compose(CONTRA, CONTRA) == COV)
check("1.2 协变∘不变 = 不变", compose(COV, INV) == INV)
check("1.3 不变∘任何 = 不变", compose(INV, COV) == INV and compose(INV, CONTRA) == INV)
check("1.4 join(协变, 逆变) = 不变", join(COV, CONTRA) == INV)
check("1.5 未出现(BIV)不参与合并", join(BIV, CONTRA) == CONTRA)

# ------------------------------------------------- 2. Reference 的内置类型型变表
builtin = [
    ("&'a T", ("ref", "'a", T), {"'a": COV, "T": COV}),
    ("&'a mut T", ("refmut", "'a", T), {"'a": COV, "T": INV}),
    ("*const T", ("ptr_const", T), {"T": COV}),
    ("*mut T", ("ptr_mut", T), {"T": INV}),
    ("[T]", ("slice", T), {"T": COV}),
    ("[T; n]", ("array", T, "3"), {"T": COV}),
    ("fn() -> T", ("fn", (), T), {"T": COV}),
    ("fn(T) -> ()", ("fn", (T,), UNIT), {"T": CONTRA}),
    ("UnsafeCell<T>", ("unsafe_cell", T), {"T": INV}),
    ("PhantomData<T>", ("phantom", T), {"T": COV}),
    ("dyn Trait<T> + 'a", ("dyn", "Trait", "'a", (T,)), {"'a": COV, "T": INV}),
]
for label, ty, expect in builtin:
    for p, v in expect.items():
        got = variance_of(ty, p)
        check("2.x %s 在 %s 上是 %s" % (label, p, NAME[v]), got == v,
              "got %s" % NAME[got])

# ------------------------------------------ 3. 官方 struct 例子（Reference 原文）
# struct Variance<'a, 'b, 'c, T, U: 'a> {
#     x: &'a U,               // 协变 'a / 协变 U
#     y: *const T,            // 协变 T
#     z: UnsafeCell<&'b f64>, // 不变 'b
#     w: *mut U,              // 不变 U
#     f: fn(&'c ()) -> &'c () // 既协又逆 → 不变 'c
# }
def make_variance(with_mut_u=True, fn_form="both"):
    fields = [("ref", "'a", U), ("ptr_const", T),
              ("unsafe_cell", ("ref", "'b", ("named", "f64", ())))]
    if with_mut_u:
        fields.append(("ptr_mut", U))
    if fn_form == "both":
        fields.append(("fn", (("ref", "'c", UNIT),), ("ref", "'c", UNIT)))
    elif fn_form == "cov":
        fields.append(("ref", "'c", UNIT))
    return Struct("Variance", ["'a", "'b", "'c", "T", "U"], fields)


official = declare(make_variance())
check("3.1 'a 协变", variance_of(("named", "Variance", ()), "'a") == COV,
      NAME[variance_of(("named", "Variance", ()), "'a")])
check("3.2 T 协变", variance_of(("named", "Variance", ()), "T") == COV)
check("3.3 'b 不变", variance_of(("named", "Variance", ()), "'b") == INV)
check("3.4 'c 不变（因同一生命周期既在参数位又在返回位）",
      variance_of(("named", "Variance", ()), "'c") == INV)
check("3.5 U 不变（因 *mut U 与 &'a U 冲突）",
      variance_of(("named", "Variance", ()), "U") == INV)

no_mut = declare(make_variance(with_mut_u=False))
check("3.6 去掉 w: *mut U 后 U 变协变（成对对照）",
      variance_of(("named", "Variance", ()), "U") == COV,
      NAME[variance_of(("named", "Variance", ()), "U")])
cov_c = declare(make_variance(fn_form="cov"))
check("3.7 把 f 换成只协变用法后 'c 变协变（成对对照）",
      variance_of(("named", "Variance", ()), "'c") == COV)

# --------------------------------------- 4. 不在 struct 里：每个位置各自算型变
# fn generic_tuple<'short, 'long: 'short>(x: (&'long u32, UnsafeCell<&'long u32>))
src = ("tuple", (("ref", "'long", ("named", "u32", ())),
                 ("unsafe_cell", ("ref", "'long", ("named", "u32", ())))))
dst = ("tuple", (("ref", "'short", ("named", "u32", ())),
                 ("unsafe_cell", ("ref", "'long", ("named", "u32", ())))))
check("4.1 官方例子：元组逐位置收缩 'long → 'short 成立", is_subtype(src, dst))
check("4.2 若把元组当整体类型算，'long 是不变（对照）",
      tuple_variance(src, "'long") == INV, NAME[tuple_variance(src, "'long")])
wrapper = declare(Struct("Wrap", ["'long"], list(src[1])))
check("4.3 同样的字段放进 struct 就真的不变了",
      variance_of(("named", "Wrap", ()), "'long") == INV)

# fn takes_fn_ptr<'short, 'middle: 'short>(f: fn(&'middle ()) -> &'middle ())
f_src = ("fn", (("ref", "'middle", UNIT),), ("ref", "'middle", UNIT))
f_dst = ("fn", (("ref", "'static", UNIT),), ("ref", "'short", UNIT))
check("4.4 官方例子：参数位延长到 'static、返回位收缩到 'short 同时成立",
      is_subtype(f_src, f_dst))
check("4.5 整体看 'middle 既协又逆 → 不变（对照）",
      variance_of(f_src, "'middle") == INV)

# ------------------------------------------------------- 5. 生命周期擦除与相等
check("5.1 &'static str <: &'a str", is_subtype(
    ("ref", "'static", ("named", "str", ())), ("ref", "'a", ("named", "str", ()))))
check("5.2 方向反过来不成立", not is_subtype(
    ("ref", "'a", ("named", "str", ())), ("ref", "'static", ("named", "str", ()))))
check("5.3 擦掉生命周期后只剩类型相等（Vec<i32> 与 Vec<u32> 无子类型关系）",
      not is_subtype(("named", "Vec", (("named", "i32", ()),)),
                     ("named", "Vec", (("named", "u32", ()),))))
check("5.4 &'a mut T 在 T 上不变：&'a mut i32 不能当 &'a mut u32",
      not is_subtype(("refmut", "'a", ("named", "i32", ())),
                     ("refmut", "'a", ("named", "u32", ()))))
check("5.5 outlives 偏序：'static ⊇ 'long ⊇ 'middle ⊇ 'short",
      outlives("'static", "'long") and outlives("'long", "'middle")
      and outlives("'middle", "'short") and not outlives("'short", "'long"))

# ------------------------------------------------------------ 6. 高阶生命周期
hrtb = ("forall", "'a", ("fn", (("ref", "'a", ("named", "i32", ())),),
                         ("ref", "'a", ("named", "i32", ()))))
concrete = ("fn", (("ref", "'static", ("named", "i32", ())),),
            ("ref", "'static", ("named", "i32", ())))
check("6.1 for<'a> fn(&'a i32) -> &'a i32 <: fn(&'static i32) -> &'static i32",
      hrtb_subtype(hrtb, concrete))
check("6.2 实例化确实替换了 'a", equal_types(instantiate(hrtb, "'static"), concrete))
hrtb2 = ("forall", "'a", ("forall", "'b",
                          ("fn", (("ref", "'a", ("named", "i32", ())),
                                  ("ref", "'b", ("named", "i32", ()))), UNIT)))
hrtb1 = ("forall", "'c", ("fn", (("ref", "'c", ("named", "i32", ())),
                                 ("ref", "'c", ("named", "i32", ()))), UNIT))
check("6.3 for<'a,'b> fn(&'a,&'b) <: for<'c> fn(&'c,&'c)",
      is_subtype(instantiate(instantiate(hrtb2, "'c"), "'c"), instantiate(hrtb1, "'c")))
check("6.4 方向反过来不成立（具体类型不是 for<'a> 类型的子类型）",
      not hrtb_subtype(concrete, hrtb))

# --------------------------------------------------- 7. PhantomData 九种写法
for kind, (va, vt, _send, _dangling) in PHANTOM_TABLE.items():
    if va != BIV:
        check("7.x %s 在 'a 上是 %s" % (kind, NAME[va]),
              phantom_variance(kind, "'a") == va,
              NAME[phantom_variance(kind, "'a")])
    if vt != BIV:
        check("7.x %s 在 T 上是 %s" % (kind, NAME[vt]),
              phantom_variance(kind, "T") == vt,
              NAME[phantom_variance(kind, "T")])

check("7.10 PhantomData<T> 与 PhantomData<&'a T> 在 T 上同型变",
      phantom_variance("PhantomData<T>", "T")
      == phantom_variance("PhantomData<&'a T>", "T") == COV)
check("7.11 PhantomData<&'a mut T> 让 T 变不变",
      phantom_variance("PhantomData<&'a mut T>", "T") == INV)
check("7.12 fn(T) 与 fn() -> T 型变相反",
      phantom_variance("PhantomData<fn(T)>", "T") == CONTRA
      and phantom_variance("PhantomData<fn() -> T>", "T") == COV)
check("7.13 fn(T) -> T 两边都用 → 不变",
      phantom_variance("PhantomData<fn(T) -> T>", "T") == INV)

# -------------------------------------------- 8. PhantomData 的 Send/Sync 与 drop
check("8.1 PhantomData<T> 的 Send/Sync 继承自 T",
      PHANTOM_TABLE["PhantomData<T>"][2] == "inherited")
check("8.2 PhantomData<*const T> 直接变成 !Send + !Sync",
      PHANTOM_TABLE["PhantomData<*const T>"][2] == "!Send + !Sync")
check("8.3 PhantomData<&'a T> 要 T: Sync 才 Send+Sync",
      PHANTOM_TABLE["PhantomData<&'a T>"][2] == "Send+Sync if T: Sync")
check("8.4 只有 PhantomData<T> 表示「拥有 T」，drop 期不允许悬垂",
      PHANTOM_TABLE["PhantomData<T>"][3] is False)
check("8.5 换成 PhantomData<&'a T> 就允许悬垂（成对对照）",
      PHANTOM_TABLE["PhantomData<&'a T>"][3] is True)
check("8.6 Cell<&'a ()> 让 'a 变成不变",
      PHANTOM_TABLE["PhantomData<Cell<&'a ()>>"][0] == INV)

print("variance: %d assertions passed" % COUNT[0])


if __name__ == "__main__":
    pass
