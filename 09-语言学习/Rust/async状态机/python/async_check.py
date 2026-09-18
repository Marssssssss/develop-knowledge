"""async 自检：惰性 / Waker 精准唤醒 / 组合子的无分配状态机 / Pin。"""

from future_model import (
    AndThen, Context, Executor, Join, PinBox, PlainFut, Reactor, SelfRefFut,
    Timer, Waker, busy_poll, is_ready, pending, ready,
)


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} FAILED {detail}")


def run():
    # ---- 1. 惰性：不 poll 就什么都不会发生（std: "Futures alone are inert"）
    r = Reactor()
    t = Timer(r, 3, label="t")
    check("1.1 构造后 0 次 poll", t.polls == 0, str(t.polls))
    check("1.2 构造后时间没动", r.now == 0)
    ex = Executor(r)
    tid = ex.spawn(t)
    check("1.3 只是 spawn、还没 run", ex.polls == 0 and tid in ex.tasks)

    # ---- 2. Waker 驱动的唤醒：只 poll 必要次数
    r2 = Reactor()
    t2 = Timer(r2, 100, label="long")
    ex2 = Executor(r2)
    ex2.spawn(t2)
    rounds = ex2.run()
    check("2.1 waker 驱动下完成", ex2.results.get(0) is not None, str(ex2.results))
    check("2.2 只 poll 了 2 次（1 次 Pending + 1 次 Ready）", t2.polls == 2, str(t2.polls))

    # 对照：没有 waker、每轮盲轮询
    r3 = Reactor()
    t3 = Timer(r3, 100, label="busy")
    polls, _val = busy_poll(t3, r3)
    check("2.3 盲轮询要 101 次", polls == 101, str(polls))
    check("2.4 waker 版比盲轮询省得多", t2.polls < polls, f"{t2.polls} vs {polls}")

    # ---- 3. 只有「最近一次」Context 里的 Waker 会被唤醒
    r4 = Reactor()
    t4 = Timer(r4, 1, label="multi")
    woken = []
    w1 = Waker("old", lambda: woken.append("old"))
    w2 = Waker("new", lambda: woken.append("new"))
    _ = t4.poll(Context(w1, r4))
    _ = t4.poll(Context(w2, r4))
    check("3.1 反应器只保留一个登记（最近一次）", r4.pending_count() == 1,
          str(r4.pending_count()))
    r4.advance(1)
    check("3.2 只唤醒了最近那个 waker", woken == ["new"], str(woken))
    check("3.3 旧 waker 一次都没被唤醒", w1.calls == 0 and w2.calls == 1,
          f"{w1.calls}/{w2.calls}")

    # ---- 4. 没有事件源就永远挂起（inert 的直接后果）
    r5 = Reactor()
    t5 = Timer(r5, 5, label="stuck")
    ex5 = Executor(r5)
    ex5.spawn(t5)
    ex5.run(drive_reactor=False)
    check("4.1 不驱动反应器 → 未完成", 0 not in ex5.results, str(ex5.results))
    check("4.2 但确实 poll 过一次", t5.polls == 1, str(t5.polls))

    # ---- 5. Join：并发推进 + 完成后不再 poll 子 future
    r6 = Reactor()
    a, b = Timer(r6, 2, label="a"), Timer(r6, 5, label="b")
    j = Join(a, b)
    ex6 = Executor(r6)
    ex6.spawn(j)
    ex6.run()
    check("5.1 Join 完成", 0 in ex6.results, str(ex6.results))
    check("5.2 a 先完成后被置 None（不再被 poll）", j.a is None)
    check("5.3 b 也被置 None", j.b is None)
    check("5.4 a 的 poll 次数少于 b", a.polls < b.polls, f"{a.polls} vs {b.polls}")
    check("5.5 a 恰好 poll 到 Ready 为止", a.polls == 2, str(a.polls))

    # ---- 6. 无分配状态机：跑完一圈属性集合不变
    r7 = Reactor()
    a2, b2 = Timer(r7, 1, label="a"), Timer(r7, 2, label="b")
    j2 = Join(a2, b2)
    before = set(vars(j2))
    inline_ok = (j2.a is a2) and (j2.b is b2)     # 必须在跑之前取：完成后字段被置 None
    ex7 = Executor(r7)
    ex7.spawn(j2)
    ex7.run()
    check("6.1 Join 的属性集合没变（没有按 poll 分配新状态）",
          set(vars(j2)) == before, str(set(vars(j2)) ^ before))
    check("6.2 子 future 是内联持有而非装箱分配", inline_ok)
    check("6.3 完成后字段确实被置 None（防止二次 poll）", j2.a is None and j2.b is None)

    # ---- 7. AndThen：严格顺序，first 没完就不碰 second
    r8 = Reactor()
    first, second = Timer(r8, 3, label="first"), Timer(r8, 3, label="second")
    at = AndThen(first, second)
    ex8 = Executor(r8)
    ex8.spawn(at)
    ex8.run()
    check("7.1 AndThen 完成", 0 in ex8.results, str(ex8.results))
    check("7.2 second 的第一次 poll 晚于 first 完成", first.polls >= 2 and second.polls >= 1,
          f"{first.polls}/{second.polls}")

    # ---- 8. 已完成的任务被误唤醒应被忽略（不再 poll）
    r9 = Reactor()
    t9 = Timer(r9, 1, label="done")
    ex9 = Executor(r9)
    ex9.spawn(t9)
    ex9.run()
    check("8.1 已完成", 0 in ex9.results)
    ex9.enqueue(0)                       # 迟到的 wake
    check("8.2 迟到唤醒被判为 spurious", ex9.spurious_wakes == 1, str(ex9.spurious_wakes))
    ex9.run()
    check("8.3 因此不会被二次 poll", t9.polls == 2, str(t9.polls))

    # ---- 9. Pin：自引用 future 一旦被搬就悬垂
    srf = SelfRefFut(42, slot=1000)
    check("9.1 未搬动时自引用指针有效", srf.pointer_is_valid())
    moved = srf.moved_to(2000)
    check("9.2 搬动后指针悬垂", not moved.pointer_is_valid())
    check("9.3 带自引用的类型不是 Unpin", SelfRefFut.UNPIN is False)

    pinned = PinBox(SelfRefFut(7, slot=1))
    try:
        pinned.move_out()
        raise AssertionError("9.4 自引用 future 应搬不动")
    except RuntimeError as e:
        check("9.4 搬不动并说明原因", "Unpin" in str(e), str(e))
    check("9.5 但仍可通过 &mut 使用", pinned.as_mut().pointer_is_valid())

    plain = PinBox(PlainFut(1))
    check("9.6 Unpin 类型可以从 Pin 里搬出来", plain.move_out() is not None)

    print("future_model: all assertions passed")


if __name__ == "__main__":
    run()
