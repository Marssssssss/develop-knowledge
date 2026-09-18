"""极简断言框架：各小节共享计数器，末尾统一汇总并用退出码反映成败。

单独成文件是为了让各校验小节（demo.py / checks_ingest.py）都能用同一套
计数器 —— 否则拆文件必然出现"两份计数器、各报各的数字"。
"""

from __future__ import annotations

from logql_ast import LogQLError

__all__ = ["check", "eq", "close", "raises", "section", "report"]

_PASSED = 0
_FAILED: list[str] = []
_SECTION = "?"


def check(label: str, cond: bool, detail: str = "") -> None:
    global _PASSED
    if cond:
        _PASSED += 1
    else:
        _FAILED.append(f"{label} [{_SECTION}] {detail}")


def eq(label: str, got, want, tol: float = 0.0) -> None:
    """相等断言。两侧都是数值时给容差，避免浮点等值直接比。"""
    if isinstance(want, float) and isinstance(got, (int, float)):
        check(label, abs(got - want) <= tol, f"got={got!r} want≈{want!r} tol={tol}")
    else:
        check(label, got == want, f"got={got!r} want={want!r}")


def close(label: str, got: float, want: float, tol: float = 1e-9) -> None:
    eq(label, got, want, tol)


def raises(label: str, fn, exc=LogQLError) -> None:
    """断言 fn 抛出 exc。抛了别的类型也算失败——那通常意味着接错了异常类。"""
    try:
        fn()
    except exc:
        check(label, True)
        return
    except Exception as other:  # noqa: BLE001
        check(label, False, f"抛了非预期异常 {type(other).__name__}: {other}")
        return
    check(label, False, "没有抛异常")


def section(name: str) -> None:
    global _SECTION
    _SECTION = name


def report() -> int:
    """打印汇总，返回进程退出码。"""
    print(f"PASSED {_PASSED}  FAILED {len(_FAILED)}")
    for item in _FAILED:
        print("  FAIL", item)
    return 1 if _FAILED else 0
