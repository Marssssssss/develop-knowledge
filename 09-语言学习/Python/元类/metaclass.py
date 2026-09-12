"""
Python 元类(metaclass)与 __init_subclass__ 核心机制

涵盖:
- 第 1 节:type() 三参数动态创建类
- 第 2 节:metaclass= 关键字声明 + 类创建钩子 __prepare__/__new__/__init__
- 第 3 节:经典示例 —— OrderedClass 保留属性声明顺序(PEP 3115)
- 第 4 节:__init_subclass__(PEP 487) —— 子类注册插件系统
- 第 5 节:__set_name__(PEP 487) —— 描述符自动获知属性名
- 第 6 节:元类 vs __init_subclass__ 的取舍

运行:
    python3 metaclass.py
"""

from __future__ import annotations


# =========================================================================
# 第 1 节:type() 的三参数形式 —— 类本身的对象工厂
# =========================================================================

# 1) 单参数:type(obj) → 对象的类型
print(f"=== 第 1 节:type() 三参数动态创建类 ===")
print(f"  type(42)             = {type(42)!r}")          # <class 'int'>
print(f"  type('A', (), {{}})   = {type('A', (), {})!r}") # <class '__main__.A'>

# 2) 三参数:type(name, bases, namespace) → 动态创建一个类
def init_method(self, value):
    self.value = value

DynamicClass = type(
    "DynamicClass",                                  # 类名
    (object,),                                       # 基类 tuple
    {                                                # 类字典(namespace)
        "__init__": init_method,
        "describe": lambda self: f"DynamicClass(value={self.value})",
    },
)
print(f"  DynamicClass.__name__ = {DynamicClass.__name__!r}")
print(f"  DynamicClass.__mro__  = {DynamicClass.__mro__!r}")
inst = DynamicClass(42)
print(f"  inst.describe()       = {inst.describe()!r}")

# 关键:type 是所有类的『默认元类』—— 自定义元类就是继承 type


# =========================================================================
# 第 2 节:metaclass= 关键字 + 类创建钩子
# =========================================================================

class TracingMeta(type):
    """自定义元类:在 __new__ 与 __init__ 中插入追踪日志"""

    def __prepare__(name, bases, **kwargs):
        """类体执行前的命名空间准备;默认是普通 dict
        可返回 OrderedDict / 自定义 dict(保留插入顺序,见第 3 节)"""
        print(f"  [TracingMeta.__prepare__]  name={name!r}, bases={[b.__name__ for b in bases]!r}")
        return {}                                     # 这里用普通 dict

    def __new__(mcs, name, bases, namespace, **kwargs):
        """创建类对象前的最后一步:可修改 namespace"""
        ns = dict(namespace)
        ns["_created_by"] = mcs.__name__
        print(f"  [TracingMeta.__new__]     name={name!r}, namespace keys={sorted(ns.keys())!r}")
        cls = super().__new__(mcs, name, bases, ns, **kwargs)
        return cls

    def __init__(cls, name, bases, namespace, **kwargs):
        """类对象创建后的初始化"""
        print(f"  [TracingMeta.__init__]    name={name!r}, __mro__={[b.__name__ for b in cls.__mro__]!r}")
        super().__init__(name, bases, namespace, **kwargs)


class Traced(metaclass=TracingMeta):
    """演示 metaclass= 关键字:类创建时 TracingMeta 三个钩子依次触发"""
    def hello(self):
        return f"hello from {type(self).__name__}"

print(f"  Traced._created_by       = {Traced._created_by!r}")
print(f"  Traced().hello()         = {Traced().hello()!r}")


# =========================================================================
# 第 3 节:OrderedClass —— 保留类体属性声明顺序(PEP 3115 原版示例)
# =========================================================================

class member_table(dict):
    """自定义 dict:__setitem__ 时记录 key 的首次插入顺序"""
    def __init__(self):
        super().__init__()
        self.member_names = []

    def __setitem__(self, key, value):
        if key not in self:
            self.member_names.append(key)
        super().__setitem__(key, value)


class OrderedClass(type):
    """用 OrderedClass 元类创建类时,自动收集属性声明顺序"""

    @classmethod
    def __prepare__(mcs, name, bases):
        return member_table()                         # ← 关键:返回自定义 dict

    def __new__(mcs, name, bases, classdict):
        # classdict.member_names 包含所有属性的首次声明顺序
        result = type.__new__(mcs, name, bases, dict(classdict))
        result.member_names = classdict.member_names
        return result


class MyClass(metaclass=OrderedClass):
    z = 1                                              # 1st
    def method_first(self): pass                       # 2nd
    y = 2                                              # 3rd
    def method_second(self): pass                      # 4th
    x = 3                                              # 5th


print(f"\n=== 第 3 节:OrderedClass 保留属性顺序 ===")
print(f"  MyClass.member_names = {MyClass.member_names!r}")
print(f"  → 顺序与源代码声明顺序一致")


# =========================================================================
# 第 4 节:__init_subclass__(PEP 487) —— 轻量替代 metaclass
# =========================================================================

class PluginBase:
    """插件基类:用 __init_subclass__ 自动注册所有子类

    对比元类方案,优势:
      - 不参与『类类型』系统,不会与其他库的 metaclass 冲突
      - 像普通方法继承一样可被理解
    """
    plugins: list = []                                 # 类级别注册表

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)            # 合作式多继承
        cls.plugins.append(cls)
        print(f"  [PluginBase] registered  {cls.__name__!r} → {cls.__module__}")


class CSVExporter(PluginBase):
    "导出为 CSV"
    def export(self, data): pass


class JSONExporter(PluginBase):
    "导出为 JSON"
    def export(self, data): pass


class XMLExporter(PluginBase):
    "导出为 XML"
    def export(self, data): pass


print(f"\n=== 第 4 节:__init_subclass__ 插件注册 ===")
print(f"  PluginBase.plugins = {[p.__name__ for p in PluginBase.plugins]!r}")


# 第 4 节 b:__init_subclass__ 接受关键字参数
class Configurable:
    def __init_subclass__(cls, *, label="default", **kwargs):
        super().__init_subclass__(**kwargs)
        cls.label = label                             # 把参数转成类属性


class MyWidget(Configurable, label="primary"):
    pass


print(f"  MyWidget.label          = {MyWidget.label!r}    # 通过 __init_subclass__ 注入")


# =========================================================================
# 第 5 节:__set_name__ —— 描述符自动获知属性名(PEP 487)
# =========================================================================

class ValidatedField:
    """演示 __set_name__:类创建时自动获得 (owner, name)"""

    def __init__(self, validator):
        self.validator = validator

    def __set_name__(self, owner, name):
        # ← 在类创建时由 type.__new__ 自动调用
        self.public_name = name
        self.private_name = "_" + name

    def __get__(self, obj, objtype=None):
        if obj is None: return self
        return getattr(obj, self.private_name)

    def __set__(self, obj, value):
        if not self.validator(value):
            raise ValueError(f"Field {self.public_name!r} rejected {value!r}")
        setattr(obj, self.private_name, value)


class Person:
    name = ValidatedField(lambda v: isinstance(v, str) and 0 < len(v) <= 20)
    age  = ValidatedField(lambda v: isinstance(v, int) and 0 <= v <= 150)


print(f"\n=== 第 5 节:__set_name__ 自动获知属性名 ===")
print(f"  Person.name.public_name   = {vars(vars(Person)['name'])['public_name']!r}")
print(f"  Person.age.public_name    = {vars(vars(Person)['age'])['public_name']!r}")

p = Person()
p.name = "Alice"
p.age = 30
print(f"  p.name = 'Alice', p.age = 30 → p.name = {p.name!r}, p.age = {p.age}")
try:
    Person()
except TypeError:
    pass                                             # Positional args 缺失
p.name = "x" * 100
try:
    p.name = "x" * 100
except ValueError as e:
    print(f"  p.name = 'x'*100 → ValueError: {e}")


# =========================================================================
# 第 6 节:元类 vs __init_subclass__ 取舍
# =========================================================================

print(f"\n=== 第 6 节:取舍速查 ===")
print(f"  | 场景                                      | 推荐机制            |")
print(f"  | 注册子类 / 注入参数                       | __init_subclass__   |")
print(f"  | 描述符自动获知属性名                       | __set_name__        |")
print(f"  | 保留属性声明顺序                          | 元类 + __prepare__ |")
print(f"  | 拦截 type(name, bases, dict) 三参调用    | 元类                |")
print(f"  | 多个类装饰器 / 同时多个第三方元类场景     | __init_subclass__   |")
print(f"  → metaclass= 全局影响类的『类型』,易与其他库冲突(『元类冲突』)")
print(f"  → __init_subclass__ 只是类继承链上的方法,不会污染类型系统")