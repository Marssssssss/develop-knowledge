"""Go 定时器四叉堆：可运行演示。

模型逐行转写自官方源码 src/runtime/time.go，这里是四个能直接看出来的结论：
1) 堆是四叉的（timerHeapN=4），不是二叉；
2) Stop 只打 Zombie 标记，不从堆里摘除；
3) Reset 走 modify，堆里的 when 快照延后到 updateHeap/adjust 才同步；
4) ticker 用 next = when + period*(1+delay/period) 跳过 missed tick。
"""

from timermodel import Timer, Timers, TIMER_ZOMBIE, TIMER_MODIFIED


def section(title):
    print("\n== %s ==" % title)


def dump(ts):
    print("  heap = %s" % [(tw.timer.name, tw.when) for tw in ts.heap])


def main():
    section("1. 四叉堆：8 个定时器入堆后的父子关系")
    ts = Timers()
    for i, w in enumerate([50, 10, 90, 30, 70, 20, 80, 40]):
        ts.add_heap(Timer(w, name="t%d" % i))
    dump(ts)
    for i in range(1, len(ts.heap)):
        print("  heap[%d]=%d 的父节点 heap[%d]=%d"
              % (i, ts.heap[i].when, (i - 1) // 4, ts.heap[(i - 1) // 4].when))

    section("2. Stop 只打标记，堆长度不变")
    head = ts.heap[0].timer
    print("  Stop(%s) -> pending=%s" % (head.name, ts.stop(head)))
    dump(ts)
    print("  len(heap)=%d  zombies=%d  state(zombie=%s, modified=%s)"
          % (len(ts.heap), ts.zombies,
             bool(head.state & TIMER_ZOMBIE), bool(head.state & TIMER_MODIFIED)))
    ts.clean_head()
    dump(ts)
    print("  cleanHead 之后 len(heap)=%d（堆顶 zombie 走 deleteMin 摘除）" % len(ts.heap))

    section("3. Reset 改晚：快照延后同步")
    ts2 = Timers()
    timers = []
    for i, w in enumerate([10, 20, 30]):
        t = Timer(w, name="r%d" % i)
        timers.append(t)
        ts2.add_heap(t)
    print("  Reset(r0, 25) -> pending=%s wake=%s" % ts2.modify(timers[0], 25))
    dump(ts2)
    print("  ↑ 堆顶快照仍是 10，因为 modify 不碰堆")
    ts2.update_heap(timers[0])
    dump(ts2)
    print("  ↑ updateHeap 同步快照并 siftDown(0)")

    section("4. ticker 跳过 missed tick")
    ts3 = Timers()
    tick = Timer(10, period=5, name="tick")
    ts3.add_heap(tick)
    for now in (9, 27, 29, 30, 31):
        r = ts3.run(now)
        print("  run(now=%2d) -> %2d ; next when=%d ; fired=%d"
              % (now, r, tick.when, len(ts3.fired)))
    print("  delay=17 时 next=10+5*(1+17/5)=30，而不是 15/20/25 逐个补")


if __name__ == "__main__":
    main()
