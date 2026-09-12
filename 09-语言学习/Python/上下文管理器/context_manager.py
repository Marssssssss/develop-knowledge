"""
Python 上下文管理器(context manager)核心机制

涵盖:
- 第 1 节:经典 class-based 实现 —— __enter__/__exit__ 协议
- 第 2 节:@contextmanager 生成器实现 —— 一行 yield 替代双方法
- 第 3 节:__exit__ 返回值规则 —— 真值抑制异常,假值传播
- 第 4 节:ExitStack 动态组合多个上下文
- 第 5 节:suppress / closing / nullcontext 等实用工具
- 第 6 节:@asynccontextmanager 异步版(Python 3.7+)

运行:
    python3 context_manager.py
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from typing import IO, Optional


# =========================================================================
# 第 1 节:Class-based 实现 —— DatabaseConnection 经典模式
# =========================================================================

class DatabaseConnection:
    """演示 __enter__/__exit__ 协议的标准用法
    - __enter__:进入 with 块时调用,返回值绑定到 as 子句
    - __exit__(exc_type, exc_val, exc_tb):退出 with 块时调用(无论是否异常)
    - 返回 True 抑制异常,False/None 让异常继续向上抛"""

    def __init__(self, db_name: str):
        self.db_name = db_name
        self.conn: Optional[str] = None

    def __enter__(self) -> "DatabaseConnection":
        self.conn = f"<connection to {self.db_name}>"
        print(f"  [DB] open  → {self.conn}")
        return self                       # 绑定到 as 后的变量

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 有异常 → 回滚事务;return False 让异常继续传播
            print(f"  [DB] rollback (exc={exc_type.__name__}: {exc_val})")
            self.conn = None
            return False                  # 不抑制异常,抛给上层
        # 正常退出 → 提交事务
        print(f"  [DB] commit → {self.conn}")
        self.conn = None
        return False


# =========================================================================
# 第 2 节:@contextmanager 生成器实现 —— 一行 yield 替代双方法
# =========================================================================

@contextlib.contextmanager
def timer(label: str = "block"):
    """计时上下文管理器:典型 @contextmanager 用法
    yield 之前的代码 = __enter__,yield 之后的代码 = __exit__
    """
    t0 = time.perf_counter()
    print(f"  [timer {label}] start")
    try:
        yield t0                          # as 子句拿到 t0
    finally:
        dt = time.perf_counter() - t0
        print(f"  [timer {label}] end   ({dt * 1000:.1f} ms)")


@contextlib.contextmanager
def temporary_directory():
    """@contextmanager 包装 try/finally —— cleanup 自动保证"""
    tmpdir = tempfile.mkdtemp(prefix="ctx_demo_")
    print(f"  [tempdir] create → {tmpdir}")
    try:
        yield tmpdir
    finally:
        os.rmdir(tmpdir)
        print(f"  [tempdir] remove → {tmpdir}")


# =========================================================================
# 第 3 节:__exit__ 返回值 —— 真值抑制异常的陷阱
# =========================================================================

class SuppressError:
    """演示:__exit__ 返回 True 会『吞掉』异常"""

    def __enter__(self):
        print("  [SuppressError] enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            print(f"  [SuppressError] suppress  {exc_type.__name__}: {exc_val}")
        return True                       # ← 关键:True 抑制异常,不向上传播


class PassThroughError:
    """演示:__exit__ 返回 None/False 让异常继续传播"""

    def __enter__(self):
        print("  [PassThroughError] enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            print(f"  [PassThroughError] propagate  {exc_type.__name__}: {exc_val}")
        return None                      # ← 关键:不抑制


# =========================================================================
# 第 4 节:ExitStack 动态组合
# =========================================================================

def write_three_files(stack: contextlib.ExitStack, filenames):
    """动态打开多个文件,用 ExitStack 保证全部关闭"""
    handles = []
    for fname in filenames:
        fh = stack.enter_context(open(fname, "w"))
        handles.append(fh)
    return handles


# =========================================================================
# 第 5 节:suppress / closing / nullcontext
# =========================================================================

@contextlib.contextmanager
def trace_suppress(label, exc_type):
    """包装 suppress 以便观察触发情况"""
    print(f"  [suppress {label}] before (will suppress {exc_type.__name__})")
    with contextlib.suppress(exc_type):
        yield
    print(f"  [suppress {label}] after (no re-raise)")


# =========================================================================
# 第 6 节:@asynccontextmanager
# =========================================================================

import asyncio


@contextlib.asynccontextmanager
async def async_timer(label: str = "async-block"):
    """异步计时器:yield 之前 await = __enter__,yield 之后 await = __exit__"""
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    print(f"  [async-timer {label}] start")
    try:
        yield t0
    finally:
        dt = loop.time() - t0
        print(f"  [async-timer {label}] end   ({dt * 1000:.1f} ms)")


async def run_async_demo():
    async with async_timer("async-with"):
        await asyncio.sleep(0.05)


# =========================================================================
# 演示驱动
# =========================================================================

def main():
    # ---- 第 1 节:class-based ----
    print("=== 第 1 节:class-based 实现 ===")
    with DatabaseConnection("orders_db") as db:
        print(f"  use → {db.conn}")
    # 正常路径:commit
    try:
        with DatabaseConnection("users_db") as db:
            raise RuntimeError("模拟写库失败")
    except RuntimeError:
        pass    # rollback 已打印

    # ---- 第 2 节:@contextmanager ----
    print("\n=== 第 2 节:@contextmanager 生成器实现 ===")
    with timer("case-A"):
        time.sleep(0.05)
    with temporary_directory() as tmp:
        open(os.path.join(tmp, "a.txt"), "w").close()
        print(f"  use  → {tmp}")

    # ---- 第 3 节:__exit__ 返回值 ----
    print("\n=== 第 3 节:__exit__ 返回值规则 ===")
    try:
        with SuppressError():
            raise ValueError("不该冒出来")
    except ValueError:
        print("  ✗ ValueError 漏到外层了(异常抑制失败)")
    print("  ✓ ValueError 被 SuppressError 完全吞掉,with 块后续代码照常运行")
    with SuppressError():
        print("  这行仍然执行 —— 因为异常被吞了")

    try:
        with PassThroughError():
            raise ValueError("会冒出来")
    except ValueError as e:
        print(f"  ✓ PassThroughError 让异常正常传播: {e}")

    # ---- 第 4 节:ExitStack ----
    print("\n=== 第 4 节:ExitStack 动态组合 ===")
    with contextlib.ExitStack() as stack:
        files = write_three_files(stack, ["ctx_a.txt", "ctx_b.txt", "ctx_c.txt"])
        for fh in files:
            fh.write("hello\n")
        print(f"  opened {len(files)} files; with 退出时全部自动关闭")
    # 清理
    for name in ("ctx_a.txt", "ctx_b.txt", "ctx_c.txt"):
        if os.path.exists(name):
            os.remove(name)

    # ---- 第 5 节:suppress / closing / nullcontext ----
    print("\n=== 第 5 节:suppress / closing / nullcontext ===")
    with trace_suppress("KeyError", KeyError):
        raise KeyError("missing-key")
    print("  ✓ KeyError 被 suppress,后续代码继续")

    # nullcontext 用法 —— 条件性上下文管理器
    debug = False
    with contextlib.nullcontext() if not debug else timer("debug-mode"):
        print("  pass-through 时 nullcontext() 等同于 no-op")

    # ---- 第 6 节:@asynccontextmanager ----
    print("\n=== 第 6 节:@asynccontextmanager 异步版 ===")
    asyncio.run(run_async_demo())


if __name__ == "__main__":
    main()