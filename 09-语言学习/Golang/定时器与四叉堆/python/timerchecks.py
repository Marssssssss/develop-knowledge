"""Go 定时器四叉堆模型的自检：断言全部来自官方源码 src/runtime/time.go 的实读结论。

运行：python selfcheck_timers.py
"""

import random
import sys

from timermodel import (
    TIMER_HEAP_N, TIMER_HEAPED, TIMER_MODIFIED, TIMER_ZOMBIE, MAX_WHEN,
    Timer, Timers, TimerWhen, less, BadTimer,
)

PASS = [0]
FAIL = [0]


def ok(cond, label):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("FAIL: " + label)


def eq(got, want, label, tol=0.0):
    if tol:
        good = abs(got - want) <= tol
    else:
        good = got == want
    ok(good, "%s (got=%r want=%r)" % (label, got, want))


def raises(fn, label):
    try:
        fn()
    except BadTimer:
        PASS[0] += 1
        return
    except Exception as e:  # noqa
        FAIL[0] += 1
        print("FAIL: %s (raised %r)" % (label, e))
        return
    FAIL[0] += 1
    print("FAIL: %s (no exception)" % label)


def heap_ok(ts):
    for i in range(1, len(ts.heap)):
        p = (i - 1) // TIMER_HEAP_N
        if ts.heap[p].when > ts.heap[i].when:
            return False
    return True


def build(ts, whens):
    timers = []
    for i, w in enumerate(whens):
        t = Timer(w, name="t%d" % i)
        timers.append(t)
        ts.add_heap(t)
    return timers

# ---------------------------------------------------------------- E8 Stop 后 Reset 复活
def e8_modify_revives():
    ts = Timers()
    timers = build(ts, [10, 20, 30])
    t0 = timers[0]
    ts.stop(t0)
    eq(ts.zombies, 1, "E8 Stop 后 1 个 zombie")
    pending, wake = ts.modify(t0, 25)
    eq(pending, False, "E8 复活返回的 pending 取自旧 when（已为 0）")
    eq(ts.zombies, 0, "E8 复活后 zombie 计数归零")
    ok(t0.state & TIMER_ZOMBIE == 0, "E8 复活后清掉 Zombie 位")
    ok(t0.state & TIMER_MODIFIED != 0, "E8 复活后仍是 Modified")
    eq(ts.heap[0].when, 10, "E8 堆里的 when 快照仍是旧值 10（延后同步）")
    eq(wake, True, "E8 改到 25 不小于当前 min 之外→触发 wake 检查")
    # 官方：updateHeap 才把快照同步成 t.when
    ok(t0.state & TIMER_ZOMBIE == 0 and t0.state & TIMER_HEAPED != 0,
       "E8 复活后仍在堆里（Heaped 保留）")
    ts.update_heap(t0)
    eq(sorted(tw.when for tw in ts.heap), [20, 25, 30], "E8 快照同步为 25 后堆内容")
    eq(ts.heap[0].when, 20, "E8 改晚→只 siftDown，堆顶变成 20")
    eq(ts.heap[1].when, 25, "E8 被改晚的定时器下沉到下标 1")
    eq(ts.min_when_heap, 20, "E8 minWhenHeap=20")
    ok(t0.state & TIMER_MODIFIED == 0, "E8 updateHeap 清掉 Modified 位")


# ---------------------------------------------------------------- E9 改早靠 adjust 不靠 cleanHead
def e9_modify_earlier():
    ts = Timers()
    timers = build(ts, [100, 200, 300])
    t0 = timers[0]
    # 把堆顶改早到 50：cleanHead 里 updateHeap 只 siftDown(0)，快照同步后仍在堆顶
    ts.modify(t0, 50)
    ts.clean_head()
    eq(ts.heap[0].when, 50, "E9 改早后堆顶快照同步为 50")
    eq(ts.min_when_heap, 50, "E9 minWhenHeap 同步")
    # adjust 的早退语义：minWhenModified > now 时什么都不做
    ts2 = Timers()
    t = build(ts2, [100])[0]
    ts2.modify(t, 60)
    eq(ts2.min_when_modified, 60, "E9 modify 记录 minWhenModified=60")
    ts2.adjust(now=50, force=False)
    eq(ts2.heap[0].when, 100, "E9 now<minWhenModified 时 adjust 提前返回（堆未同步）")
    ts2.adjust(now=60, force=True)
    eq(ts2.heap[0].when, 60, "E9 force 后堆同步为 60")
    eq(ts2.min_when_modified, 0, "E9 adjust 后 minWhenModified 清零")


# ---------------------------------------------------------------- E10 ticker 重排公式
def e10_ticker():
    ts = Timers()
    t = Timer(10, period=5, name="tick")
    ts.add_heap(t)
    eq(ts.run(9), 10, "E10 now=9 未到，返回下次时刻 10")
    eq(len(ts.fired), 0, "E10 未到不触发")
    eq(ts.run(27), 0, "E10 now=27 触发（返回 0）")
    eq(len(ts.fired), 1, "E10 只触发一次")
    eq(ts.fired[0][2], 17, "E10 delay = now - when = 17")
    eq(t.when, 30, "E10 next = when + period*(1+delay/period) = 10+5*4 = 30")
    ok(t.when != 15, "E10 不是朴素 when+period=15（跳过 missed tick）")
    eq(ts.run(29), 30, "E10 30 之前不触发")
    eq(ts.run(30), 0, "E10 now=30 再次触发")
    eq(t.when, 35, "E10 未延误时 next = 30+5 = 35")
    eq(len(ts.fired), 2, "E10 累计两次触发")


# ---------------------------------------------------------------- E11 一次性定时器触发后成 zombie
def e11_oneshot_becomes_zombie():
    ts = Timers()
    timers = build(ts, [10, 20])
    t0 = timers[0]
    eq(ts.run(10), 0, "E11 一次性定时器到点触发")
    eq(t0.when, 0, "E11 触发后 when=0（next=0 表示不再排期）")
    eq(ts.zombie_peak, 1, "E11 触发瞬间曾置 Zombie（瞬时 +1）")
    eq(ts.zombies, 0, "E11 紧接着 updateHeap→deleteMin 又减回 0")
    ok(t0.state & TIMER_ZOMBIE == 0, "E11 摘除时清掉 Zombie 位")
    ok(t0.ts is None, "E11 该定时器已离开堆（t.ts=nil）")
    eq(len(ts.heap), 1, "E11 堆里只剩一个")
    eq(ts.heap[0].when, 20, "E11 剩下 20")


# ---------------------------------------------------------------- E12 wakeTime 取 modified 优先
def e12_wake_time():
    ts = Timers()
    timers = build(ts, [100, 200])
    t0 = timers[0]
    ts.modify(t0, 40)
    eq(ts.min_when_heap, 100, "E12 minWhenHeap 仍是旧快照 100")
    eq(ts.wake_time(), 40, "E12 wakeTime 取 min(minWhenModified, minWhenHeap)=40")
    ts.adjust(now=40, force=True)
    eq(ts.wake_time(), 40, "E12 adjust 后 wakeTime=40（已落回堆）")
    eq(ts.min_when_heap, 40, "E12 minWhenHeap 精确为 40")


# ---------------------------------------------------------------- E13 rand 决定同时刻次序
def e13_rand_order():
    a = Timer(5, rand=1, name="a")
    b = Timer(5, rand=2, name="b")
    eq(less(TimerWhen(a, 5), TimerWhen(b, 5)), True, "E13 同 when 时 rand 小的在前")
    eq(less(TimerWhen(b, 5), TimerWhen(a, 5)), False, "E13 反向为假")
    ts = Timers()
    ta = Timer(5, rand=7, name="a")
    tb = Timer(5, rand=3, name="b")
    ts.add_heap(ta)
    ts.add_heap(tb)
    eq(ts.heap[0].timer.name, "b", "E13 rand=3 排在 rand=7 之前")
    ts.run(5)
    ts.run(5)
    eq([f[0] for f in ts.fired][:2], ["b", "a"], "E13 触发顺序按 rand")


# ---------------------------------------------------------------- E14 坏数据
def e14_bad_timer():
    ts = Timers()
    ts.heap = [TimerWhen(Timer(0, name="z"), 0)]
    raises(lambda: ts.sift_up(0), "E14 siftUp 对 when<=0 触发 badTimer")
    # 反直觉：siftDown 的 when<=0 检查在「是叶子就提前返回」之后，
    # 单元素堆里 i=0 是叶子，根本走不到那个检查，不会报错。
    try:
        ts.sift_down(0)
        ok(True, "E14 siftDown 在叶子位提前返回，不检查 when<=0")
    except BadTimer:
        ok(False, "E14 siftDown 在叶子位提前返回，不检查 when<=0")
    ts3 = Timers()
    ts3.heap = [TimerWhen(Timer(0, name="h"), 0), TimerWhen(Timer(1, name="c"), 1)]
    raises(lambda: ts3.sift_down(0), "E14 非叶位上 when<=0 才真的 badTimer")
    ts2 = Timers()
    t = Timer(5)
    raises(lambda: ts2.modify(t, 0), "E14 modify(when<=0) 抛错")
    raises(lambda: ts2.modify(t, 5, -1), "E14 modify(period<0) 抛错")


def state_checks():
    for fn in (e8_modify_revives, e9_modify_earlier, e10_ticker,
               e11_oneshot_becomes_zombie, e12_wake_time, e13_rand_order,
               e14_bad_timer):
        fn()
