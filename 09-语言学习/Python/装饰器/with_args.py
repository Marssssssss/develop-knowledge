"""
Python 装饰器 · 带参数的版本(装饰器工厂)

展示:
- 「装饰器」其实常常是「返回装饰器的工厂函数」
- 三层闭包:外层参数 → 装饰器 → wrapper
- 用 random 让 flaky 函数有时失败,演示 retry 装饰器生效

运行:
    python3 with_args.py
预期输出见 README.md 的「运行方式」。
"""

from __future__ import annotations

import functools
import random
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable[..., object])


def retry(max_attempts: int = 3):
    """装饰器工厂:retry(参数) 返回真正的装饰器"""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exc = e
                    print(f"[RETRY] attempt {attempt}/{max_attempts} failed: {e!r}")
            # 所有重试用完,抛出最后一次的异常
            assert last_exc is not None
            raise last_exc
        return wrapper      # type: ignore[return-value]
    return decorator


# ----------------------------------------------------------------------
# 演示 1:不依赖 random,用「前两次必然失败」让输出完全可控
# ----------------------------------------------------------------------

_attempt_counter = {"n": 0}

@retry(max_attempts=4)
def flaky_deterministic():
    """前两次故意抛异常,第三次成功 —— 输出完全可预测"""
    _attempt_counter["n"] += 1
    if _attempt_counter["n"] < 3:
        raise RuntimeError(f"故意失败第 {_attempt_counter['n']} 次")
    return f"成功于第 {_attempt_counter['n']} 次"


# ----------------------------------------------------------------------
# 演示 2:用 random 让 flaky 真的随机失败 —— 真正体现「重试的价值」
# ----------------------------------------------------------------------

@retry(max_attempts=3)
def flaky_random() -> str:
    if random.random() < 0.5:
        raise RuntimeError("随机失败")
    return "一次就成功"


if __name__ == "__main__":
    print("=== 带参数的装饰器:retry ===\n")
    print("--- 演示 1:可控失败(前两次必失败) ---")
    print(flaky_deterministic())

    print()
    print("--- 演示 2:随机失败(概率 50%) ---")
    print(flaky_random())

    # 验证 functools.wraps 仍然生效
    print()
    print("=== wraps 的作用 ===")
    print(f"flaky_deterministic.__name__ = {flaky_deterministic.__name__!r}")