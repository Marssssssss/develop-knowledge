# -*- coding: utf-8 -*-
"""单例的代价与依赖注入/服务定位器模型。

口径(实读源):
  refactoring.guru Singleton 页(Pros/Cons 与两个问题)
  Martin Fowler《Inversion of Control Containers and the Dependency Injection pattern》
  —— Service Locator vs Dependency Injection 一节与"分离配置与使用"原则
"""

import threading


class SingletonMeta(type):
    """线程安全单例:首次请求才初始化(lazy)。"""

    _instances = {}
    _lock = threading.Lock()
    init_calls = 0

    def __call__(cls, *a, **kw):
        with cls._lock:
            if cls not in cls._instances:
                cls.init_calls += 1
                cls._instances[cls] = super().__call__(*a, **kw)
        return cls._instances[cls]


class Config(metaclass=SingletonMeta):
    def __init__(self):
        self.source = "prod"

    @staticmethod
    def instance():
        return Config()


class ServiceLocator:
    """Fowler:应用类『显式地』向定位器发消息要服务——每个使用者都依赖定位器。"""

    _services = {}

    @classmethod
    def provide(cls, name, impl):
        cls._services[name] = impl

    @classmethod
    def get(cls, name):
        return cls._services[name]


class ClientWithLocator:
    """依赖集:隐式服务 + 显式的定位器本身。"""

    def __init__(self):
        self.db = ServiceLocator.get("db")

    def dependencies(self):
        return ["db", "ServiceLocator"]        # 定位器进了依赖清单


class ClientWithDI:
    """依赖集 = 构造签名本身:看一眼 __init__ 参数就知道全部依赖。"""

    def __init__(self, db):
        self.db = db

    def dependencies(self):
        return ["db"]                          # 构造注入:无隐藏依赖


INJECTION_FORMS = ["Constructor Injection", "Setter Injection", "Interface Injection"]
