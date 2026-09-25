# -*- coding: utf-8 -*-
"""访问者、双分派与表达式问题模型。

口径(实读源):
  refactoring.guru Visitor 页 —— 重载无法按动态类型选方法(会落到基类重载);
  accept 把选择权交还元素(双分派);加类要改所有访问者(官方 Cons)。
  表达式问题两个轴的对照为本 demo 的分析(标注),佐证 Rust Option 文档的
  『无 null 引用,取值先模式匹配』的封闭枚举语义。
"""


class Node:
    def accept(self, visitor):
        raise NotImplementedError


class Num(Node):
    def __init__(self, value):
        self.value = value

    def accept(self, visitor):
        return visitor.visit_num(self)       # 元素知道自己是谁,替访问者选方法


class Add(Node):
    def __init__(self, left, right):
        self.left, self.right = left, right

    def accept(self, visitor):
        return visitor.visit_add(self)


class BaseOverloadVisitor:
    """反例:只有按**静态类型** Node 的重载 → 动态类型丢失。"""

    def visit(self, node):
        return "visit(Node)"                 # Python 无重载,这里模拟『落到基类重载』


class EvalVisitor:
    def visit_num(self, n):
        return n.value

    def visit_add(self, a):
        return a.left.accept(self) + a.right.accept(self)


class PrintVisitor:
    def visit_num(self, n):
        return str(n.value)

    def visit_add(self, a):
        return f"({a.left.accept(self)} + {a.right.accept(self)})"


def change_impact(axis):
    """表达式问题两轴的改动面(访问者刻度):
    加操作=加一个访问者类(既有类零改动);加节点=改每一个访问者。"""
    return {
        "add_operation": ("1 个新访问者", 0),
        "add_node": ("1 个新节点子类 + accept", None),   # None = 视访问者数而定
    }[axis]


VISITOR_CONS = [
    "元素层级增删时,所有访问者都要更新",
    "访问者可能拿不到元素的私有字段与方法",
]
