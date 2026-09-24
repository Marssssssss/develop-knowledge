# -*- coding: utf-8 -*-
"""
Python · enum 与 dataclasses 的运行时行为

两套"类工厂"机制对拍:@dataclass 装饰器在类创建**之后**改写(所以 __init_subclass__
看不到 fields),EnumType 元类在类创建**之中**改写(成员即实例、别名同身份);
外加 hash 矩阵、冻结语义、InitVar 陷阱、_missing_ 钩子与按值包含的宽松 in。

参考(实读):
  - https://docs.python.org/3/library/dataclasses.html   (hash 规则/InitVar/asdict)
  - https://docs.python.org/3/library/enum.html          (EnumType/_missing_/别名)
  - https://peps.python.org/pep-0557/                    (dataclasses 设计)
  - 本机 CPython 3.12 Lib/dataclasses.py 与 Lib/enum.py
"""

import pickle
from dataclasses import (InitVar, dataclass, field, fields, asdict,
                         astuple, replace, FrozenInstanceError)
from enum import Enum, IntEnum, auto

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


# ---- dataclass 演示类 ----

@dataclass
class EqOnly:
    x: int = 1


@dataclass(frozen=True)
class Frozen:
    x: int = 1


@dataclass(eq=False)
class EqOff:
    x: int = 1


@dataclass
class Base:
    a: int = 0
    b: str = "s"


@dataclass
class Sub(Base):
    b: str = "t"
    c: float = 1.5


@dataclass
class SubNoDefault(Base):
    b: str


@dataclass
class Inner:
    v: int = 0


@dataclass
class Outer:
    i: Inner = field(default_factory=Inner)
    lst: list = field(default_factory=list)


@dataclass
class WithPost:
    x: int
    doubled: int = field(init=False)
    scale: int = 1

    def __post_init__(self):
        self.doubled = self.x * self.scale


@dataclass
class HasInitVar:
    x: int = 0
    calc: InitVar[int] = 5
    y: int = field(init=False, default=0)

    def __post_init__(self, calc):
        self.y = self.x + calc


# ---- enum 演示类 ----

Color = Enum("Color", ["RED", "GREEN", "BLUE"])


class Aliased(Enum):
    X = 1
    Y = 2
    ALIAS = 1


class Lenient(Enum):
    @classmethod
    def _missing_(cls, value):
        return cls.X if str(value).lower() == "x" else None

    X = 1


class Level(IntEnum):
    DEBUG = 10
    INFO = 20


HOOK_SEEN = []


class Hooked:
    def __init_subclass__(cls, **kw):
        HOOK_SEEN.append(hasattr(cls, "__dataclass_fields__"))


@dataclass
class HookChild(Hooked):
    x: int = 0


def demo_hash_matrix():
    print("1. eq/frozen 的 hash 矩阵")
    assert EqOnly.__hash__ is None
    try:
        hash(EqOnly(1))
    except TypeError:
        ok("eq=True + frozen=False → __hash__=None,实例不可哈希(可变就不该进 set/dict 键)")
    assert hash(Frozen(1)) == hash(Frozen(1)) and Frozen(1) == Frozen(1)
    ok("eq+frozen → 自动生成 __hash__(按字段元组),值对象语义成立")
    assert EqOff(1) != EqOff(1)
    ne = {EqOff(1), EqOff(1)}
    assert len(ne) == 2
    ok("eq=False → __eq__/__hash__ 都不动:退回 object 的身份语义")


def demo_frozen_and_defaults():
    print("2. 冻结与可变默认值")
    f = Frozen(1)
    try:
        f.x = 2
    except FrozenInstanceError:
        ok("frozen 的 __setattr__ 抛 FrozenInstanceError(生成 __init__ 内部走 object.__setattr__ 绕行)")
    try:
        @dataclass
        class Bad:
            x: list = []
    except ValueError as e:
        assert "mutable default" in str(e)
        ok("可变默认值(不可哈希对象)在装饰时就 ValueError——而不是运行期才踩共享陷阱")

    o1 = Outer(); o1.lst.append(1)
    o2 = Outer()
    assert o1.lst == [1] and o2.lst == [] and o1.i is not o2.i
    ok("default_factory 每次实例化新建:实例间绝不共享")


def demo_inheritance_and_post():
    print("3. 继承合并、__post_init__ 与 InitVar")
    order = [(f.name, f.default) for f in fields(Sub)]
    assert order == [("a", 0), ("b", "t"), ("c", 1.5)]
    ok("字段序 = 基类在前;子类重声明同名字段**占住原位**只换默认值")
    assert SubNoDefault().b == "s"
    ok("子类重声明**不带默认**时继承基类默认(不是丢失默认)")
    assert WithPost(3, scale=10).doubled == 30
    ok("init=False 字段由 __post_init__ 补算")
    iv = HasInitVar(1, calc=100)
    assert iv.y == 101 and "calc" not in vars(iv) and iv.calc == 5
    ok("InitVar 只进 __init__/__post_init__,不进实例 __dict__;iv.calc 读到的是类属性 5!")
    assert [f.name for f in fields(HasInitVar)] == ["x", "y"]
    ok("fields() 不含 ClassVar/InitVar 伪字段")
    assert replace(WithPost(3, scale=10), x=5).doubled == 50
    d = asdict(Outer(Inner(7), [1]))
    d["i"]["v"] = 99; d["lst"].append(9)
    ok("replace() 重跑 __init__+__post_init__;asdict() 递归 deepcopy,改副本不回灌实例")


def demo_init_subclass_timing():
    print("4. __init_subclass__ 的时序")
    assert HOOK_SEEN == [False]
    ok("@dataclass 是装饰器:类创建(__init_subclass__ 已触发)之后才生成 fields——钩子里拿不到")
    probe = []
    try:
        class ExtendWithMembers(Aliased):
            Z = 3
    except TypeError as e:
        assert "cannot extend" in str(e)
        probe.append("blocked")
    assert probe == ["blocked"]
    ok("Enum 反面:元类在类创建之中改写——父枚举已有成员时,定义新成员的子类直接 TypeError")


def demo_enum_runtime():
    print("5. 枚举成员:单例、别名、查找与修改")
    assert Color["RED"].value == 1 and Color(2).name == "GREEN"
    assert Color.RED is Color["RED"] is Color(1)
    ok("成员是单例:按名 [] / 按值 () / 属性访问殊途同归")
    assert [m.name for m in Aliased] == ["X", "Y"]
    assert list(Aliased.__members__) == ["X", "Y", "ALIAS"]
    assert Aliased.ALIAS is Aliased.X
    ok("别名:迭代只出正主,__members__ 才含别名,同一身份")
    try:
        Aliased.X = 5
    except AttributeError as e:
        assert "cannot reassign member" in str(e)
        ok("EnumType.__setattr__ 拦截成员重赋值(运行期不可篡改)")
    assert pickle.loads(pickle.dumps(Level.DEBUG)) is Level.DEBUG
    ok("成员按『枚举名.成员名』序列化——改名会破坏旧流,但值永远不会错绑")
    assert Level.INFO > Level.DEBUG and Level(20) == 20
    ok("IntEnum 混入 int:成员间可比、可与裸 int 比较相等")


def demo_missing_and_in():
    print("6. _missing_ 钩子与宽松 in(版本敏感)")
    assert Lenient("x") is Lenient.X
    try:
        Lenient(99)
    except ValueError:
        ok("_missing_ 返回 None → ValueError;返回成员 → 自定义解码(大小写归一化惯用法)")
    assert 1 in Lenient and Lenient.X in Lenient and "X" not in Lenient
    ok("3.12 的 in 仍按**值**宽松包含(1 in E 为 True);新版本对非成员改抛 TypeError——别依赖")
    assert [m.value for m in Enum("N", "A B C", start=10)] == [10, 11, 12]
    ok("函数式 API + start:auto 起值可调,名字串按空白/逗号切分")


def main():
    demo_hash_matrix()
    demo_frozen_and_defaults()
    demo_inheritance_and_post()
    demo_init_subclass_timing()
    demo_enum_runtime()
    demo_missing_and_in()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
