"""演示：一次 aeMain 循环里发生了什么。

输出三件事：
  1. 一个 fd 上 READABLE|WRITABLE 同时就绪时的派发顺序（含 AE_BARRIER 反转）
  2. 时间事件的到期 → 重排 / 自毁
  3. poll 超时是如何由最近的定时器算出来的
"""

from ae import (
    AE_ALL_EVENTS,
    AE_BARRIER,
    AE_CALL_AFTER_SLEEP,
    AE_CALL_BEFORE_SLEEP,
    AE_FILE_EVENTS,
    AE_NOMORE,
    AE_READABLE,
    AE_TIME_EVENTS,
    AE_WRITABLE,
    Clock,
    EventLoop,
    FiredEvent,
    ae_create_file_event,
    ae_process_events,
)
from ae_time import ae_create_time_event
from io_threads import (
    COPY_AVOID_MIN_IO_THREADS,
    COPY_AVOID_MIN_STRING_SIZE,
    COPY_AVOID_MIN_STRING_SIZE_THREADED,
    IO_THREADS_MAX_NUM,
    FakeClient,
    FakeRobj,
    Server,
    is_copy_avoid_preferred,
    suggested_io_threads,
)

READY = [(7, AE_READABLE | AE_WRITABLE)]


def build(clock, ready):
    return lambda el, tvp: [FiredEvent(fd, mask) for fd, mask in ready]


def demo_dispatch(barrier):
    clock = Clock(0)
    el = EventLoop(64, clock, build(clock, READY))
    trace = []
    ae_create_file_event(el, 7, AE_READABLE, lambda *a: trace.append("读回调"), None)
    ae_create_file_event(el, 7, AE_WRITABLE, lambda *a: trace.append("写回调"), None)
    if barrier:
        el.events[7].mask |= AE_BARRIER
    ae_process_events(el, AE_FILE_EVENTS | AE_CALL_BEFORE_SLEEP | AE_CALL_AFTER_SLEEP)
    return trace, el.last_tvp


def demo_timer():
    clock = Clock(1_000_000)
    el = EventLoop(64, clock, build(clock, []))
    fired = []

    def cron(el_, tid, data):
        fired.append(clock.get_monotonic_us())
        clock.advance(20_000)
        return 50

    def oneshot(el_, tid, data):
        fired.append(clock.get_monotonic_us())
        return AE_NOMORE

    ae_create_time_event(el, 10, cron, None, None)
    ae_create_time_event(el, 10, oneshot, None, None)
    clock.advance(10_000)   # 循环睡到最近定时器到期
    before = el.last_tvp
    n1 = ae_process_events(el, AE_TIME_EVENTS)
    return el, fired, n1, before


def main():
    print("== 1. 文件事件派发顺序 ==")
    trace, tvp = demo_dispatch(False)
    print(f"  无 AE_BARRIER : {trace}   poll 超时={tvp}"
          f"（flags 未带 AE_TIME_EVENTS，不按定时器算超时 → 无限等待）")
    trace2, _ = demo_dispatch(True)
    print(f"  有 AE_BARRIER : {trace2}  （写被提到读之前）")

    print("\n== 2. 时间事件 ==")
    el, fired, n1, _ = demo_timer()
    print(f"  本轮处理 {n1} 个时间事件，触发时刻(us) = {fired}")
    print(f"  剩余定时器 id 与下次到期(when)：", end=" ")
    te = el.time_event_head
    while te:
        print(f"id={te.id} when={te.when}", end="  ")
        te = te.next
    print("\n  （一次性事件返回 AE_NOMORE → id 已置 -1，下一轮才释放）")

    print("\n== 3. poll 超时由最近定时器决定 ==")
    clock = Clock(0)
    el = EventLoop(64, clock, build(clock, []))
    ae_create_time_event(el, 30, lambda *a: AE_NOMORE, None, None)
    ae_process_events(el, AE_TIME_EVENTS)
    print(f"  最近定时器 30ms 后到期 → tvp = {el.last_tvp} us")

    print("\n== 4. 多线程 I/O ==")
    print(f"  IO_THREADS_MAX_NUM = {IO_THREADS_MAX_NUM}")
    print(f"  拷贝规避阈值：>= {COPY_AVOID_MIN_IO_THREADS} 线程时不限长度；"
          f"单线程 {COPY_AVOID_MIN_STRING_SIZE} B；带线程 {COPY_AVOID_MIN_STRING_SIZE_THREADED} B")
    srv = Server(io_threads_num=4)
    c, o = FakeClient(), FakeRobj()
    print(f"  4 线程、16 KB 字符串 → 走引用? {bool(is_copy_avoid_preferred(srv, c, o, 16384))}")
    print(f"  4 线程、64 KB 字符串 → 走引用? {bool(is_copy_avoid_preferred(srv, c, o, 65536))}")
    print(f"  8 核建议 io-threads = {suggested_io_threads(8)}（conf：4 核用 3、8 核用 7）")


if __name__ == "__main__":
    main()
