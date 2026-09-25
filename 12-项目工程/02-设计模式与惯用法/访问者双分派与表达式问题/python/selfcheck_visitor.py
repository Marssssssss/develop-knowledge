# -*- coding: utf-8 -*-
"""双分派与表达式问题断言。"""

from visitor import (
    VISITOR_CONS, Add, BaseOverloadVisitor, EvalVisitor, Num, PrintVisitor,
    change_impact,
)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def main():
    print("1. 重载为什么救不了")
    expr = Add(Num(1), Num(2))
    wrong = BaseOverloadVisitor().visit(expr)
    assert wrong == "visit(Node)"
    ok("节点动态类型未知时,重载按**静态类型**解析,一律落到基类重载——"
       "页面原文的『无法确定该执行哪个方法』")

    print("2. 双分派")
    assert expr.accept(EvalVisitor()) == 3
    assert expr.accept(PrintVisitor()) == "(1 + 2)"
    ok("元素 accept(visitor) → 元素回调 visit_具体(自己):选择权交还给元素,"
       "动态类型在元素自己手里——不需要条件判断")

    print("3. 加一个操作(访问者的高光轴)")
    class CodeGenVisitor:
        def visit_num(self, n):
            return f"PUSH {n.value}"

        def visit_add(self, a):
            return f"{a.left.accept(self)}; {a.right.accept(self)}; ADD"

    assert expr.accept(CodeGenVisitor()) == "PUSH 1; PUSH 2; ADD"
    tag, touched = change_impact("add_operation")
    assert tag == "1 个新访问者" and touched == 0
    ok("新行为=新访问者类:元素层级与既有访问者零改动(官方 Pros:OCP)")

    print("4. 加一个节点类型(访问者的代价轴)")
    class Mul(Num):                                    # 新节点(简化:复用 value)
        def accept(self, visitor):
            return visitor.visit_mul(self) if hasattr(visitor, "visit_mul") \
                else (_ for _ in ()).throw(TypeError("visitor lacks visit_mul"))

    class MulEval(EvalVisitor):
        def visit_mul(self, m):
            return m.value

    assert Mul(7).accept(MulEval()) == 7
    try:
        Mul(7).accept(EvalVisitor())                   # 旧访问者没跟上
        raise AssertionError("unreachable")
    except TypeError as e:
        assert "visit_mul" in str(e)
    ok("元素层级一变,**每个**访问者都得改(官方 Cons 第一条);"
       f"另一条代价:{VISITOR_CONS[1]}")

    print("5. 表达式问题的两个轴")
    ok("『加操作易』与『加类型易』不可兼得是表达式问题:访问者押操作轴,"
       "封闭枚举+模式匹配(Rust Option 那类)押类型轴——先问哪种变化更频繁再选")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
