"""演示:捕获集、同形状推断、变长类型实参与包遍历。

运行: python main.py
"""

from packs_model import (Each, Repeat, Node, captures, ShapeSolver,
                         infer_from_pack_expansion, check_pack_expansion, PackError,
                         bind_generic_args, infer_requirements, check_stored_property,
                         iterate_over_pack, expand_all, abstract_tuple_example)


def main():
    print("== 捕获集 ==")
    pat = Repeat(Node("foo", Each("x"), Node("self", Each("T")), Repeat(Each("y"))))
    print("  repeat foo(each x, (each T).self, (repeat each y))")
    print("    外层捕获:", sorted(captures(pat)))
    print("    内层捕获:", sorted(captures(pat.parts[0].parts[2])))
    tp = Node("Foo", Each("U"), Repeat(Each("V")))
    print("  值包类型 Foo<U, (repeat each V)> 顺带捕获:", sorted(captures(tp)))

    print("\n== 同形状推断 ==")
    s = ShapeSolver(["T", "U", "V"])
    print("  推断前 T 与 U 同形状?", s.same_shape("T", "U"))
    infer_from_pack_expansion(s, {"T", "U"})
    print("  在函数返回类型处见过 (repeat (each T, each U)) 之后:",
          s.same_shape("T", "U"), "等价类 =", s.class_of("T"))
    try:
        check_pack_expansion(s, {"T", "V"}, "local var")
    except PackError as e:
        print("  局部变量位置写 (repeat (each T, each V)) ->", e)

    print("\n== 变长泛型类型的实参绑定 ==")
    spec = [("scalar", "T"), ("pack", "U"), ("scalar", "V")]
    for args in (["Int", "Float"], ["Int", "Bool", "Float"], ["Int", "Bool", "String", "Float"]):
        print("  S<%s> -> %s" % (", ".join(args), bind_generic_args(spec, args)))
    try:
        bind_generic_args(spec, ["Int"])
    except PackError as e:
        print("  S<Int> ->", e)

    print("\n== 要求推断(SE-0398) ==")
    print("  repeat ImposeRequirement<each U> ->",
          infer_requirements("scalar", [("pack", "U")]))
    print("  ImposeRepeatedRequirement<Int, V, repeat each U> ->",
          infer_requirements("expansion", [("concrete", "Int"), ("concrete", "V"),
                                           ("pack", "U")]))
    try:
        infer_requirements("expansion", [("pack", "U"), ("pack", "V")],
                           pack_depths={"U": 1, "V": 2})
    except PackError as e:
        print("  repeat ImposeRepeatedSameType<each U, repeat each V> ->", e)

    print("\n== 实存属性 ==")
    for label, node in (("var a: repeat each T", Repeat(Each("T"))),
                        ("var b: (repeat each T)", Node("tuple", Repeat(Each("T"))))):
        try:
            check_stored_property(node)
            print("  %-24s -> 合法" % label)
        except PackError as e:
            print("  %-24s -> %s" % (label, e))

    print("\n== 包遍历 vs 包扩展(惰性 vs 全量) ==")
    values = [1, "hello", True]
    ev = iterate_over_pack(values, lambda i: "evaluated %r" % values[i], break_at=1)
    print("  for-in(break at 1):", [e for k, e in ev if k == "eval"])
    print("  repeat 全量展开    :", [e for k, e in expand_all(values, lambda i: "evaluated %r" % values[i])])

    print("\n== 抽象元组的区别(SE-0399) ==")
    for k, v in abstract_tuple_example([1, 2, 3], (4, 5, 6)).items():
        print("  %-20s -> %s" % (k, v))


if __name__ == "__main__":
    main()
