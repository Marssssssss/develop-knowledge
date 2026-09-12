"""
Python 描述符协议(Descriptor Protocol)核心机制

涵盖:第 1 节 数据/非数据描述符 优先级链;第 2 节 property 等价实现;
第 3 节 函数也是描述符 + 绑定方法;第 4 节 staticmethod/classmethod;
第 5 节 __set_name__(PEP 487);第 6 节 Validator + ORM Field 实战。

运行:python3 descriptor.py
"""

from __future__ import annotations

import abc


# 第 1 节:数据描述符 vs 非数据描述符

class NonDataDescriptor:
    """只定义 __get__:非数据描述符,实例字典可覆盖它"""

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return f"<NonDataDescriptor on {obj.name}>"


class DataDescriptor:
    """同时定义 __get__ + __set__:数据描述符,实例字典无法覆盖"""

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        return obj.__dict__.get("__data_value__", "<unset>")

    def __set__(self, obj, value):
        # 任何赋值都被描述符拦截,不会进入实例字典
        obj.__dict__["__data_value__"] = value


class Widget:
    """演示两种描述符的优先级"""
    non_data = NonDataDescriptor()      # 非数据描述符
    data = DataDescriptor()             # 数据描述符

    def __init__(self, name):
        self.name = name


# =========================================================================
# 第 2 节:property 的纯 Python 等价实现
# =========================================================================

class Property:
    """纯 Python 等价于内置 property() —— 验证 property 是数据描述符
    同时定义 __get__ 与 __set__ ⇒ 数据描述符(实例字典无法覆盖)"""

    def __init__(self, fget=None, fset=None, fdel=None, doc=None):
        self.fget = fget
        self.fset = fset
        self.fdel = fdel
        if doc is None and fget is not None:
            doc = fget.__doc__
        self.__doc__ = doc

    def __set_name__(self, owner, name):
        self.__name__ = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        if self.fget is None:
            raise AttributeError("readonly")
        return self.fget(obj)

    def __set__(self, obj, value):
        if self.fset is None:
            raise AttributeError("can't set attribute")
        self.fset(obj, value)

    def __delete__(self, obj):
        if self.fdel is None:
            raise AttributeError("can't delete attribute")
        self.fdel(obj)


class Temperature:
    def __init__(self, celsius):
        self._celsius = celsius

    @Property                        # 用我们自己的描述符验证(等价于 @property)
    def celsius(self):
        """摄氏度"""
        return self._celsius

    @celsius.setter
    def celsius(self, value):
        if value < -273.15:
            raise ValueError("低于绝对零度")
        self._celsius = value


# =========================================================================
# 第 3 节 + 第 4 节:函数 / staticmethod / classmethod 的 __get__ 演示
# =========================================================================

class FunctionDescriptorDemo:
    """函数、staticmethod、classmethod 的描述符行为差异
    - 函数:非数据描述符,__get__ 绑定 self 返回 bound method
    - staticmethod:__get__ 直接返回原函数,不绑定
    - classmethod:__get__ 把 cls 作为第一参数绑定"""

    def instance_method(self, x):
        return f"instance_method({self!r}, x={x})"

    @staticmethod
    def static_method(x):
        return f"static_method(x={x})"

    @classmethod
    def class_method(cls, x):
        return f"class_method(cls={cls.__name__}, x={x})"


# =========================================================================
# 第 5 节:__set_name__ —— 描述符自动获知属性名(PEP 487)
# =========================================================================

class LoggedAccess:
    """演示 PEP 487:描述符创建时自动获得属性名(类创建时 type.__new__ 自动调用)"""

    def __set_name__(self, owner, name):
        self.public_name = name
        self.private_name = "_" + name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        print(f"    [LOG] read {self.public_name}")
        return getattr(obj, self.private_name)

    def __set__(self, obj, value):
        print(f"    [LOG] write {self.public_name} = {value!r}")
        setattr(obj, self.private_name, value)


class Person:
    name = LoggedAccess()
    age = LoggedAccess()

    def __init__(self, name, age):
        self.name = name                  # 触发 __set_name__ 中记录的 private_name
        self.age = age


# =========================================================================
# 第 6 节:Validator 描述符 —— 文档原版的"完整"示例
# =========================================================================

class Validator(abc.ABC):
    """基类:__set__ 时做 validate 校验"""

    def __set_name__(self, owner, name):
        self.private_name = "_" + name

    def __get__(self, obj, objtype=None):
        return getattr(obj, self.private_name)

    def __set__(self, obj, value):
        self.validate(value)
        setattr(obj, self.private_name, value)

    @abc.abstractmethod
    def validate(self, value):
        ...


class OneOf(Validator):
    def __init__(self, *options):
        self.options = set(options)

    def validate(self, value):
        if value not in self.options:
            raise ValueError(f"Expected {value!r} to be one of {self.options!r}")


class Number(Validator):
    def __init__(self, minvalue=None, maxvalue=None):
        self.minvalue = minvalue
        self.maxvalue = maxvalue

    def validate(self, value):
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError(f"Expected {value!r} to be a number")
        if self.minvalue is not None and value < self.minvalue:
            raise ValueError(f"Expected {value!r} >= {self.minvalue!r}")
        if self.maxvalue is not None and value > self.maxvalue:
            raise ValueError(f"Expected {value!r} <= {self.maxvalue!r}")


class Order:
    """真实应用:多个描述符协作做类型校验"""
    status = OneOf("pending", "shipped", "delivered")
    quantity = Number(minvalue=0, maxvalue=999)

    def __init__(self, status, quantity):
        self.status = status
        self.quantity = quantity


# =========================================================================
# 第 6 节 b:ORM 风格的 Field 描述符(简化版)
# =========================================================================

class Field:
    """ORM Field:把属性访问代理到外部存储(实际 ORM 都靠这个机制)"""

    def __set_name__(self, owner, name):
        self.column = name

    def __get__(self, obj, objtype=None):
        return obj._data[self.column]

    def __set__(self, obj, value):
        obj._data[self.column] = value


class User:
    name = Field()
    email = Field()

    def __init__(self, name, email):
        self._data = {}
        self.name = name
        self.email = email


# 演示驱动

def main():
    print("=== 第 1 节:数据 vs 非数据描述符的优先级 ===")
    w = Widget("alpha")
    print(f"  w.non_data         = {w.non_data!r}")
    w.__dict__["non_data"] = "实例字典里的同名属性"
    print(f"  设了实例字典后:w.non_data = {w.non_data!r}")
    print("  → 非数据描述符被实例字典『覆盖』了(实例字典优先级高)")
    w.data = "DATA-value"
    print(f"  w.data = 'DATA-value' → w.data = {w.data!r}")
    w.__dict__["data"] = "想绕过描述符直接写"
    print(f"  设了实例字典后:w.data = {w.data!r}")
    print("  → 数据描述符没被覆盖(优先级最高)")

    print("\n=== 第 2 节:property 的描述符实现 ===")
    t = Temperature(25.0)
    print(f"  t.celsius          = {t.celsius}")          # 触发 __get__
    t.celsius = 30.0                                       # 触发 __set__
    print(f"  t.celsius = 30.0 后 → t.celsius = {t.celsius}")
    try:
        t.celsius = -300
    except ValueError as e:
        print(f"  t.celsius = -300    → {e}")

    print("\n=== 第 3 + 4 节:函数 / staticmethod / classmethod 的 __get__ 行为 ===")
    obj = FunctionDescriptorDemo()
    print(f"  obj.instance_method(1) → {obj.instance_method(1)}")
    print(f"  obj.static_method(1)   → {obj.static_method(1)}")
    print(f"  FunctionDescriptorDemo.class_method(2) → "
          f"{FunctionDescriptorDemo.class_method(2)}")
    print(f"  obj.class_method(2)    → {obj.class_method(2)}")
    print(f"  函数是『非数据描述符』,staticmethod 不绑定 self,classmethod 把 cls 作首参")

    print("\n=== 第 5 节:__set_name__ 自动获知属性名 ===")
    p = Person("Alice", 30)
    print(f"  读 p.name           → {p.name!r}     # 触发 LOGgedAccess.__get__")
    print(f"  LoggedAccess.public_name == 'name': "
          f"{vars(vars(Person)['name'])['public_name'] == 'name'}")
    print(f"  LoggedAccess.public_name == 'age':  "
          f"{vars(vars(Person)['age'])['public_name'] == 'age'}")

    print("\n=== 第 6 节:Validator 描述符 ===")
    o = Order("shipped", 5)
    print(f"  Order('shipped', 5)   → OK")
    try:
        Order("invalid-status", 5)
    except ValueError as e:
        print(f"  Order('invalid-status', 5) → ValueError: {e}")
    try:
        Order("pending", -1)
    except ValueError as e:
        print(f"  Order('pending', -1)       → ValueError: {e}")

    print("\n=== 第 6 节 b:ORM Field 描述符 ===")
    u = User("Alice", "alice@example.com")
    print(f"  u.name  = {u.name!r}    (从 _data dict 读)")
    print(f"  u.email = {u.email!r}")
    u.name = "Bob"
    print(f"  u.name = 'Bob' → u.name = {u.name!r}, u._data = {u._data!r}")


if __name__ == "__main__":
    main()