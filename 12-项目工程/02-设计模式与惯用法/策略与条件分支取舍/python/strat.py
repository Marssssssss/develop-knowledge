# -*- coding: utf-8 -*-
"""策略模式与条件分支的取舍模型。

口径(实读源):refactoring.guru Strategy 页(Context/Strategy/Concrete 结构、
构造注入 + 运行时 setter 换策略);分支→多态的重构入口是目录中的
Replace Conditional with Polymorphism。分支数阈值为本 demo 的启发式(标注为模型)。
"""


class Strategy:
    def execute(self, amount):  # pragma: no cover - 接口声明
        raise NotImplementedError


class NormalStrategy(Strategy):
    def execute(self, amount):
        return amount


class SeniorStrategy(Strategy):
    def execute(self, amount):
        return amount * 0.7


class VipStrategy(Strategy):
    def execute(self, amount):
        return amount * 0.5


class Context:
    """Context 只认识 Strategy 接口;构造注入 + 运行时可换。"""

    def __init__(self, strategy):
        self._strategy = strategy

    def set_strategy(self, strategy):
        self._strategy = strategy

    def checkout(self, amount):
        return self._strategy.execute(amount)


def branch_version(kind, amount):
    """条件分支版:策略种类写死在调用处。"""
    if kind == "normal":
        return amount
    elif kind == "senior":
        return amount * 0.7
    elif kind == "vip":
        return amount * 0.5
    raise ValueError(kind)


REGISTRY = {"normal": NormalStrategy, "senior": SeniorStrategy, "vip": VipStrategy}


def add_strategy(name, cls):
    """加新策略 = 注册新类;Context 与既有策略零改动。"""
    REGISTRY[name] = cls


def branch_duplication_cost(n_branches, n_call_sites):
    """分支版的维护账本(模型):每处调用点都长着同一棵分支树。"""
    return n_branches * n_call_sites


def strategy_fixed_cost(n_strategies):
    """策略版:1 个接口 + 每策略 1 个类。"""
    return n_strategies + 1
