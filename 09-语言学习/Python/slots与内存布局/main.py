# -*- coding: utf-8 -*-
"""__slots__ 与对象内存布局(datamodel §3.3.2.4)+ dataclass slots=True。

运行: python3 main.py
依据: CPython 参考手册 Data model "Notes on using __slots__"(原文精读)+
      dataclasses 官方文档(见 README 参考资料)。
"""

import sys
import weakref
from dataclasses import dataclass, field, fields

PASS = []

def ok(name):
    PASS.append(name)
    print(f"[PASS] {name}")

def size_of_instance(obj):
    """实例自身的字节数 + 若有 __dict__ 再加 dict 的大小。"""
    total = sys.getsizeof(obj)
    d = getattr(obj, "__dict__", None)
    return total + (sys.getsizeof(d) if d is not None else 0)

def demo_basic_slots():
    """slots 声明 → 无 __dict__;未列出的属性赋值报 AttributeError。"""
    class Plain:
        def __init__(self):
            self.x = 1
            self.y = 2

    class Slotted:
        __slots__ = ("x", "y")

        def __init__(self):
            self.x = 1
            self.y = 2

    p, s = Plain(), Slotted()
    assert not hasattr(s, "__dict__")          # 阻止 __dict__ 自动创建
    assert hasattr(p, "__dict__")
    assert (s.x, s.y) == (1, 2)
    try:
        s.z = 3
        raise AssertionError("未列出的属性应报 AttributeError")
    except AttributeError as e:
        assert "no attribute" in str(e) or "object has no attribute" in str(e)
    # 如需动态属性:把 '__dict__' 加进 slots
    class WithDict:
        __slots__ = ("x", "__dict__")

    w = WithDict()
    w.x = 1
    w.anything = 2                             # __dict__ 存在 → 合法
    assert w.anything == 2
    # 内存:同样两个字段,slots 版更省(布局紧凑 + 无 dict)
    assert size_of_instance(s) < size_of_instance(p), (
        f"slots={size_of_instance(s)} vs plain={size_of_instance(p)}")
    ok(f"基础:无 __dict__、未列出属性报错;实例内存 {size_of_instance(s)}B < 普通 {size_of_instance(p)}B")
    return Plain, Slotted

def demo_memory_at_scale():
    """规模化对比:100 万实例,slots 显著省内存(用浅层估算)。"""
    class Plain:
        def __init__(self):
            self.a = 1
            self.b = 2

    class Slotted:
        __slots__ = ("a", "b")

        def __init__(self):
            self.a = 1
            self.b = 2

    per_plain = size_of_instance(Plain())
    per_slot = size_of_instance(Slotted())
    n = 1_000_000
    print(f"  100 万实例估算:普通 ≈ {per_plain * n / 1e6:.1f} MB,slots ≈ {per_slot * n / 1e6:.1f} MB")
    assert per_slot < per_plain * 0.6           # 至少省 40%
    ok("规模化:每实例开销 slots 明显小于 普通+__dict__")

def demo_inheritance_rules():
    """继承:父类无 slots → 子类实例 __dict__ 始终可用;子类不声明 slots 也重新拿到 __dict__。"""
    class NoSlots:
        pass

    class Child(NoSlots):
        __slots__ = ("a",)                      # 父类无 slots → __dict__ 穿透下来

    c = Child()
    c.a = 1
    assert hasattr(c, "__dict__"), "父类链上无 slots 的类会拖回 __dict__"
    c.dynamic = 2
    assert c.dynamic == 2

    class Slotted:
        __slots__ = ("a",)

    class SubNoSlots(Slotted):
        pass                                    # 子类未声明 slots

    class SubSlots(Slotted):
        __slots__ = ("b",)                       # 只声明新增 slot,不重复父类

    s1 = SubNoSlots()
    assert hasattr(s1, "__dict__"), "子类不声明 slots → 重新获得 __dict__ 和 __weakref__"
    s2 = SubSlots()
    assert not hasattr(s2, "__dict__")
    s2.a, s2.b = 1, 2
    assert (s2.a, s2.b) == (1, 2)
    ok("继承:父类无 slots 则 __dict__ 穿透;子类必须也声明 slots(只列新增)")

def demo_weakref_rule():
    """声明 slots 的类默认不支持弱引用;需显式加 '__weakref__'。"""
    class NoWeak:
        __slots__ = ("x",)

    class HasWeak:
        __slots__ = ("x", "__weakref__")

    n = NoWeak()
    try:
        weakref.ref(n)
        raise AssertionError("未声明 __weakref__ 不应支持弱引用")
    except TypeError as e:
        assert "weak reference" in str(e)
    h = HasWeak()
    h.x = 1
    r = weakref.ref(h)
    assert r() is h
    ok("弱引用:未声明 __weakref__ 报 TypeError;声明后正常")

def demo_descriptor_and_defaults():
    """slots 在类级别以描述符实现 → 类属性默认值会覆盖描述符,slot 失效。"""
    class WithDefault:
        __slots__ = ("x",)

    WithDefault.x = 5                            # 覆盖 slot 描述符(datamodel 明示的坑)
    c = WithDefault()
    assert c.x == 5                              # 读到类属性
    try:
        c.x = 6                                  # 无 __dict__ 可存 → AttributeError
        raise AssertionError("描述符被覆盖后实例赋值应失败")
    except AttributeError:
        pass
    # 对比:默认值应放在 __init__ 里(或 dataclass 处理)
    class Proper:
        __slots__ = ("x",)

        def __init__(self, x=5):
            self.x = x

    assert Proper().x == 5
    ok("类属性默认值:覆盖 slot 描述符导致 slot 失效(赋值报 AttributeError)")

def demo_multi_inheritance_conflict():
    """多继承:只允许一个父类有非空 slot 布局,否则 TypeError。"""
    class A:
        __slots__ = ("a",)

    class B:
        __slots__ = ("b",)

    try:
        class C(A, B):
            __slots__ = ()
        raise AssertionError("两个非空 slot 布局的多继承应报 TypeError")
    except TypeError as e:
        assert "lay-out conflict" in str(e), e
    # 正确姿势:其余基类空 slot 布局
    class Empty:
        __slots__ = ()

    class D(A, Empty):
        __slots__ = ("d",)

    d = D()
    d.a, d.d = 1, 2
    assert (d.a, d.d) == (1, 2)
    # 同名 slot:子类遮蔽基类 slot,基类变量不可访问
    class Shadow(A):
        __slots__ = ("a",)

    sh = Shadow()
    sh.a = 99                                    # 写的是子类 slot
    ok("多继承:非空布局冲突报 TypeError;空布局可共存;同名 slot 遮蔽基类")

def demo_class_assignment():
    """__class__ 赋值只在两个类 slots 相同时可行。"""
    class S1:
        __slots__ = ("x",)

    class S2:
        __slots__ = ("x",)

    class S3:
        __slots__ = ("x", "y")

    a = S1()
    a.__class__ = S2                             # 相同 slots → 允许
    assert type(a) is S2
    try:
        a.__class__ = S3                         # 不同布局 → TypeError
        raise AssertionError("不同 slots 的 __class__ 赋值应失败")
    except TypeError:
        pass
    ok("__class__ 赋值:相同 slots 允许,不同布局报 TypeError")

def demo_iterator_slots():
    """用迭代器赋值 __slots__:每个值都生成描述符,但 __slots__ 本身是空迭代器。"""
    gen = (name for name in ("p", "q"))
    It = type("It", (), {"__slots__": gen})
    it = It()
    it.p, it.q = 1, 2
    assert (it.p, it.q) == (1, 2)
    # datamodel:"the __slots__ attribute will be an empty iterator"——
    # 迭代器被消耗,list() 静默得到空列表
    assert list(It.__slots__) == []
    # dict 也可赋值:键作 slot 名,值作 docstring
    Dict = type("Dict", (), {"__slots__": {"v": "the v slot"}})
    dv = Dict()
    dv.v = 7
    assert dv.v == 7
    ok("迭代器/dict 赋值 __slots__:迭代器版自身变空迭代器;dict 版键即 slot 名")

def demo_variable_length_base():
    """变长内建类型(int/bytes/tuple)派生 + 非空 slots → TypeError。"""
    try:
        type("SubInt", (int,), {"__slots__": ("x",)})
        raise AssertionError("变长内建类型 + 非空 slots 应报 TypeError")
    except TypeError:
        pass
    EmptySub = type("EmptySub", (int,), {"__slots__": ()})   # 空的可以
    assert EmptySub(3) + 1 == 4
    ok("变长内建类型:非空 slots 报 TypeError,空 slots 可继承")

def demo_dataclass_slots():
    """dataclass slots=True:返回【新类】;字段名进 __slots__;frozen 用 object.__setattr__。"""
    @dataclass
    class Normal:
        a: int
        b: list = field(default_factory=list)

    @dataclass(slots=True)
    class Slotty:
        a: int
        b: list = field(default_factory=list)

    n1, n2 = Normal(1), Normal(1)
    s1, s2 = Slotty(1), Slotty(1)
    assert n1 == n2 and s1 == s2                 # eq 逐字段比较
    assert hasattr(n1, "__dict__") and not hasattr(s1, "__dict__")
    assert set(Slotty.__slots__) == {"a", "b"}
    assert [f.name for f in fields(Slotty)] == ["a", "b"]   # 查字段用 fields(),不用 __slots__
    assert size_of_instance(s1) <= size_of_instance(n1)
    s1.b.append(9)
    assert s1.b == [9] and s2.b == []            # default_factory 每实例独立
    # frozen:赋值抛 FrozenInstanceError,__init__ 内部经 object.__setattr__ 绕过
    @dataclass(frozen=True, slots=True)
    class Frozen:
        x: int

    f = Frozen(3)
    assert f.x == 3
    try:
        f.x = 4
        raise AssertionError("frozen 实例赋值应抛 FrozenInstanceError")
    except Exception as e:
        assert type(e).__name__ == "FrozenInstanceError"
    ok("dataclass(slots=True):返回新类、字段入 slots、frozen 抛 FrozenInstanceError")

def main():
    print(f"Python {sys.version.split()[0]}\n")
    demo_basic_slots()
    demo_memory_at_scale()
    demo_inheritance_rules()
    demo_weakref_rule()
    demo_descriptor_and_defaults()
    demo_multi_inheritance_conflict()
    demo_class_assignment()
    demo_iterator_slots()
    demo_variable_length_base()
    demo_dataclass_slots()
    print(f"\n共 {len(PASS)} 项断言全部通过")

if __name__ == "__main__":
    main()
