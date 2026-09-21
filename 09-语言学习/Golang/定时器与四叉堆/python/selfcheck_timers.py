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


# ---------------------------------------------------------------- E1 拓扑
def e1_topology():
    eq(TIMER_HEAP_N, 4, "E1 timerHeapN=4")
    eq(MAX_WHEN, (1 << 63) - 1, "E1 maxWhen")
    bad = [i for i in range(1, 600)
           if not (i in [TIMER_HEAP_N * ((i - 1) // TIMER_HEAP_N) + k
                         for k in range(1, TIMER_HEAP_N + 1)])]
    eq(bad, [], "E1 每个 i>0 都落在 parent(i) 的 4 个孩子里")
    # initHeap 起点 (n-2)/4 是最后一个非叶节点
    bad2 = []
    for n in range(2, 400):
        i0 = (n - 2) // TIMER_HEAP_N
        if not (TIMER_HEAP_N * i0 + 1 < n and TIMER_HEAP_N * (i0 + 1) + 1 >= n):
            bad2.append(n)
    eq(bad2, [], "E1 (n-2)/4 恰是最后一个有孩子的下标")
    # 4 层四叉堆能装 85 个节点
    eq((4 ** 4 - 1) // 3, 85, "E1 深度 0..3 共 85 个节点")
    ts = Timers()
    build(ts, list(range(85, 0, -1)))
    eq(len(ts.heap), 85, "E1 85 个定时器入堆")
    depths = set()
    for i in range(1, 85):
        d, j = 0, i
        while j > 0:
            j = (j - 1) // TIMER_HEAP_N
            d += 1
        depths.add(d)
    eq(max(depths), 3, "E1 85 个节点的最大深度=3（4 层）")


# ---------------------------------------------------------------- E2 siftDown 提前返回
def e2_siftdown_early_return():
    # n=5 时 i=1 是叶子（孩子 5..8 都不存在）
    ts = Timers()
    build(ts, [5, 9, 8, 7, 6])
    before = [tw.when for tw in ts.heap]
    ts.siftdown_steps = 0
    ts.sift_down(1)
    eq([tw.when for tw in ts.heap], before, "E2 叶子节点 siftDown 是 no-op")
    eq(ts.siftdown_steps, 0, "E2 叶子 siftDown 循环 0 次")
    # 反证：i=0 是内部节点，堆顶 9 大于孩子(7/6/...) → 会下沉
    ts2 = Timers()
    ts2.heap = [TimerWhen(Timer(w, name="x%d" % w), w) for w in (9, 7, 8, 6, 5)]
    ts2.siftdown_steps = 0
    ts2.sift_down(0)
    ok(ts2.siftdown_steps > 0, "E2 非叶节点 siftDown 真的动了")
    eq(ts2.heap[0].when, 5, "E2 下沉后堆顶是最小值 5")
    ok(heap_ok(ts2), "E2 下沉后堆序成立")


# ---------------------------------------------------------------- E3 堆序不变式
def e3_heap_invariant():
    rnd = random.Random(20260921)
    for trial in range(5):
        ts = Timers()
        whens = [rnd.randint(1, 10 ** 6) for _ in range(120)]
        build(ts, whens)
        ok(heap_ok(ts), "E3 随机插入后堆序成立 trial=%d" % trial)
        eq(ts.heap[0].when, min(whens), "E3 堆顶=全局最小 trial=%d" % trial)
        eq(ts.min_when_heap, min(whens), "E3 minWhenHeap 同步 trial=%d" % trial)


# ---------------------------------------------------------------- E4 deleteMin 弹出升序
def e4_delete_min():
    rnd = random.Random(7)
    whens = [rnd.randint(1, 5000) for _ in range(200)]
    ts = Timers()
    timers = build(ts, whens)
    popped = []
    while len(ts.heap) > 0:
        popped.append(ts.heap[0].when)
        ts.delete_min()
    eq(popped, sorted(whens), "E4 deleteMin 弹出序列=升序")
    eq(ts.min_when_heap, 0, "E4 空堆 minWhenHeap=0")
    ok(all(t.ts is None for t in timers), "E4 弹出后 t.ts 全部清空")


# ---------------------------------------------------------------- E5 四叉 vs 二叉
def e5_fourary_vs_binary():
    def bin_sift_up(heap, i):
        steps = 0
        x = heap[i]
        while i > 0:
            p = (i - 1) // 2
            if heap[p] <= x:
                break
            heap[i] = heap[p]
            i = p
            steps += 1
        heap[i] = x
        return steps

    rnd = random.Random(99)
    data = [rnd.randint(1, 10 ** 6) for _ in range(500)]
    ts = Timers()
    ts.siftup_steps = 0
    for w in data:
        t = Timer(w)
        ts.add_heap(t)
    four = ts.siftup_steps
    bh, two = [], 0
    for w in data:
        bh.append(w)
        two += bin_sift_up(bh, len(bh) - 1)
    ok(four < two, "E5 同数据下四叉堆上浮次数少于二叉堆 (%d < %d)" % (four, two))
    # 递减插入（最坏情况）对比：四叉堆深度只有二叉堆的一半
    ts2 = Timers()
    ts2.siftup_steps = 0
    for w in range(1000, 0, -1):
        ts2.add_heap(Timer(w))
    bh2, two2 = [], 0
    for w in range(1000, 0, -1):
        bh2.append(w)
        two2 += bin_sift_up(bh2, len(bh2) - 1)
    ok(ts2.siftup_steps < two2,
       "E5 最坏插入四叉堆上浮次数少于二叉堆 (%d vs %d)" % (ts2.siftup_steps, two2))
    # 闭式核对：递减插入时第 i 个元素的上浮次数 = 它在堆里的深度
    def depth(i, n_ary):
        d, cnt = 0, 1
        total = 0
        while i >= total + cnt:
            total += cnt
            cnt *= n_ary
            d += 1
        return d
    eq(ts2.siftup_steps, sum(depth(i, 4) for i in range(1000)),
       "E5 四叉堆最坏上浮次数 = Σ 四叉深度（闭式）")
    eq(two2, sum(depth(i, 2) for i in range(1000)),
       "E5 二叉堆最坏上浮次数 = Σ 二叉深度（闭式）")
    ok(0.5 < float(ts2.siftup_steps) / two2 < 0.6,
       "E5 上浮次数比值落在 0.5~0.6（实测 %.4f）" % (float(ts2.siftup_steps) / two2))


# ---------------------------------------------------------------- E6 stop 语义
def e6_stop():
    ts = Timers()
    timers = build(ts, [10, 20, 30])
    t0 = timers[0]
    n0 = len(ts.heap)
    pending = ts.stop(t0)
    eq(pending, True, "E6 未触发时 Stop 返回 true")
    eq(len(ts.heap), n0, "E6 Stop 不从堆里摘除")
    eq(ts.zombies, 1, "E6 Stop 记 1 个 zombie")
    ok(t0.state & TIMER_ZOMBIE != 0 and t0.state & TIMER_MODIFIED != 0,
       "E6 Stop 同时置 Zombie 与 Modified")
    eq(t0.when, 0, "E6 Stop 把 when 清零")
    pending2 = ts.stop(t0)
    eq(pending2, False, "E6 已停止的定时器再 Stop 返回 false")
    eq(ts.zombies, 1, "E6 重复 Stop 不重复计数")
    eq(ts.min_when_heap, 10, "E6 Stop 不改 minWhenHeap（堆没动）")


# ---------------------------------------------------------------- E7 cleanHead
def e7_clean_head():
    # (a) 堆尾是 zombie：零堆调整地摘掉
    ts = Timers()
    timers = build(ts, [10, 20, 30, 40])
    tail = [t for t in timers if t.when == 40][0]
    ts.stop(tail)
    before = [tw.when for tw in ts.heap[:-1]]
    ts.clean_head()
    eq(len(ts.heap), 3, "E7a 堆尾 zombie 被摘掉")
    eq([tw.when for tw in ts.heap], before, "E7a 其余元素顺序未变（无堆调整）")
    eq(ts.zombies, 0, "E7a zombie 计数归零")
    # (b) 堆顶是 zombie：走 updateHeap → deleteMin
    ts2 = Timers()
    timers2 = build(ts2, [10, 20, 30])
    head = [t for t in timers2 if t.when == 10][0]
    ts2.stop(head)
    ts2.clean_head()
    eq(len(ts2.heap), 2, "E7b 堆顶 zombie 走 deleteMin 删除")
    eq(ts2.heap[0].when, 20, "E7b 新堆顶是次小值 20")
    ok(heap_ok(ts2), "E7b 删除后堆序成立")


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


def main():
    for fn in (e1_topology, e2_siftdown_early_return, e3_heap_invariant,
               e4_delete_min, e5_fourary_vs_binary, e6_stop, e7_clean_head,
               e8_modify_revives, e9_modify_earlier, e10_ticker,
               e11_oneshot_becomes_zombie, e12_wake_time, e13_rand_order,
               e14_bad_timer):
        fn()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
