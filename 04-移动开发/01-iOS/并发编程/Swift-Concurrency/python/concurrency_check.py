#!/usr/bin/env python3
r"""Swift 并发自检 —— 把 async/await / actor / 结构化并发 / 协作式取消变成可断言的数字。

运行: python3 concurrency_check.py(内核见 loop_core.py)
"""

from __future__ import annotations

from loop_core import (ALL_CHILDREN, Actor, Deadlock, Loop, SerialQueue,
                       async_io, finished_at, spawn_child, spawn_root)

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    print(("  [PASS] " if ok else "  [FAIL] ") + label + (f"   {detail}" if detail else ""))


# [1] await 是串行点,async let 才是并行
def t01_sequential_vs_parallel() -> None:
    print("[1] `await a(); await b()` 串行 vs `async let` 并行")
    jobs = (("A", 30.0), ("B", 50.0), ("C", 20.0))

    def run(parallel: bool) -> tuple[float, list]:
        loop, log = Loop(), []

        def body(me):
            for n, ms in jobs:
                c = spawn_child(loop, me, n, async_io(n, ms, log))
                if not parallel:
                    yield ("await", c)          # 串行 await:一条在飞,其余还没起
            yield ("await", ALL_CHILDREN)       # async let:先全部起飞,再一起等
            return loop.now

        spawn_root(loop, "main", body)
        loop.run_until_idle()
        return loop.now, log

    s, slog = run(False)
    p, plog = run(True)
    check("串行 await:耗时 = 30 + 50 + 20 = 100ms", abs(s - 100.0) < 1e-9, f"{s:g}ms")
    check("async let:耗时 = max(30, 50, 20) = 50ms", abs(p - 50.0) < 1e-9, f"{p:g}ms")
    check("并行把总耗时压到 1/2(100 → 50)", abs(s / p - 2.0) < 1e-9, f"{s / p:.2f}x")
    check("串行下 B 在 A 之后才启动(严格前后关系)",
          slog.index("B 开始") > slog.index("A 结束"), " ".join(slog))
    check("并行下 B 在 A 结束前就已启动(时间上真的重叠了)",
          plog.index("B 开始") < plog.index("A 结束"), " ".join(plog))


# [2] actor 保护的是"同步段",所以不变量安全
def t02_actor_protects_sync_segment() -> None:
    print("[2] actor 串行化:不变量在同步段内完成,无丢失更新")
    loop, bank, ok = Loop(), Actor("Bank"), {"balance": 0, "seen": []}

    def good_deposit(n: int, ms: float):
        def body(_me):
            ok["balance"] += n                  # 读-改-写:同一个同步段内完成
            ok["seen"].append(ok["balance"])
            yield ("sleep", ms)                 # 挂起点:actor 在此刻对其它 job 开放
            return ok["balance"]
        return body

    for i in range(5):
        bank.send(loop, f"deposit{i}", good_deposit(10, 10.0))
    loop.run_until_idle()
    check("5 次 +10 后余额 = 50(没有丢失更新)", ok["balance"] == 50, f"{ok['balance']}")
    check("观察序列严格递增 → 每个同步段都是原子的,没有被别的 job 插入",
          ok["seen"] == [10, 20, 30, 40, 50], f"{ok['seen']}")
    check("actor 上执行了 10 个同步段 = 5 个 job × 2(挂起前一段 + 挂起后一段)",
          bank.segments == 10 and loop.suspensions == 5,
          f"segments={bank.segments} 挂起点={loop.suspensions}")

    # 对照:把"读-改-写"拆到挂起点两侧 → 5 次更新全部丢失,只剩 1 次
    loop2, broken = Loop(), {"balance": 0}

    def bad_deposit(n: int, ms: float):
        def body(_me):
            tmp = broken["balance"]             # 读
            yield ("sleep", ms)                 # 挂起 → 别人也都读到了同一个旧值
            broken["balance"] = tmp + n         # 写回"旧值 + n"
        return body

    for i in range(5):
        spawn_root(loop2, f"bad{i}", bad_deposit(10, 10.0))
    loop2.run_until_idle()
    check("把读-改-写拆到 await 两侧:5 次 +10 只剩 10(经典丢失更新)",
          broken["balance"] == 10, f"{broken['balance']}")


# [3] 可重入:保护范围只有同步段,await 之后前提可能已被破坏
def t03_reentrancy_stale_check() -> None:
    print("[3] 可重入导致 await 之后的前提可能失效")
    loop, st = Loop(), {"balance": 100, "results": []}

    def withdraw(amount: int):
        def body(_me):
            if st["balance"] >= amount:         # 检查(check)
                yield ("sleep", 10.0)           # await → 这里别的 job 可以插进来
                st["balance"] -= amount         # 使用(use):前提已被破坏
                st["results"].append(("ok", amount))
            else:
                st["results"].append(("rejected", amount))
        return body

    for i in (1, 2):
        spawn_root(loop, f"w{i}", withdraw(80))
    loop.run_until_idle()
    check("天真写法:两笔 80 都判为可提,余额被扣成 -60(不变量被破坏)",
          st["balance"] == -60 and [r[0] for r in st["results"]] == ["ok", "ok"],
          f"balance={st['balance']}")

    loop2, st2 = Loop(), {"balance": 100, "results": []}

    def careful(amount: int):
        def body(_me):
            if st2["balance"] >= amount:
                yield ("sleep", 10.0)
                if st2["balance"] >= amount:    # 重新检查放在同步段里 → 原子
                    st2["balance"] -= amount
                    st2["results"].append(("ok", amount))
                    return
            st2["results"].append(("rejected", amount))
        return body

    for i in (1, 2):
        spawn_root(loop2, f"w{i}", careful(80))
    loop2.run_until_idle()
    check("把复检放回同步段:第二笔被拒,余额 = 20",
          st2["balance"] == 20 and st2["results"] == [("ok", 80), ("rejected", 80)],
          f"balance={st2['balance']} {st2['results']}")


# [4] 可重入 vs 不可重入:A、B 互相 await
def t04_reentrancy_avoids_deadlock() -> None:
    print("[4] 可重入的收益:两个 actor 互相 await 不会死锁")
    log: list = []

    def build(reentrant: bool):
        loop = Loop()
        a, b = Actor("A", reentrant), Actor("B", reentrant)

        def ping(_me):
            log.append("A.ping 同步段")
            yield ("await", b.send(loop, "pong", pong))
            log.append("A.ping 拿到结果")
            return "ping-done"

        def pong(_me):
            log.append("B.pong 同步段")
            yield ("await", a.send(loop, "note", note))
            log.append("B.pong 拿到结果")
            return "pong-done"

        def note(_me):
            log.append("A.note 同步段")
            yield ("sleep", 5.0)
            return "note-done"

        root = a.send(loop, "ping", ping)
        try:
            loop.run_until_idle()
        except Deadlock as e:
            return root, f"死锁:{e}"
        return root, root.result

    root, res = build(True)
    check("可重入:ping → pong → note 全部完成", res == "ping-done", f"{res!r}")
    check("note 确实在 A 还停留在 ping 的中途被跑掉了(挂起点释放了 actor)",
          log.index("A.note 同步段") < log.index("A.ping 拿到结果"), " → ".join(log))

    root2, res2 = build(False)
    check("不可重入:同一个调用链卡死(A 占着自己不放,note 进不去)",
          isinstance(res2, str) and res2.startswith("死锁") and not root2.done, f"{res2}")


# [5] 协作式取消 + 级联 + "不检查就停不下来"
def t05_cooperative_cancellation() -> None:
    print("[5] 协作式取消:级联传播 + 不检查就不停")
    loop, log, st = Loop(), [], {"processed": 0}

    def worker(i: int):
        def body(me):
            for k in range(3):
                me.check_cancellation()         # 对应 Task.checkCancellation()
                yield ("sleep", 10.0)
                st["processed"] += 1
                log.append(f"w{i} 完成第 {k + 1} 项")
            return "all-done"
        return body

    def group_body(me):
        for i in range(3):
            spawn_child(loop, me, f"w{i}", worker(i))
        yield ("await", ALL_CHILDREN)
        return "group-done"

    parent = spawn_root(loop, "group", group_body)
    for _ in range(3):                          # 只推进三小步 → 三个 worker 各走一个同步段
        loop._run_one(loop.ready.pop(0))
    parent.cancel()                             # 级联取消 3 个子任务
    loop.run_until_idle()
    check("取消父任务级联到全部子任务:3 个 worker 全部以 CancellationError 结束",
          all(c.done and c.result.startswith("已取消") for c in parent.children),
          f"{[c.result for c in parent.children]}")
    check("没有任何 worker 跑满 3 项(9 项里只完成 2 项,每个 worker 至多 1 项)",
          st["processed"] == 2 and len(log) == 2, f"processed={st['processed']}/9 {log}")
    check("父任务自己没检查取消 → 等完子任务后照样正常返回(框架不会替你中断)",
          parent.done and parent.is_cancelled and parent.result == "group-done",
          f"is_cancelled={parent.is_cancelled} result={parent.result!r}")

    # 不检查取消的任务:取消标志置了也没用(协作式的本质)
    loop2, stubborn = Loop(), {"runs": 0}

    def stubborn_body(_me):
        for _ in range(3):                      # 从不调用 check_cancellation()
            yield ("sleep", 10.0)
            stubborn["runs"] += 1

    t = spawn_root(loop2, "stubborn", stubborn_body)
    t.cancel()
    loop2.run_until_idle()
    check("不检查取消标志的任务照样跑完 3 轮(取消不是强杀)",
          stubborn["runs"] == 3, f"runs={stubborn['runs']}")
    check("取消标志一直为 True 但任务毫无察觉,正常返回 → 只有显式检查才生效",
          t.is_cancelled and t.done and t.result is None,
          f"cancelled={t.is_cancelled} result={t.result!r}")


# [6] 结构化并发:父任务不会忘记等子任务;子任务抬高父任务优先级
def t06_structured_concurrency() -> None:
    print("[6] 结构化并发:必须等完子任务 + 优先级提升")
    loop, log, seen = Loop(), [], {}

    def parent_body(me):
        for name, ms in (("slow", 40.0), ("fast", 10.0)):
            spawn_child(loop, me, name, async_io(name, ms, log), priority=9)
        seen["prio"] = me.priority              # 子任务挂上父任务时,优先级立刻被抬高
        yield ("await", ALL_CHILDREN)
        return "group-done"

    root = spawn_root(loop, "group", parent_body, priority=1)
    loop.run_until_idle()
    check("父任务优先级被抬到子任务最高优先级(1 → 9)",
          seen["prio"] == 9 and root.priority == 9, f"priority={root.priority}")
    check("父任务不会在子任务之前结束(structured concurrency 的核心保证)",
          finished_at(loop, "group") >= max(finished_at(loop, "group>slow"),
                                            finished_at(loop, "group>fast")),
          f"parent={finished_at(loop, 'group')} slow={finished_at(loop, 'group>slow')} "
          f"fast={finished_at(loop, 'group>fast')}")
    check("父任务在最慢的子任务处收工(40ms),而不是自己先跑完",
          finished_at(loop, "group") == 40.0 and root.result == "group-done",
          f"{root.result!r} @ {finished_at(loop, 'group')}ms")
    check("两个子任务真的并发跑了(10ms 的那个先结束,而不是等 40ms 的那个)",
          log.index("fast 结束") < log.index("slow 结束"), " ".join(log))


# [7] actor 不是 FIFO 队列;串行队列 sync 回自己会死锁
def t07_no_fifo_and_serial_queue() -> None:
    print("[7] actor 不保证 FIFO;串行队列 sync 回自己 = 死锁")
    loop, done = Loop(), []

    def job(name: str):
        def body(_me):
            yield ("sleep", 10.0)
            done.append(name)
        return body

    spawn_root(loop, "低优先级(先到)", job("低优先级"), priority=1)
    spawn_root(loop, "高优先级(后到)", job("高优先级"), priority=9)
    loop.run_until_idle()
    check("后到的高优先级任务先完成 → actor 上的 job 不保证按到达顺序收尾",
          done == ["高优先级", "低优先级"], f"{done}")

    sq = SerialQueue("com.example.serial")
    sq.async_("block1", lambda _q: None).async_("block2", lambda _q: None)
    sq.drain()
    check("对照:串行队列严格 FIFO",
          sq.log == ["block1 开始", "block1 结束", "block2 开始", "block2 结束"], f"{sq.log}")

    sq2 = SerialQueue("com.example.serial2")
    sq2.async_("outer", lambda q: q.sync_(f"{q.name} 内部", lambda _q: None))
    try:
        sq2.drain()
        check("串行队列内部 sync 回同一个队列 → 死锁", False, "居然没死锁")
    except Deadlock as e:
        check("串行队列内部 sync 回同一个队列 → 死锁(队列在等自己,actor 的可重入正是为解此)", True, str(e))


if __name__ == "__main__":
    for fn in (t01_sequential_vs_parallel, t02_actor_protects_sync_segment,
               t03_reentrancy_stale_check, t04_reentrancy_avoids_deadlock,
               t05_cooperative_cancellation, t06_structured_concurrency,
               t07_no_fifo_and_serial_queue):
        fn()
    print(f"\n断言 {len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项:", FAIL)
    raise SystemExit(1 if FAIL else 0)
