# -*- coding: utf-8 -*-
"""
Python · 属性查找全链路

用纯 Python 复刻 CPython 的 PyObject_GenericGetAttr + slot_tp_getattro
(数据描述符 > 实例 __dict__ > 非数据描述符 > __getattr__ 兜底),
拿真实类的属性访问当 oracle 对拍;再验 __setattr__/__delattr__ 的同构路由、
特殊方法的"只看类"通道与模块级 __getattr__(PEP 562)。

参考(实读):
  - https://docs.python.org/3/howto/descriptor.html   (优先级链/数据vs非数据)
  - https://docs.python.org/3/reference/datamodel.html#customizing-attribute-access
  - CPython Objects/typeobject.c: PyObject_GenericGetAttr / slot_tp_getattro(语义出处)
"""

import types

PASS = []
MISSING = object()


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


def find_in_mro(cls, name):
    for k in cls.__mro__:
        if name in vars(k):
            return vars(k)[name]
    return MISSING


def generic_getattr(obj, name):
    """复刻 PyObject_GenericGetAttr(不含 __getattr__ 兜底)。"""
    descr = find_in_mro(type(obj), name)
    if descr is not MISSING and (hasattr(descr, "__set__") or hasattr(descr, "__delete__")):
        return descr.__get__(obj, type(obj)) if hasattr(descr, "__get__") else descr
    d = obj.__dict__
    if d is not None and name in d:
        return d[name]
    if descr is not MISSING:
        return descr.__get__(obj, type(obj)) if hasattr(descr, "__get__") else descr
    raise AttributeError(name)


def py_getattr(obj, name):
    """slot_tp_getattro:先走 generic,AttributeError 再问 __getattr__。"""
    try:
        return generic_getattr(obj, name)
    except AttributeError:
        ga = find_in_mro(type(obj), "__getattr__")
        if ga is not MISSING:
            return ga(obj, name)
        raise


def py_setattr(obj, name, value):
    descr = find_in_mro(type(obj), name)
    if descr is not MISSING and hasattr(descr, "__set__"):
        descr.__set__(obj, value)
    else:
        obj.__dict__[name] = value


# ---- oracle 演示类 ----

class DataDesc:
    def __get__(self, obj, owner=None):
        return ("data-desc", obj is not None)

    def __set__(self, obj, value):
        obj.__dict__["_log"] = ("set-via-desc", value)


class NonData:
    def __get__(self, obj, owner=None):
        return ("non-data", obj is not None)


class Animal:
    kind = "animal"                 # 普通类属性
    d = DataDesc()
    n = NonData()

    def speak(self):
        return "..."

    @staticmethod
    def stat():
        return "static"

    @classmethod
    def clsm(cls):
        return cls.__name__

    def __getattr__(self, name):
        return f"__getattr__({name})"


class Dog(Animal):
    kind = "dog"


class Slots:
    __slots__ = ("v",)


def demo_precedence():
    print("1. 优先级链:数据描述符 > 实例 dict > 非数据描述符")
    a = Animal()
    a.__dict__.update(d="in-dict", n="in-dict", speak="shadowed", kind="inst-kind")
    assert py_getattr(a, "d") == getattr(a, "d") == ("data-desc", True)
    ok("数据描述符(带 __set__)压过实例 __dict__——property 永远赢")
    assert py_getattr(a, "n") == getattr(a, "n") == "in-dict"
    ok("实例 __dict__ 压过非数据描述符——所以方法能被实例属性遮蔽")
    assert py_getattr(a, "speak") == getattr(a, "speak") == "shadowed"
    ok("函数是非数据描述符:实例 dict 里的同名值直接盖掉方法(运行期才炸 'str' is not callable)")
    assert py_getattr(a, "kind") == getattr(a, "kind") == "inst-kind"
    assert py_getattr(Dog(), "kind") == "dog"
    ok("普通类属性:实例 dict 优先,否则沿 MRO 取最近定义")


def demo_binding():
    print("2. 描述符 __get__ 的三种绑定")
    a = Animal()
    bm = py_getattr(a, "speak")
    assert bm.__self__ is a and bm == a.speak
    assert py_getattr(Animal, "speak") is Animal.speak
    ok("函数经 __get__(obj, cls) 绑定出 bound method;经类访问(obj=None)拿裸函数,无 __func__ 可剥")
    assert py_getattr(a, "stat")() == "static" and py_getattr(a, "clsm")() == "Animal"
    ok("staticmethod.__get__ 原样返回;classmethod.__get__ 绑定类本身")


def demo_getattr_fallback():
    print("3. __getattr__ 兜底与 __getattribute__ 拦截")

    class Loud:
        def __getattribute__(self, name):
            if name == "spec":
                raise AttributeError("boom")
            return object.__getattribute__(self, name)

        def __getattr__(self, name):
            return f"fallback({name})"

    x = Loud(); x.real = 1
    assert x.real == 1 and x.missing == "fallback(missing)" and x.spec == "fallback(spec)"
    ok("__getattr__ 收两种:查无此名,以及 __getattribute__ 内部抛的 AttributeError")

    class EmptySlot:
        __slots__ = ("v",)

    e = EmptySlot()
    try:
        generic_getattr(e, "v")
    except AttributeError:
        pass
    assert getattr(e, "v", "dflt") == "dflt"
    ok("slot 描述符读未赋值槽 → member_descriptor.__get__ 抛 AttributeError(空槽不是没有属性)")


def demo_setattr_delattr():
    print("4. __setattr__/__delattr__ 的同构路由")
    a = Animal()
    py_setattr(a, "d", 42)
    assert getattr(a, "d") == ("data-desc", True)
    assert a.__dict__["_log"] == ("set-via-desc", 42)
    ok("赋值先查数据描述符 __set__:property/描述符拦截,连实例 dict 都不落")
    py_setattr(a, "plain", 1)
    assert a.__dict__["plain"] == 1
    ok("无数据描述符 → 直接写实例 __dict__(这就是默认行为)")

    class DelRoute:
        def __delete__(self, obj):
            obj.__dict__["deleted"] = True

        def __get__(self, obj, owner=None):
            return 1

    class U:
        x = DelRoute()

    u = U()
    del u.x
    assert u.__dict__["deleted"] is True
    ok("del 同样先路由数据描述符 __delete__")

    assert type(Animal.__dict__["__dict__"]).__name__ == "getset_descriptor"
    assert hasattr(Slots.v, "__set__")
    ok("__dict__/__weakref__ 本身是 getset 描述符;slot 的 member_descriptor 带 __set__ 是数据描述符——全链都在这一套规则里")


def demo_special_channel():
    print("5. 特殊方法通道:只看类")
    li = types.SimpleNamespace()
    li.__len__ = lambda: 0
    try:
        len(li)
        raise SystemExit("should not reach")
    except TypeError as e:
        assert "no len" in str(e)
    ok("len(obj) 走类型槽,实例属性 __len__ 完全被忽略(但 li.__len__ 手动能调)")
    assert li.__len__() == 0


def demo_module_getattr():
    print("6. 模块级 __getattr__(PEP 562)")
    m = types.ModuleType("m")
    m.explicit = "real"
    m.__getattr__ = lambda name: f"lazy({name})"
    assert m.explicit == "real" and m.anything == "lazy(anything)"
    ok("模块属性先查模块 __dict__,查不到才问模块级 __getattr__——惰性导出的官方姿势")


def main():
    demo_precedence()
    demo_binding()
    demo_getattr_fallback()
    demo_setattr_delattr()
    demo_special_channel()
    demo_module_getattr()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
