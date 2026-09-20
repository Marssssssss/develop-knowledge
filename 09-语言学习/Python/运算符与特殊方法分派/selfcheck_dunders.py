# -*- coding: utf-8 -*-
"""
运算符与特殊方法分派 —— 自检。

把数据模型里"运算符 → 特殊方法"的分派规则做成可执行的调用日志:
  NotImplemented 与回退、反射方法、子类优先、in-place 回退、
  比较运算符的反射与 is 兜底、__hash__ 契约、__bool__ 回退 __len__。

    python selfcheck_dunders.py
"""
import warnings

from main import (LOG, Lhs, Rhs, RhsSub, Same, Blocked, Provider,
                  WithIAdd, IAddNotImplemented, OnlyAdd,
                  AlwaysEqual, SubEq, OnlyLt, Nothing,
                  NoHash, KeepHash, ByLen, BadBool)

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


# ------------------------------------------------- 1. NotImplemented 是什么
def demo_notimplemented():
    assert type(NotImplemented).__name__ == "NotImplementedType"
    assert repr(NotImplemented) == "NotImplemented"
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            val = bool(NotImplemented)
            raised = None
        except TypeError as e:          # 3.14 起
            val, raised = None, e
        if raised is None:
            assert val is True and any(issubclass(x.category, DeprecationWarning)
                                       for x in w), "3.9~3.13 应给 DeprecationWarning"
            note = "3.13: 布尔上下文是 True 但发 DeprecationWarning"
        else:
            note = "3.14+: 布尔上下文直接 TypeError"
    ok(f"NotImplemented 是单例;{note}(永远不要把它当成返回值给调用方)")


# ------------------------------------------------- 2. 算术:反射与子类优先
def demo_reflected():
    LOG.clear()
    assert Lhs() + Rhs() == "rhs"
    assert LOG == ["Lhs.__add__", "Rhs.__radd__"], LOG
    ok("不同类型:左操作数返回 NotImplemented → 调用右操作数的 __radd__")

    LOG.clear()
    assert Lhs() + RhsSub() == "sub"
    assert LOG == ["RhsSub.__radd__"], f"子类优先时反射方法应先于左操作数方法: {LOG}"
    ok("子类优先:右操作数是左操作数的子类且改写了反射方法 → 反射方法**先**被调用")


def demo_same_type_no_reflection():
    LOG.clear()
    try:
        Same() + Same()
        raise AssertionError("同类型时不应调用反射方法")
    except TypeError as e:
        assert "unsupported operand" in str(e), f"实为 {e}"
    assert LOG == ["Same.__add__"], LOG
    ok("同类型:脚注[4]假定非反射方法失败即不支持 → 反射方法根本不会被调用")


def demo_none_blocks_fallback():
    try:
        Blocked() + Provider()
        raise AssertionError("__add__ = None 应阻断回退")
    except TypeError as e:
        assert "unsupported operand" in str(e) or "NoneType" in str(e), f"实为 {e}"
    ok("坑:把特殊方法设成 None 是**阻断**回退,不是促成回退(脚注[3])")


# ------------------------------------------------- 3. in-place 的回退链
def demo_inplace():
    LOG.clear()
    x = WithIAdd(1)
    x += 5
    assert x.v == 6 and LOG == ["__iadd__"]

    LOG.clear()
    y = IAddNotImplemented()
    z = y
    z += 1
    assert z == "via __add__", "__iadd__ 返回 NotImplemented 后回退到 __add__"
    assert LOG == ["__iadd__->NotImplemented", "__add__"], LOG
    assert isinstance(y, IAddNotImplemented), "y 本身仍是原对象,只有 z 被重新绑定"

    LOG.clear()
    o = OnlyAdd()
    o += 1
    assert o == "only __add__" and LOG == ["OnlyAdd.__add__"]
    ok("in-place:__iadd__ 缺失或返回 NotImplemented → 回退 __add__ / __radd__(结果会重新绑定)")


# ------------------------------------------------- 4. 比较:反射与 is 兜底
def demo_comparison():
    LOG.clear()
    a = AlwaysEqual()
    assert (a == 5) is True
    assert (5 == a) is True, "__eq__ 是自己的反射,左右互换都成立"
    assert (a != 5) is False, "object.__ne__ 委托给 __eq__ 并取反"

    x, y = OnlyLt(1), OnlyLt(2)
    LOG.clear()
    assert (x > y) is False
    assert LOG == ["__lt__"], f"x > y 应反射成 y.__lt__(x): {LOG}"
    assert (x.__lt__(y)) is True

    p, q = Nothing(), Nothing()
    assert (p == q) is False, "没有方法返回非 NotImplemented → == 退化为 is"
    assert (p != q) is True, "!= 退化为 is not"
    assert (p == p) is True
    ok("比较:__eq__/__ne__ 互为自身反射,__lt__/__gt__ 互为反射;全都失败时 == 退化为 is")


def demo_comparison_subclass_priority():
    LOG.clear()
    assert (AlwaysEqual() == SubEq()) == "sub"
    assert LOG == ["SubEq.__eq__"], f"右侧子类优先: {LOG}"
    ok("比较同样遵循子类优先(虚拟基类不算:规范明确 Virtual subclassing is not considered)")


# ------------------------------------------------- 5. __hash__ 契约
def demo_hash():
    assert NoHash.__hash__ is None, "重写 __eq__ 而不写 __hash__ → __hash__ 被置 None"
    try:
        hash(NoHash())
        raise AssertionError("应不可哈希")
    except TypeError as e:
        assert "unhashable" in str(e), f"实为 {e}"
    assert isinstance(hash(KeepHash()), int), "显式赋值后恢复哈希"

    class Point:
        def __init__(self, x):
            self.x = x

        def __eq__(self, o):
            return isinstance(o, Point) and o.x == self.x

        def __hash__(self):
            return hash(self.x)

    assert hash(Point(1)) == hash(1)
    assert len({Point(1), Point(1)}) == 1, "相等的对象必须哈希相等"
    ok("__hash__:重写 __eq__ 会丢哈希(置 None);相等对象必须同哈希值才能进 set")


# ------------------------------------------------- 6. 真值测试
def demo_truth():
    assert not ByLen(0) and ByLen(3)
    assert bool(ByLen(1)) is True
    try:
        bool(BadBool())
        raise AssertionError("__bool__ 必须返回 bool")
    except TypeError as e:
        assert "bool" in str(e), f"实为 {e}"
    try:
        bool(ByLen(-1))
        raise AssertionError("__len__ 不能返回负数")
    except ValueError as e:
        assert "negative" in str(e) or "__len__" in str(e), f"实为 {e}"
    ok("真值:无 __bool__ 时回退 __len__;__bool__ 必须返回 bool,__len__ 不能为负")


def main():
    print("1. NotImplemented")
    demo_notimplemented()
    print("2. 算术反射")
    demo_reflected()
    demo_same_type_no_reflection()
    demo_none_blocks_fallback()
    print("3. in-place")
    demo_inplace()
    print("4. 比较")
    demo_comparison()
    demo_comparison_subclass_priority()
    print("5. 哈希契约")
    demo_hash()
    print("6. 真值测试")
    demo_truth()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
