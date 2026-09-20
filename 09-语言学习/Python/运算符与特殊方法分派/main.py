# -*- coding: utf-8 -*-
"""
Python · 运算符与特殊方法分派 —— 参与分派的类(模型层)

每个类都在被调用时往 LOG 里写一行,自检脚本据此判断"到底调了谁、按什么顺序调"。
自检脚本见 selfcheck_dunders.py:
    python selfcheck_dunders.py

参考(实读):https://docs.python.org/3/reference/datamodel.html
"""

LOG = []


# ------------------------------------------------- 算术:反射与子类优先
class Lhs:
    def __add__(self, other):
        LOG.append("Lhs.__add__")
        return NotImplemented


class Rhs:
    def __radd__(self, other):
        LOG.append("Rhs.__radd__")
        return "rhs"


class RhsSub(Lhs):                        # 子类,且改写了反射方法
    def __radd__(self, other):
        LOG.append("RhsSub.__radd__")
        return "sub"


class Same:
    def __add__(self, other):
        LOG.append("Same.__add__")
        return NotImplemented

    def __radd__(self, other):
        LOG.append("Same.__radd__")
        return "never"


class Blocked:
    __add__ = None                        # 脚注[3]:设成 None 会**显式阻断**回退


class Provider:
    def __radd__(self, other):
        return "provided"


# ------------------------------------------------- in-place 的回退链
class WithIAdd:
    def __init__(self, v):
        self.v = v

    def __iadd__(self, other):
        LOG.append("__iadd__")
        self.v += other
        return self


class IAddNotImplemented:
    def __iadd__(self, other):
        LOG.append("__iadd__->NotImplemented")
        return NotImplemented

    def __add__(self, other):
        LOG.append("__add__")
        return "via __add__"


class OnlyAdd:
    def __add__(self, other):
        LOG.append("OnlyAdd.__add__")
        return "only __add__"


# ------------------------------------------------- 比较
class AlwaysEqual:
    def __eq__(self, other):
        LOG.append("__eq__")
        return True


class SubEq(AlwaysEqual):                 # 子类改写 __eq__,子类优先
    def __eq__(self, other):
        LOG.append("SubEq.__eq__")
        return "sub"


class OnlyLt:
    def __init__(self, v):
        self.v = v

    def __lt__(self, other):
        LOG.append("__lt__")
        return self.v < getattr(other, "v", other)


class Nothing:
    pass


# ------------------------------------------------- 哈希契约
class NoHash:
    def __eq__(self, other):
        return isinstance(other, NoHash)


class KeepHash(NoHash):
    __hash__ = object.__hash__          # 规范:必须显式"告知"解释器


# ------------------------------------------------- 真值测试
class ByLen:
    def __init__(self, n):
        self.n = n

    def __len__(self):
        return self.n


class BadBool:
    def __bool__(self):
        return 1                        # 非 bool → TypeError
