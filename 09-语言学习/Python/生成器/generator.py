"""
Python 生成器与 yield 核心机制

涵盖:
- 基础:含 yield 的函数就是生成器函数
- send / throw / close 三方法的语义
- yield from (PEP 380) 委派语义
- 异步生成器的关键差异(PEP 525)

运行:
    python3 generator.py
预期输出见 README.md「运行方式」。
"""

from __future__ import annotations

# =========================================================================
# 第 1 节:基础生成器
# =========================================================================

def simple_gen():
    """最简单的生成器:每次 next() 从 yield 处恢复"""
    print("  [simple_gen] 执行到 yield 1")
    yield 1
    print("  [simple_gen] 执行到 yield 2")
    yield 2
    print("  [simple_gen] 执行结束")


# =========================================================================
# 第 2 节:send / throw / close 三方法(官方文档 6.2.10.2 经典示例)
# =========================================================================

def echo(value=None):
    """echo 生成器,展示 send/throw/close 的完整行为
    yield 表达式的当前值 = 传入 send() 的参数;首次必须 send(None)。"""
    print("  [echo] Execution starts when 'next()' is called for the first time.")
    try:
        while True:
            try:
                value = (yield value)
            except Exception as e:
                value = e
    finally:
        print("  [echo] Don't forget to clean up when 'close()' is called.")


# =========================================================================
# 第 3 节:yield from (PEP 380) —— 委派到子迭代器
# =========================================================================

def inner():
    """子生成器:yield from 会把外层的 send/throw/close 直接转发给我"""
    r = yield "A"
    print(f"  [inner] 收到 send({r!r})")
    yield "B"
    return "inner_done"   # 返回值通过 StopIteration.value 传出


def outer():
    """外层生成器:用 yield from 委派给 inner
    关键:yield from 自动建立双向通道 —— 不必手动转发 send/throw/close"""
    result = yield from inner()
    print(f"  [outer] inner 的返回值 = {result!r}")
    yield "outer_done"


# =========================================================================
# 第 4 节:典型应用 —— 惰性数据流管道
# =========================================================================

def integers():
    """无限自然数流(惰性,占用 O(1) 内存)"""
    n = 1
    while True:
        yield n
        n += 1


def squared(seq):
    """惰性映射:每来一个数 → 求平方"""
    for n in seq:
        yield n * n


def take(n, seq):
    """惰性取前 n 个"""
    for i, x in enumerate(seq):
        if i >= n:
            return
        yield x


# =========================================================================
# 第 5 节:异步生成器(PEP 525)—— 必须在 async def 中
# =========================================================================

import asyncio


async def aticker(interval, count):
    """异步生成器:yield 处的暂停 / 恢复由事件循环控制
    差异:__anext__ 是 awaitable,耗尽抛 StopAsyncIteration。"""
    for i in range(count):
        await asyncio.sleep(interval)
        yield i


async def run_async_demo():
    """消费异步生成器:async for"""
    print("\n=== 异步生成器(PEP 525) ===")
    async for tick in aticker(0.05, 3):
        print(f"  [aticker] tick = {tick}")
    print("  [aticker] 耗尽 → StopAsyncIteration(由 async for 透明处理)")


# =========================================================================
# 演示驱动
# =========================================================================

def main():
    print("=== 第 1 节:基础生成器 ===")
    g = simple_gen()
    print(f"  next(g) = {next(g)}")
    print(f"  next(g) = {next(g)}")
    try:
        next(g)
    except StopIteration as e:
        print(f"  next(g) → StopIteration(value={e.value!r})")

    print("\n=== 第 2 节:send / throw / close 三方法 ===")
    gen = echo(1)
    print(f"  next(gen)        = {next(gen)!r}    # 启动执行")
    print(f"  next(gen)        = {next(gen)!r}    # yield 表达式当前值 = None")
    print(f"  gen.send(2)      = {gen.send(2)!r}  # 把 2 注入 (yield value)")
    result = gen.throw(ValueError, "demo-error")  # Python 3.12+ 推荐写法
    print(f"  gen.throw(...)   = {result!r}        # 生成器内部捕获并把异常作为 value")
    gen.close()                                   # 触发 GeneratorExit,执行 finally

    print("\n=== 第 3 节:yield from 委派 ===")
    o = outer()
    print(f"  next(o)          = {next(o)!r}        # 委派到 inner 的 'A'")
    print(f"  o.send('hello')  = {o.send('hello')!r}  # send 自动转发给 inner")
    try:
        next(o)
    except StopIteration as e:
        print(f"  next(o)          → StopIteration(value={e.value!r})")

    print("\n=== 第 4 节:惰性数据流管道 ===")
    # 全部惰性,never materializes the infinite sequence in memory
    pipeline = take(5, squared(integers()))
    print(f"  list(pipeline)   = {list(pipeline)}")
    print("  # 1→1, 2→4, 3→9, 4→16, 5→25(无限流的连续 5 个,内存 O(1))")

    print("\n=== 同步部分演示完毕,接下来运行异步部分 ===")
    asyncio.run(run_async_demo())


if __name__ == "__main__":
    main()