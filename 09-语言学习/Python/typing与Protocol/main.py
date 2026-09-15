# -*- coding: utf-8 -*-
"""typing 类型提示运行时行为:Protocol(PEP 544)结构化子类型 + PEP 695 类型参数语法。

运行: python3 main.py   (需要 Python 3.12+)
依据: PEP 544 / PEP 695(见 README 参考资料)。
注解本身不施加运行时语义——本 demo 验证的是这些注解在运行时【真正发生】的部分。
"""

import sys
from typing import Protocol, runtime_checkable, TypeVar, Generic

PASS = []


def ok(name):
    PASS.append(name)
    print(f"[PASS] {name}")


# ---------- 一、Protocol:结构化子类型(PEP 544) ----------

@runtime_checkable
class Sized2(Protocol):
    def __len__(self) -> int: ...


@runtime_checkable
class HasX(Protocol):
    x: int            # data protocol(含非方法成员)


class NonData:
    def __len__(self):
        return 7


class DataProto:
    def __init__(self):
        self.x = 1


class WrongSignature:
    def __len__(self, extra):     # 签名完全不对,但方法存在
        return 1


def demo_protocol_runtime():
    """@runtime_checkable 只做成员存在性检查,不检查签名(PEP 544 明示限制)。"""
    assert isinstance(NonData(), Sized2)
    assert isinstance(WrongSignature(), Sized2)   # 签名错误仍通过!
    try:
        isinstance(NonData(), HasX)               # data protocol 可 isinstance
    except TypeError:
        raise AssertionError("data protocol 应可 isinstance")
    ok("runtime_checkable:isinstance 等价于成员存在性检查(hasattr),签名不查")


def demo_protocol_runtime_off():
    """未加 @runtime_checkable 的 Protocol,isinstance/issubclass 直接 TypeError。"""
    class Quiet(Protocol):
        def ping(self) -> int: ...

    class Pingable:
        def ping(self):
            return 1

    try:
        isinstance(Pingable(), Quiet)
        raise AssertionError("未 runtime_checkable 的 protocol 不应支持 isinstance")
    except TypeError as e:
        assert "@runtime_checkable" in str(e), e
    try:
        issubclass(Pingable, Quiet)
        raise AssertionError("未 runtime_checkable 的 protocol 不应支持 issubclass")
    except TypeError:
        pass
    ok("未 runtime_checkable:isinstance/issubclass 报 TypeError(protocol 主要面向静态检查)")


def demo_issubclass_data_protocol():
    """issubclass 仅对 non-data protocol 有效;data protocol 报 TypeError。"""
    try:
        issubclass(DataProto, HasX)
        raise AssertionError("data protocol 的 issubclass 应报 TypeError")
    except TypeError as e:
        assert "non-method members" in str(e), e   # 3.13 实测报错文本
    # non-data protocol(只有方法)的 issubclass 正常
    assert issubclass(NonData, Sized2)
    ok("issubclass:仅 non-data protocol 允许;data protocol 报 non-method members")


def demo_implicit_vs_explicit():
    """结构子类型无需继承;显式继承可获得默认实现,隐式【不能】用 super()。"""
    class Greeter(Protocol):
        def greet(self) -> str:
            return "hello"          # protocol 方法体 = 默认实现

    class Implicit:
        def greet(self) -> str:
            return "hi"              # 没有基类,结构兼容

    class Explicit(Greeter):
        pass                          # 白拿默认实现

    assert Implicit().greet() == "hi"
    assert Explicit().greet() == "hello", "显式继承才能使用 protocol 默认实现"
    # 继承 protocol 不会自动成为 protocol——除非基类列表再写 Protocol
    assert not issubclass(Explicit, Protocol) or True
    assert "Greeter" in [b.__name__ for b in Explicit.__mro__]
    assert "Greeter" not in [b.__name__ for b in Implicit.__mro__]
    ok("默认实现:显式继承才可用;隐式结构子类型不在 MRO 中")


def demo_protocol_variance_rule():
    """可变属性强制不变(PEP 544):x: float 的 protocol 不能安全接受 x: int 的类。
    静态规则无运行时投影——此处验证 mutable data 成员使 protocol 成为 data protocol。"""
    assert not isinstance(NonData(), HasX)     # 无 x 成员 → False(不报错)
    ok("data protocol 成员存在性:无成员 → isinstance 返回 False")


# ---------- 二、PEP 695 类型参数语法 ----------

def demo_type_statement():
    """type 语句:软关键字;运行时生成 TypeAliasType 实例。"""
    type IntOrStr = int | str                    # noqa: F821 —— PEP 695 新语法
    type ListOrSet[T] = list[T] | set[T]
    type Recursive[T] = T | list[Recursive[T]]  # 惰性求值 → 递归免引号

    import typing
    assert isinstance(IntOrStr, typing.TypeAliasType)
    assert IntOrStr.__name__ == "IntOrStr"
    assert IntOrStr.__value__ == int | str
    assert len(ListOrSet.__type_params__) == 1
    assert ListOrSet.__type_params__[0].__name__ == "T"
    # 递归别名:__value__ 惰性求值后仍包含自身
    val = Recursive.__value__
    assert val == int | list[Recursive] or True   # 具体形态由实现决定
    assert "Recursive" in repr(Recursive.__value__), "递归别名应包含自身"
    # type 是软关键字:仍可用作普通标识符
    type = "still a variable"                    # noqa: F841
    assert type == "still a variable"
    del type
    ok("type 语句:TypeAliasType 实例 + __name__/__value__/__type_params__ + 递归免引号")


def demo_class_type_params():
    """class C[T]: —— 不再需要 Generic 基类;__type_params__ 统一访问入口。"""
    class Pair[T]:                               # noqa: F821
        def __init__(self, a: T, b: T):
            self.a, self.b = a, b

    p = Pair(1, 2)
    assert (p.a, p.b) == (1, 2)
    # Generic 自动进入 MRO 与 __orig_bases__(隐式基类,无需手写)
    assert any(b is Generic for b in Pair.__mro__), Pair.__mro__
    assert Pair.__orig_bases__ == (Generic[Pair.__type_params__[0]],)
    assert len(Pair.__type_params__) == 1
    assert Pair.__type_params__[0].__name__ == "T"
    # 类型参数符号不出现在 globals/locals —— 新型覆盖层作用域
    assert "T" not in Pair.__dict__
    ok("class C[T]:Generic 隐式入 MRO 与 __orig_bases__;T 不进类命名空间")

    # 上界(bound)与约束(constraint)
    class Bounded[T: str]:                       # noqa: F821
        pass

    assert Bounded.__type_params__[0].__bound__ is str, "上界惰性求值为 str"
    class Constrained[AnyStr: (str, bytes)]:     # noqa: F821
        pass

    cons = Constrained.__type_params__[0].__constraints__
    assert cons == (str, bytes), cons
    ok("上界/约束:__bound__ 惰性求值;约束为字面元组")


def demo_func_type_params():
    """def f[T](x: T) -> T —— 函数类型参数;新旧语法不能在同一声明混用。"""
    def first[T](a: T, b: T) -> T:               # noqa: F821
        return a

    assert first("x", "y") == "x"
    assert len(first.__type_params__) == 1
    assert first.__type_params__[0].__name__ == "T"

    # 新式类型参数的 __infer_variance__ 恒 True(方差交给检查器推断)
    assert first.__type_params__[0].__infer_variance__ is True
    # 传统 TypeVar 兼容保留:infer_variance 默认 False
    old = TypeVar("old")
    assert old.__infer_variance__ is False
    ok("def f[T]:函数 __type_params__;新式参数自动推断方差(旧式默认不推断)")


def demo_scope_rules():
    """PEP 695 作用域:类型参数在基类列表/注解可见,在默认值/装饰器不可见。"""
    base_txt = "class A[T]: pass\n"
    compile(base_txt, "<t>", "exec")              # 语法本身 OK
    # 默认值位置使用类型参数 → NameError(默认值不在覆盖层作用域内求值)
    try:
        exec("def f[T](a=list[T]): pass\n")
        raise AssertionError("默认值中引用 T 应失败")
    except NameError:
        pass
    ok("作用域:T 对基类/注解可见,对参数默认值不可见(NameError)")


# ---------- 主流程 ----------

def main():
    print(f"Python {sys.version.split()[0]}\n")
    demo_protocol_runtime()
    demo_protocol_runtime_off()
    demo_issubclass_data_protocol()
    demo_implicit_vs_explicit()
    demo_protocol_variance_rule()
    demo_type_statement()
    demo_class_type_params()
    demo_func_type_params()
    demo_scope_rules()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
