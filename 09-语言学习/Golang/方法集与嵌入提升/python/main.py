"""Go 方法集与选择器演示：规范 §Selectors 官方示例 + 提升/遮蔽/歧义三组对照。"""

from selectormodel import (
    Universe, GoType, Ambiguous, NotFound, method_set, lookup, selector,
)


def official():
    u = Universe()
    u.add(GoType("T0", fields={"x": "int"}, methods={"M0": True}))
    u.add(GoType("T1", fields={"y": "int"}, methods={"M1": False}))
    u.add(GoType("T2", fields={"z": "int"},
                 embedded=[("T1", "T1", False), ("T0", "T0", True)],
                 methods={"M2": True}))
    u.add(GoType("Q", is_defined_pointer_of="T2"))
    return u


def describe(res):
    depth, path, kind, decl = res
    if not path:
        return "深度 %d，由 %s 自己声明（%s）" % (depth, decl, kind)
    return "深度 %d，经 %s 提升（%s，声明于 %s）" % (depth, " → ".join(path), kind, decl)


def main():
    u = official()

    print("== 1. 官方示例的方法集 ==")
    for name, ptr in (("T0", False), ("T0", True), ("T1", False),
                      ("T2", False), ("T2", True)):
        ms = method_set(u, name, ptr)
        print("  %-4s 的方法集 = %s" % (("*" + name) if ptr else name,
                                    sorted(ms) or "{}"))

    print("\n== 2. 官方示例的选择器 ==")
    for expr in ("z", "y", "x", "M0", "M1", "M2"):
        try:
            print("  t.%-3s : %s" % (expr, describe(lookup(u, "T2", expr))))
        except (NotFound, Ambiguous) as e:
            print("  t.%-3s : 非法（%s）" % (expr, e))

    print("\n== 3. 定义型指针 Q = *T2 的例外：只放行字段 ==")
    for expr in ("x", "z", "M0", "M1"):
        try:
            print("  q.%-3s : 合法（%s）" % (expr, describe(selector(u, "Q", expr))))
        except (NotFound, Ambiguous) as e:
            print("  q.%-3s : 非法 —— %s" % (expr, e))

    print("\n== 4. 嵌入值 vs 嵌入指针 ==")
    u2 = Universe()
    u2.add(GoType("A", methods={"Val": False, "Ptr": True}))
    u2.add(GoType("S1", embedded=[("A", "A", False)]))
    u2.add(GoType("S2", embedded=[("A", "A", True)]))
    for name in ("S1", "S2"):
        print("  %s(嵌入 %s): S = %s ; *S = %s"
              % (name, "A" if name == "S1" else "*A",
                 sorted(method_set(u2, name, False)),
                 sorted(method_set(u2, name, True))))

    print("\n== 5. 遮蔽与歧义 ==")
    u3 = Universe()
    u3.add(GoType("A", fields={"F": "int"}, methods={"M": False}))
    u3.add(GoType("B", fields={"F": "int"}, methods={"M": False}))
    u3.add(GoType("C", embedded=[("A", "A", False), ("B", "B", False)]))
    u3.add(GoType("D", fields={"F": "string"},
                  embedded=[("A", "A", False), ("B", "B", False)]))
    try:
        lookup(u3, "C", "F")
        print("  C.F : 竟然合法？")
    except Ambiguous as e:
        print("  C.F : 非法 —— %s" % e)
    print("  D.F : %s（自身声明遮蔽了两处提升）" % describe(lookup(u3, "D", "F")))


if __name__ == "__main__":
    main()
