"""型变与 PhantomData —— 演示入口。

与 `selfcheck_variance.py` 共用 `variance` 模型；打印三件事：
官方 `Variance` 结构体各参数的型变（含成对对照）、「struct 里冲突即不变 vs
裸位置逐位置各算」、以及用型变代数**算**出来的 PhantomData 九种写法与 Nomicon 表的比对。
"""

from variance import (
    COV,
    NAME,
    PHANTOM_TABLE,
    Struct,
    declare,
    is_subtype,
    phantom_variance,
    tuple_variance,
    variance_of,
)

T = ("param", "T")
U = ("param", "U")
UNIT = ("unit",)
VARIANCE = ("named", "Variance", ())


def make_variance(with_mut_u=True, fn_form="both"):
    """Reference 的官方例子：struct Variance<'a, 'b, 'c, T, U: 'a>。"""
    fields = [("ref", "'a", U), ("ptr_const", T),
              ("unsafe_cell", ("ref", "'b", ("named", "f64", ())))]
    if with_mut_u:
        fields.append(("ptr_mut", U))
    if fn_form == "both":
        fields.append(("fn", (("ref", "'c", UNIT),), ("ref", "'c", UNIT)))
    elif fn_form == "cov":
        fields.append(("ref", "'c", UNIT))
    return Struct("Variance", ["'a", "'b", "'c", "T", "U"], fields)


def main() -> None:
    print("[1] 官方 Variance 结构体：每个参数的型变由字段合并而来")
    declare(make_variance())
    for p in ["'a", "'b", "'c", "T", "U"]:
        print("    %-3s -> %s" % (p, NAME[variance_of(VARIANCE, p)]))

    print("\n[2] 成对对照：只去掉 w: *mut U，U 就变回协变")
    declare(make_variance(with_mut_u=False))
    print("    U  -> %s" % NAME[variance_of(VARIANCE, "U")])
    declare(make_variance(fn_form="cov"))
    print("    把 f 换成纯协变用法，'c -> %s" % NAME[variance_of(VARIANCE, "'c")])

    print("\n[3] 不在 struct 里时，每个位置各算各的")
    src = ("tuple", (("ref", "'long", ("named", "u32", ())),
                     ("unsafe_cell", ("ref", "'long", ("named", "u32", ())))))
    dst = ("tuple", (("ref", "'short", ("named", "u32", ())),
                     ("unsafe_cell", ("ref", "'long", ("named", "u32", ())))))
    print("    元组整体在 'long 上的型变：%s" % NAME[tuple_variance(src, "'long")])
    print("    但逐位置收缩仍成立：%s" % is_subtype(src, dst))
    declare(Struct("Wrap", ["'long"], list(src[1])))
    print("    同样的字段放进 struct：'long 真的变成 %s"
          % NAME[variance_of(("named", "Wrap", ()), "'long")])

    print("\n[4] PhantomData：算出来的型变 vs Nomicon 表格")
    print("    %-26s %-14s %-14s %-14s" % ("写法", "'a（算/表）", "T（算/表）", "Send+Sync"))
    for kind, (va, vt, ss, _dangling) in PHANTOM_TABLE.items():
        ca, ct = phantom_variance(kind, "'a"), phantom_variance(kind, "T")
        flag = "" if (ca == va and ct == vt) else "  <== 不一致"
        print("    %-26s %-14s %-14s %-14s%s"
              % (kind, NAME[ca], NAME[ct], ss, flag))

    print("\n[5] 逆变只出现在函数参数位：fn(T) -> () 在 T 上是 %s"
          % NAME[variance_of(("fn", (T,), UNIT), "T")])
    print("    而 fn() -> T 是 %s，两者合起来 fn(T) -> T 是 %s"
          % (NAME[variance_of(("fn", (), T), "T")],
             NAME[variance_of(("fn", (T,), T), "T")]))


if __name__ == "__main__":
    main()
