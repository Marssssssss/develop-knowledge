"""
Python 装饰器 · 基础版

展示:
- 装饰器 = 接受函数 + 返回函数的高阶函数
- @decorator 是语法糖,等价于 func = decorator(func)
- functools.wraps 保留原函数元信息
- *args / **kwargs 透传任意参数

运行:
    python3 basic.py
预期输出见 README.md 的「运行方式」。
"""

from __future__ import annotations

import functools
import time
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable[..., object])


def logger(func: F) -> F:
    """装饰器:打印「调用前 / 调用后 / 耗时」"""
    @functools.wraps(func)          # 保留 __name__ / __doc__ / __signature__
    def wrapper(*args, **kwargs):
        print(f"[LOG] call: {func.__name__}({args=}, {kwargs=})")
        t0 = time.perf_counter()
        try:
            result = func(*args, **kwargs)
        finally:
            dt = time.perf_counter() - t0
            print(f"[LOG] done : {func.__name__} -> {result!r} (took {dt:.2f}s)")
        return result
    return wrapper          # type: ignore[return-value]


@logger
def greet(name: str) -> str:
    """Say hello."""
    time.sleep(0.1)
    return f"Hello, {name}!"


@logger
def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    print("=== 基础装饰器:日志 + 计时 ===\n")
    greet("Alice")
    greet("Bob")
    print()
    add(1, 2)

    # 验证 functools.wraps 的作用
    print()
    print("=== functools.wraps 的作用 ===")
    print(f"greet.__name__ = {greet.__name__!r}    # 应该是 'greet',不是 'wrapper'")
    print(f"greet.__doc__  = {greet.__doc__!r}")