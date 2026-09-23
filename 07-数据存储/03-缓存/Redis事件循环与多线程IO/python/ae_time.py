"""ae.c 的时间事件部分（从 ae.py 拆出，保持单文件 ≤ 300 行）。

刻意不从 ae 导入任何符号：ae 会导入本模块，避免循环 import；
EventLoop 全部按鸭子类型访问（el.clock / el.time_event_head / el.time_event_next_id）。
"""

from ae_const import AE_DELETED_EVENT_ID, AE_ERR, AE_NOMORE, AE_OK


class TimeEvent:
    """对应 C 的 aeTimeEvent。"""

    def __init__(self, id, when, time_proc, finalizer_proc, client_data):
        self.id = id
        self.when = when
        self.time_proc = time_proc
        self.finalizer_proc = finalizer_proc
        self.client_data = client_data
        self.prev = None
        self.next = None
        self.refcount = 0


def us_until_earliest_timer(el):
    """ae.c:263：最近一个未删除定时器的剩余微秒；没有则返回 -1（无限等待）。"""
    te = el.time_event_head
    if te is None:
        return -1
    earliest = None
    while te is not None:
        # 已标记删除的事件不参与超时计算
        if (earliest is None or te.when < earliest.when) and te.id != AE_DELETED_EVENT_ID:
            earliest = te
        te = te.next
    if earliest is None:
        return -1
    now = el.clock.get_monotonic_us()
    return 0 if now >= earliest.when else earliest.when - now


def process_time_events(el):
    """ae.c:284：处理到期时间事件，返回处理个数。"""
    processed = 0
    te = el.time_event_head
    max_id = el.time_event_next_id - 1
    now = el.clock.get_monotonic_us()

    while te is not None:
        if te.id == AE_DELETED_EVENT_ID:
            nxt = te.next
            # 有引用（递归 timeProc）时不释放，留到下一轮
            if te.refcount:
                te = nxt
                continue
            if te.prev is not None:
                te.prev.next = te.next
            else:
                el.time_event_head = te.next
            if te.next is not None:
                te.next.prev = te.prev
            if te.finalizer_proc is not None:
                te.finalizer_proc(el, te.client_data)
                now = el.clock.get_monotonic_us()
            te = nxt
            continue

        # 本轮内由时间事件新创建的时间事件不参与本轮处理
        if te.id > max_id:
            te = te.next
            continue

        if te.when <= now:
            te.refcount += 1
            retval = te.time_proc(el, te.id, te.client_data)
            te.refcount -= 1
            processed += 1
            now = el.clock.get_monotonic_us()
            if retval != AE_NOMORE:
                te.when = now + retval * 1000
            else:
                te.id = AE_DELETED_EVENT_ID
        te = te.next

    return processed


def ae_create_time_event(el, milliseconds, proc, finalizer_proc, client_data):
    """ae.c:218：新事件总是插在链表头，id 从 0 起递增。"""
    tid = el.time_event_next_id
    el.time_event_next_id += 1
    te = TimeEvent(
        tid,
        el.clock.get_monotonic_us() + milliseconds * 1000,
        proc,
        finalizer_proc,
        client_data,
    )
    te.next = el.time_event_head
    if te.next is not None:
        te.next.prev = te
    el.time_event_head = te
    return tid


def ae_delete_time_event(el, tid):
    """ae.c:241：只打 AE_DELETED_EVENT_ID 标记，真正释放在下一轮。"""
    te = el.time_event_head
    while te is not None:
        if te.id == tid:
            te.id = AE_DELETED_EVENT_ID
            return AE_OK
        te = te.next
    return AE_ERR
