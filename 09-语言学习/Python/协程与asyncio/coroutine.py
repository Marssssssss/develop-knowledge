"""
Python 协程与 asyncio 核心机制

涵盖:
- 第 1 节:async def + await 基础 + 常见陷阱(单纯调用不会执行)
- 第 2 节:Task / Future / awaitable 三种类型
- 第 3 节:asyncio.run / asyncio.create_task / asyncio.gather
- 第 4 节:asyncio.TaskGroup(3.11+) 结构化并发
- 第 5 节:asyncio.wait / as_completed 语义差异
- 第 6 节:超时控制(asyncio.timeout / wait_for)
- 第 7 节:asyncio.to_thread 包装阻塞函数

运行:
    python3 coroutine.py
"""

from __future__ import annotations

import asyncio
import time


# =========================================================================
# 第 1 节:async def + await
# =========================================================================

async def say_after(delay, what):
    await asyncio.sleep(delay)
    print(f"  [{time.strftime('%X')}] {what}")


async def call_coro_without_running():
    """演示:单纯调用协程不会执行"""
    coro = say_after(0.1, "world")
    print(f"  type(coro) = {type(coro).__name__}")    # coroutine 对象
    print(f"  coro       = {coro!r}")
    print("  ⚠ 单纯调用 main() 不执行任何东西,需要 await 或 create_task")
    coro.close()                                     # 不跑也得手动 close


# =========================================================================
# 第 2 节:三种 awaitable
# =========================================================================

async def demo_awaitables():
    """coroutine / Task / Future 三种 awaitable 的演示"""
    # 1. coroutine —— await 异步函数返回值
    async def native_coro():
        return 42
    v1 = await native_coro()
    print(f"  await native_coro()  = {v1}")

    # 2. Task —— asyncio.create_task() 包装即自动调度
    task = asyncio.create_task(say_after(0.05, "via-Task"))
    v2 = await task                                  # 拿到 task 的结果(None)
    print(f"  await task           = {v2!r}  # Task 也是 awaitable")

    # 3. Future —— 低层 awaitable,极少见应用代码创建
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    fut.set_result("via-Future")
    v3 = await fut
    print(f"  await future         = {v3!r}")


# =========================================================================
# 第 3 节:gather 并发运行多个协程
# =========================================================================

async def factorial(name, number):
    """官方文档示例:模拟耗时 IO"""
    f = 1
    for i in range(2, number + 1):
        if number >= 4:
            print(f"  Task {name}: Compute factorial({number}), i={i}...")
        await asyncio.sleep(0.05)
        f *= i
    print(f"  Task {name}: factorial({number}) = {f}")
    return f


async def demo_gather():
    """gather:并发执行,按入参顺序收集结果"""
    t0 = time.perf_counter()
    results = await asyncio.gather(
        factorial("A", 2),
        factorial("B", 3),
        factorial("C", 4),
    )
    dt = time.perf_counter() - t0
    print(f"  results = {results}    总耗时 ≈ {dt*1000:.0f} ms  (并发而非 250ms 串行)")


# =========================================================================
# 第 4 节:TaskGroup 结构化并发(3.11+)
# =========================================================================

async def demo_taskgroup():
    """TaskGroup 是 3.11+ 推荐的并发原语,比 gather 更安全"""
    print(f"  TaskGroup 比 gather 更安全:任一失败 → 取消剩余 → ExceptionGroup")
    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(factorial("T-A", 3))
            tg.create_task(factorial("T-B", 3))
            tg.create_task(asyncio.sleep(0.01))      # 立刻结束,无事发生
        print(f"  TaskGroup 退出 → 所有 task 完成")
    except* ValueError as eg:
        # except* 语法(3.11+)按类型解包 ExceptionGroup
        print(f"  caught {len(eg.exceptions)} ValueError(s)")


# =========================================================================
# 第 5 节:asyncio.wait / as_completed 语义差异
# =========================================================================

async def slow_task(delay, label):
    await asyncio.sleep(delay)
    return label


async def demo_wait_as_completed():
    """asyncio.wait:返回 (done, pending) 集合;
       as_completed:按完成顺序产出结果"""
    tasks = [asyncio.create_task(slow_task(d, f"task-{i}({d}s)"))
             for i, d in enumerate([0.10, 0.05, 0.07])]

    # 1. asyncio.wait —— 阻塞到全部完成
    done, pending = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)
    print(f"  asyncio.wait → done={len(done)}, pending={len(pending)}")

    # 2. as_completed —— 异步迭代,按完成顺序
    tasks2 = [asyncio.create_task(slow_task(d, f"task-{i}({d}s)"))
              for i, d in enumerate([0.10, 0.05, 0.07])]
    results = []
    async for earliest in asyncio.as_completed(tasks2):
        result = await earliest
        results.append(result)
    print(f"  as_completed 完成顺序 = {results}    # 短 delay 的先完成")


# =========================================================================
# 第 6 节:超时控制
# =========================================================================

async def eternity():
    await asyncio.sleep(10)
    print("  yep, eternity done")                     # 不会到这里


async def demo_timeout():
    """两种超时 API:asyncio.timeout(3.11+) 与 asyncio.wait_for"""
    print("  ---- asyncio.timeout() 上下文管理器 ----")
    try:
        async with asyncio.timeout(0.1):
            await eternity()
    except TimeoutError:
        print(f"  ✓ TimeoutError(3.11+ 改为这个,旧版 asyncio.TimeoutError 已合并)")

    print("  ---- asyncio.wait_for() 函数式 ----")
    try:
        await asyncio.wait_for(eternity(), timeout=0.1)
    except TimeoutError:
        print(f"  ✓ wait_for 也抛 TimeoutError(3.11+ 行为统一)")


# =========================================================================
# 第 7 节:asyncio.to_thread 包装阻塞函数
# =========================================================================

def blocking_io():
    """阻塞 IO:直接 await 它会阻塞整个事件循环"""
    time.sleep(0.1)
    return "blocking-result"


async def demo_to_thread():
    """to_thread 把阻塞函数扔到线程池,事件循环继续跑"""
    t0 = time.perf_counter()
    results = await asyncio.gather(
        asyncio.to_thread(blocking_io),
        asyncio.sleep(0.05),
    )
    dt = time.perf_counter() - t0
    print(f"  results = {results}    总耗时 ≈ {dt*1000:.0f} ms  (≈ 100ms,而非 150ms)")


# =========================================================================
# 演示驱动
# =========================================================================

async def main():
    print("=== 第 1 节:async def + await 基础 ===")
    t0 = time.perf_counter()
    await say_after(0.05, "hello")
    await say_after(0.05, "world")
    print(f"  → 串行两个 50ms 任务 = {(time.perf_counter()-t0)*1000:.0f} ms")
    await call_coro_without_running()

    print("\n=== 第 2 节:三种 awaitable 类型 ===")
    await demo_awaitables()

    print("\n=== 第 3 节:asyncio.gather 并发收集 ===")
    await demo_gather()

    print("\n=== 第 4 节:TaskGroup 结构化并发(3.11+) ===")
    await demo_taskgroup()

    print("\n=== 第 5 节:wait vs as_completed 语义差异 ===")
    await demo_wait_as_completed()

    print("\n=== 第 6 节:超时控制 ===")
    await demo_timeout()

    print("\n=== 第 7 节:asyncio.to_thread 包装阻塞函数 ===")
    await demo_to_thread()


if __name__ == "__main__":
    asyncio.run(main())