#!/usr/bin/env python3
"""Kotlin 协程:上下文 / 调度器 / 结构化并发 —— 自检(实跑)。

依据 kotlinlang.org 官方《Coroutine context and dispatchers》与《Coroutines basics》
描述的语义,用 coroutine_runtime.py 的最小运行时逐条验证。

运行: python3 coroutine_check.py
"""

from __future__ import annotations

import sys

from coroutine_runtime import (Ctx, DISPATCHER, JOB, NAME, Sim, new_dispatchers, simulate_pool)

PASS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS
    if not cond:
        print(f"FAIL  {label}  {detail}")
        raise AssertionError(label)
    PASS += 1
    print(f"ok    {label}" + (f"  [{detail}]" if detail else ""))


def scenario_context_merge() -> None:
    d = new_dispatchers(4)
    base = Ctx({NAME: "root", DISPATCHER: d["default"], JOB: "rootJob"})
    child = base.plus(Ctx({DISPATCHER: d["io"]}))
    check("Context 是键值元素集合,合并时同键右侧覆盖", child[DISPATCHER] is d["io"], str(child))
    check("合并不会丢掉未覆盖的元素", child[NAME] == "root" and child[JOB] == "rootJob", repr(child))
    check("合并顺序无关的新键可叠加",
          base.plus(Ctx({NAME: "outer"})).plus(Ctx({JOB: "j2"}))[NAME] == "outer",
          repr(base.plus(Ctx({NAME: "outer"})).plus(Ctx({JOB: "j2"}))))


def scenario_dispatcher_inheritance() -> None:
    d, sim, observed = new_dispatchers(4), Sim(), []

    def child(name):
        def body(job):
            observed.append((name, str(job.dispatcher)))
            yield ("delay", 5)
        return body

    def parent_body(job):
        sim.launch("inherit", child("inherit"), parent=job)                        # 不传 → 继承
        sim.launch("override", child("override"), parent=job, dispatcher=d["io"])  # 显式覆盖
        yield ("delay", 5)

    sim.launch("parent", parent_body, dispatcher=d["default"])
    sim.run()
    check("子协程未传 dispatcher 时继承父的调度器",
          observed[0] == ("inherit", "Dispatchers.Default"), str(observed))
    check("子协程显式传 dispatcher 时覆盖继承",
          observed[1] == ("override", "Dispatchers.IO"), str(observed))


def scenario_default_pool_limit() -> None:
    d = new_dispatchers(4)
    check("Dispatchers.Default 的并行度上限 = CPU 核数", d["default"].parallelism == 4,
          f"parallelism={d['default'].parallelism}")
    makespan, peak = simulate_pool(d["default"].parallelism, [10] * 8)
    check("8 个 10ms 纯 CPU 任务在 Default(4) 上:完成时刻 20ms", makespan == 20, f"makespan={makespan}")
    check("Default(4) 上峰值并发恰为 4(不会超发线程)", peak == 4, f"peak={peak}")
    makespan_io, peak_io = simulate_pool(d["io"].parallelism, [10] * 8)
    check("同样 8 个任务放到 IO(64) 上:完成时刻 10ms", makespan_io == 10, f"makespan={makespan_io}")
    check("IO 上峰值并发为 8(并行度不构成瓶颈)", peak_io == 8, f"peak={peak_io}")
    check("阻塞任务会占满 Default 的核数,把小任务挤到后面",
          simulate_pool(d["default"].parallelism, [50, 50, 50, 50, 1])[0] == 51,
          "4 个阻塞任务占满 4 条线程,第 5 个小任务要等到 50ms 之后 → 51ms")


def scenario_unconfined_vs_confined() -> None:
    d, sim = new_dispatchers(4), Sim()

    def body(job):
        yield ("delay", 1)

    unconfined = sim.launch("unconfined", body, dispatcher=d["unconfined"])
    confined = sim.launch("confined", body, dispatcher=d["main"])
    sim.run()
    check("Unconfined:挂起点之前在调用者线程执行", unconfined.thread_log[0] == "main",
          str(unconfined.thread_log))
    check("Unconfined:挂起点之后在挂起函数所用线程恢复",
          unconfined.thread_log[1] == "kotlinx.coroutines.DefaultExecutor", str(unconfined.thread_log))
    check("confined 调度器:前后两段线程一致", confined.thread_log == ["Dispatchers.Main"] * 2,
          str(confined.thread_log))


def scenario_structured_concurrency() -> None:
    d, sim = new_dispatchers(4), Sim()

    def child_body(job):
        yield ("delay", 30)

    def parent_body(job):
        for i in range(3):
            sim.launch(f"c{i}", child_body, parent=job)
        yield ("delay", 1)

    parent = sim.launch("parent", parent_body, dispatcher=d["default"])
    sim.run()
    check("父协程体 1ms 就返回,但要等 3 个 30ms 的子协程 → 30ms 才 COMPLETED",
          parent.state == "COMPLETED" and parent.finished_at == 30,
          f"{parent.state}@{parent.finished_at}")
    check("三个子协程全部 COMPLETED", [c.state for c in parent.children] == ["COMPLETED"] * 3,
          str(parent.children))


def scenario_child_failure_cancels_scope() -> None:
    d, sim, refs = new_dispatchers(4), Sim(), {}

    def slow_body(job):
        yield ("delay", 50)

    def bad_body(job):
        yield ("delay", 5)
        raise RuntimeError("worker crashed")

    def parent_body(job):
        refs["slow"] = sim.launch("slow", slow_body, parent=job)
        refs["bad"] = sim.launch("bad", bad_body, parent=job)
        yield ("delay", 1000)

    parent = sim.launch("parent", parent_body, dispatcher=d["default"])
    sim.run()
    check("子协程抛异常 → 父协程被取消(不是正常完成)", parent.state == "CANCELLED",
          f"state={parent.state}")
    check("异常向上传播,父协程拿到同一个异常对象",
          isinstance(parent.exception, RuntimeError) and str(parent.exception) == "worker crashed",
          repr(parent.exception))
    check("兄弟协程被取消", refs["slow"].state == "CANCELLED", f"state={refs['slow'].state}")
    check("被取消的协程不会再被恢复执行(thread_log 只有 1 段)",
          len(refs["slow"].thread_log) == 1, str(refs["slow"].thread_log))
    check("父协程的 1000ms 定时器到期也不复活", parent.finished_at == 5, f"finished_at={parent.finished_at}")


def main() -> int:
    for fn in (scenario_context_merge, scenario_dispatcher_inheritance, scenario_default_pool_limit,
               scenario_unconfined_vs_confined, scenario_structured_concurrency,
               scenario_child_failure_cancels_scope):
        print(f"\n--- {fn.__name__} ---")
        fn()
    print(f"\nALL PASS: {PASS} assertions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
