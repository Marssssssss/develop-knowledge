"""Go 定时器四叉堆模型的自检入口。

E1~E7（堆拓扑、堆序、删除、四叉 vs 二叉、Stop/cleanHead）在本文件；
E8~E14（modify/改早/ticker/zombie/wakeTime/rand/badTimer）在 timerchecks.py。
断言全部来自官方源码 src/runtime/time.go 的实读结论。

运行：python selfcheck_timers.py
"""

import random
import sys

from timerchecks import (  # noqa: F401
    PASS, FAIL, ok, eq, raises, heap_ok, build, state_checks,
)
from timermodel import (  # noqa: F401
    TIMER_HEAP_N, TIMER_HEAPED, TIMER_MODIFIED, TIMER_ZOMBIE, MAX_WHEN,
    Timer, Timers, TimerWhen, less, BadTimer,
)

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


def main():
    for fn in (e1_topology, e2_siftdown_early_return, e3_heap_invariant,
               e4_delete_min, e5_fourary_vs_binary, e6_stop, e7_clean_head):
        fn()
    state_checks()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
