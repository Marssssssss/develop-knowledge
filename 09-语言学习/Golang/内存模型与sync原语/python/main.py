#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 内存模型（happens-before / DRF-SC）与 sync 原语状态机的可执行模型。

`memory_model.py` 复刻 https://go.dev/ref/mem 的形式化定义（Requirement 1~3、
synchronized before、happens before、数据竞争、DRF-SC、可见写）。
`sync_mutex.py` 复刻 Go 1.24 `internal/sync/mutex.go` 的状态位与正常/饥饿双模式。

本文件只放场景与断言。所有断言实跑通过。
"""

from memory_model import *          # noqa: F401,F403
from sync_mutex import *            # noqa: F401,F403

STATS = {"n": 0, "fail": []}


def check(label, cond, detail=""):
    STATS["n"] += 1
    if not cond:
        STATS["fail"].append(label)
    print(("PASS  " if cond else "FAIL  ") + label
          + (("  | " + str(detail)) if detail else ""))


# ---------------------------------------------------------------- 文档场景
def hello_via_send_first():
    """文档 §Channel communication 第一个例子：保证打印 "hello, world"。"""
    e = Execution()
    go_stmt = e.op(0, "go f()", "go")
    w_a = e.op(1, "a = \"hello, world\"", "write", "a")
    snd = e.op(1, "c <- 0", "send", "c")
    rcv = e.op(0, "<-c", "recv", "c")
    rd = e.op(0, "print(a)", "read", "a")
    e.sync(go_stmt, w_a, "go 语句 synchronized before 新 goroutine 开始执行")
    e.sync(snd, rcv, "send synchronized before 对应 receive 完成")
    return e, w_a, rd


def recv_first(buffered):
    """先收后发：无缓冲时保证、缓冲时不保证。"""
    e = Execution()
    go_stmt = e.op(0, "go f()", "go")
    w_a = e.op(1, "a = \"hello, world\"", "write", "a")
    rcv = e.op(1, "<-c", "recv", "c")
    snd = e.op(0, "c <- 0", "send", "c")
    rd = e.op(0, "print(a)", "read", "a")
    e.sync(go_stmt, w_a, "go 语句 → goroutine 开始")
    if not buffered:
        e.sync(rcv, snd, "**unbuffered**：receive synchronized before 对应 send 完成")
    return e, w_a, rd


def goroutine_destruction():
    """文档 §Goroutine destruction：赋值后无同步事件 → 不保证被观察到。"""
    e = Execution()
    go_stmt = e.op(0, "go func(){...}()", "go")
    w_a = e.op(1, "a = \"hello\"", "write", "a")
    rd = e.op(0, "print(a)", "read", "a")
    e.sync(go_stmt, w_a, "go 语句 → goroutine 开始")
    return e, w_a, rd


def mutex_doc_example():
    """文档 §Locks：第 n 次 Unlock synchronized before 第 m 次 Lock 返回（n < m）。"""
    e = Execution()
    l1 = e.op(0, "l.Lock() 第 1 次", "lock", "l")
    go_stmt = e.op(0, "go f()", "go")
    w_a = e.op(1, "a = \"hello, world\"", "write", "a")
    un = e.op(1, "l.Unlock()", "unlock", "l")
    l2 = e.op(0, "l.Lock() 第 2 次", "lock", "l")
    rd = e.op(0, "print(a)", "read", "a")
    e.sync(l1, w_a, "第 1 次 Lock → go 语句 → f 里的写")
    e.sync(un, l2, "第 1 次 Unlock synchronized before 第 2 次 Lock 返回")
    return e, w_a, rd


def kth_recv_scenario(cap_c=2, k=1):
    """文档：容量 C 的 channel，第 k 次 receive synchronized before 第 k+C 次 send 完成。"""
    e = Execution()
    rcv = e.op(0, "第 %d 次 receive" % k, "recv", "c")
    sends = [e.op(1, "第 %d 次 send" % i, "send", "c") for i in range(1, 5)]
    e.sync(rcv, sends[k + cap_c - 1], "k=%d, C=%d → 第 k+C 次 send" % (k, cap_c))
    return e, rcv, sends


def once_example():
    """文档 §Once：f() 的完成 synchronized before 任何 once.Do(f) 的返回。"""
    e = Execution()
    w_a = e.op(1, "setup(): a = \"hello, world\"", "write", "a")
    done = e.op(1, "once.Do(setup) 返回", "unlock", "once")
    rd1 = e.op(0, "print(a) (doprint #1)", "read", "a")
    rd2 = e.op(0, "print(a) (doprint #2)", "read", "a")
    e.sync(w_a, done, "f() 完成 → once.Do 返回")
    e.sync(done, rd1, "once.Do 返回 → 读到 a")
    e.sync(done, rd2, "once.Do 返回 → 读到 a")
    return e, w_a, rd1


def main():
    print("=== A. happens-before 关系的基本性质 ===")
    e, w_a, rd = hello_via_send_first()
    hb, _ = e.happens_before()
    n = len(e.ops)
    check("A1 happens-before 是严格偏序（不自反）",
          all(not hb[i][i] for i in range(n)))
    check("A2 happens-before 传递闭包成立",
          all(not (hb[i][k] and hb[k][j]) or hb[i][j]
              for i in range(n) for k in range(n) for j in range(n)))
    check("A3 go 语句 happens before 新 goroutine 的首操作", e.ordered(e.ops[0], e.ops[1]))
    check("A4 send happens before 对应 receive",
          e.ordered(w_a, rd) and not e.races())
    check("A5 Requirement 3：可见写唯一", e.visible_writes(rd) == [w_a])

    print("\n=== B. 文档四个例子：哪些是保证的 ===")
    e, w_a, rd = hello_via_send_first()
    check("B1 缓冲 channel + 先发后收 → **保证**打印",
          e.ordered(w_a, rd) and not e.races())
    e, w_a, rd = recv_first(buffered=False)
    check("B2 无缓冲 channel + 先收后发 → **仍然保证**打印",
          e.ordered(w_a, rd) and not e.races())
    e, w_a, rd = recv_first(buffered=True)
    check("B3 缓冲 channel + 先收后发 → 无同步边，构成**数据竞争**（文档明说不保证）",
          e.races() and not e.ordered(w_a, rd), e.races())
    check("B4 竞争对正是 (write a, print(a))",
          e.races() == [(w_a.label, rd.label)], e.races())
    e, w_a, rd = goroutine_destruction()
    check("B5 goroutine 退出**不保证**同步于任何事件 → 该例是竞争",
          e.races() and not e.ordered(w_a, rd))
    e, w_a, rd = mutex_doc_example()
    check("B6 Mutex 例：第 n 次 Unlock happens before 第 m 次 Lock 返回 → **保证**打印",
          e.ordered(w_a, rd) and not e.races())

    print("\n=== C. 其余同步机制 ===")
    e, rcv, sends = kth_recv_scenario(cap_c=2, k=1)
    check("C1 容量 2：第 1 次 receive happens before 第 3 次 send 完成",
          e.ordered(rcv, sends[2]))
    check("C2 但第 1 次 receive **不**同步于第 2 次 send（需到 k+C）",
          not e.ordered(rcv, sends[1]))
    e, w_a, rd = once_example()
    check("C3 Once：setup 完成 happens before 两次 print(a)",
          e.ordered(w_a, rd) and len(e.visible_writes(rd)) == 1)
    e = Execution()
    q_init = e.op(0, "q.init()", "unlock", "init_q")
    p_init = e.op(0, "p.init()", "lock", "init_q")
    main_fn = e.op(0, "main.main()", "lock", "init_p")
    e.sync(q_init, p_init, "p 导入 q：q 的 init 完成 → p 的 init 开始")
    e.sync(p_init, main_fn, "所有 init 完成 → main.main 开始")
    check("C4 init 链 q→p→main 全序（无竞争）", not e.races() and e.ordered(q_init, main_fn))
    e = Execution()
    setf = e.op(0, "runtime.SetFinalizer(x, f)", "unlock", "fin")
    callf = e.op(1, "f(x) 终结器调用", "lock", "fin")
    e.sync(setf, callf, "SetFinalizer 调用 synchronized before 终结器调用")
    check("C5 Finalizer：SetFinalizer happens before 终结器执行", not e.races())
    e = Execution()
    st = e.op(0, "atomic.Store(x, 1)", "atomic_write", "x")
    ld = e.op(1, "atomic.Load(x)", "atomic_read", "x")
    e.sync(st, ld, "原子操作 A 被 B 观察到 → A synchronized before B")
    check("C6 原子操作由 SC 全序连接 → 不构成竞争", not e.races())
    check("C7 原子读的可见写唯一", e.visible_writes(ld) == [st])
    e = Execution()
    w = e.op(1, "临界区: a = 1", "write", "a")
    e.op(0, "l.TryLock() 失败（无同步效果）", "read", "trylock")
    r = e.op(0, "读 a", "read", "a")
    check("C8 **失败**的 TryLock 不建立同步关系 → 仍是竞争", e.races())

    print("\n=== D. Mutex 状态位与快路径 ===")
    check("D1 状态位布局 locked=1 / woken=2 / starving=4 / waiterShift=3",
          (LOCKED, WOKEN, STARVING, WAITER_SHIFT) == (1, 2, 4, 3))
    check("D2 饥饿阈值 = 1e6 ns（1ms）", STARVATION_THRESHOLD_NS == 10 ** 6)
    m = Mutex()
    check("D3 零值 Mutex 可直接 TryLock 成功", m.try_lock())
    check("D4 已加锁时 TryLock 失败", not m.try_lock())
    m = Mutex()
    m.state = LOCKED | STARVING
    check("D5 饥饿模式下 TryLock 失败（locked|starving 任一即失败）", not m.try_lock())
    check("D6 等待者计数 = state >> 3", ((3 << WAITER_SHIFT) >> WAITER_SHIFT) == 3)
    m = Mutex()
    try:
        m.unlock(0)
        check("D7 解锁未加锁的 Mutex → fatal", False, "未抛异常")
    except MutexFatal as ex:
        check("D7 解锁未加锁的 Mutex → fatal",
              str(ex) == "sync: unlock of unlocked mutex", str(ex))
    m = Mutex()
    m.state = LOCKED
    try:
        m.acquire_handoff(1, 0, Waiter(1))
        check("D8 状态不一致时 acquire_handoff 抛 fatal", False, "未抛异常")
    except MutexFatal as ex:
        check("D8 状态不一致时 acquire_handoff 抛 fatal",
              str(ex) == "sync: inconsistent mutex state", str(ex))

    print("\n=== E. 正常模式：插队（barge）与队首重排 ===")
    m = Mutex(max_spin=0)                     # 先关自旋，把状态机走干净
    ok1, b1 = m.lock(1, now=0)
    check("E1 快路径拿到锁且不阻塞", ok1 and not b1)
    w = {g: Waiter(g) for g in (2, 3)}
    ok2, b2 = m.lock(2, now=10, w=w[2])
    ok3, b3 = m.lock(3, now=20, w=w[3])
    check("E2 G2/G3 排队阻塞", (not ok2 and b2) and (not ok3 and b3))
    check("E3 队列 FIFO：[2, 3]", m.queue == [2, 3], m.queue)
    check("E4 等待者计数为 2", m.waiters == 2, m.waiters)
    woke = m.unlock(now=100)
    check("E5 正常模式 unlock 唤醒队首 G2", woke == 2, woke)
    check("E6 唤醒后 state 置了 WOKEN 位", bool(m.state & WOKEN))
    ok4, b4 = m.lock(4, now=101)
    check("E7 新到的 G4 抢在 G2 之前拿到锁（barge，正常模式允许）",
          ok4 and not b4 and m.barges == 1, m.barges)
    ok2b, b2b = m.lock(2, now=102, w=w[2])     # 被唤醒的 G2 输掉竞争
    check("E8 输掉竞争的 G2 被插回**队首**（LIFO 重排）",
          not ok2b and b2b and m.queue[0] == 2 and m.lifo_requeues == 1,
          (m.queue, m.lifo_requeues))
    check("E9 重排后 WOKEN 已被清掉（awoke 分支生效）", not (m.state & WOKEN))
    check("E10 全程未进入饥饿模式（等待均 < 1ms）", not m.starving)
    ms = Mutex(max_spin=2)
    ms.lock(1, now=0)
    a = Waiter(2)
    ms.lock(2, now=10, w=a)
    check("E11 自旋发生但此时没有别的等待者 → **不**置 WOKEN",
          ms.spin_events == 1 and not (ms.state & WOKEN))
    ms.lock(3, now=20, w=Waiter(3))
    check("E12 已有等待者时自旋才置 WOKEN（源码条件 waiterShift != 0）",
          ms.spin_events == 2 and bool(ms.state & WOKEN))

    print("\n=== F. 饥饿模式：移交与退出条件 ===")
    m = Mutex(max_spin=0)
    m.lock(1, now=0)
    c = Waiter(2)
    m.lock(2, now=10, w=c)
    check("F1 G2 刚入队时未置饥饿", not m.starving)
    c.starving = (2_000_000 - c.wait_start) > STARVATION_THRESHOLD_NS
    check("F2 等待超过 1ms 后等待者自身置 starving（本地标志）", c.starving)
    m.lock(2, now=2_000_000, w=c)
    check("F3 只有当前确实被锁住才把 Mutex 切到饥饿模式", m.starving)
    before = m.spin_events
    m.lock(2, now=2_000_100, w=c)
    check("F4 饥饿模式下**不再自旋**（所有权靠移交，抢也抢不到）",
          m.spin_events == before)
    woke = m.unlock(now=2_000_200)
    check("F5 饥饿模式 unlock **直接移交**所有权给队首",
          woke == 2 and m.handoffs == 1, (woke, m.handoffs))
    leaving = m.acquire_handoff(2, now=2_000_300, w=c)
    check("F6 通过移交拿到锁：LOCKED 已置、自身等待者计数已减、饥饿位清掉",
          bool(m.state & LOCKED) and m.waiters == 0 and not m.starving)
    check("F7 队里再无等待者 → 退出饥饿模式", leaving)
    m2 = Mutex(max_spin=0)
    m2.lock(1, now=0)
    x, y = Waiter(2), Waiter(3)
    m2.lock(2, now=10, w=x)
    m2.lock(3, now=20, w=y)
    m2.state |= STARVING
    x.starving = True
    m2.unlock(now=100)
    leaving2 = m2.acquire_handoff(2, now=110, w=x)
    check("F8 G2 仍在饥饿状态且队里还有 G3 → **不**退出饥饿模式",
          not leaving2 and m2.starving and m2.waiters == 1)

    print("\n=== G. 尾延迟：两种模式的对照 ===")
    mn = Mutex(max_spin=0)
    mn.lock(1, now=0)
    wn = {g: Waiter(g) for g in (2, 3, 4)}
    for g in (2, 3, 4):
        mn.lock(g, now=g, w=wn[g])
    check("G1 三个等待者按 FIFO 排队 [2,3,4]", mn.queue == [2, 3, 4], mn.queue)
    check("G2 正常模式 unlock 唤醒队首 G2", mn.unlock(now=1000) == 2)
    ok99, _ = mn.lock(99, now=1001)
    check("G3 新到者 G99 插队成功（barges=1）→ 长尾的来源",
          ok99 and mn.barges == 1, mn.barges)
    ok2, _ = mn.lock(2, now=1005, w=wn[2])
    check("G4 G2 被插队后重排回队首（lifo_requeues=1）",
          not ok2 and mn.queue[0] == 2 and mn.lifo_requeues == 1)
    mh = Mutex(max_spin=0)
    mh.lock(1, now=0)
    wh = {g: Waiter(g) for g in (2, 3, 4)}
    for g in (2, 3, 4):
        mh.lock(g, now=g, w=wh[g])
    mh.state |= STARVING
    check("G5 饥饿模式 unlock 直接移交队首 G2", mh.unlock(now=1000) == 2 and mh.handoffs == 1)
    ok99h, b99h = mh.lock(99, now=1001)
    check("G6 饥饿模式新到者**不能**插队，只能排到队尾（[2,3,4,99]）",
          not ok99h and b99h and mh.queue == [2, 3, 4, 99], mh.queue)
    leaving = mh.acquire_handoff(2, now=1010, w=wh[2])
    check("G7 移交完成后队列顺序不变（[3,4,99]）→ 饥饿模式严格 FIFO、无长尾",
          leaving and mh.queue == [3, 4, 99] and not mh.starving, mh.queue)

    print("\n" + "=" * 62)
    print("断言总数 %d；失败 %d %s" % (STATS["n"], len(STATS["fail"]), STATS["fail"] or "（全绿）"))
    print("=" * 62)
    return 1 if STATS["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
