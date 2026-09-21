"""Dart 事件循环：微任务链表 + Timer 二叉堆 + 零延迟计时器 FIFO 的可执行模型。

对应源码（dart-lang/sdk @main）：
  sdk/lib/async/schedule_microtask.dart  —— 微任务单链表、优先级回调、microtaskLoop
  sdk/lib/_internal/vm/lib/timer_impl.dart —— _TimerHeap、零延迟计时器链表、_runTimers
  sdk/lib/isolate/isolate.dart + _internal/vm/lib/isolate_patch.dart —— Isolate.spawn / ReceivePort

常量与分支顺序均按源码转写；Python 无 Dart 的 int64 语义处显式模拟。
"""

from collections import deque

# ------------------------------------------------------------------ 微任务


class _AsyncCallbackEntry:
    __slots__ = ("callback", "next")

    def __init__(self, callback):
        self.callback = callback
        self.next = None


class AsyncRuntime:
    """schedule_microtask.dart 的状态机。"""

    def __init__(self):
        self.next_callback = None      # _nextCallback
        self.last_callback = None      # _lastCallback
        self.last_priority_callback = None
        self.is_in_callback_loop = False
        self.immediates = deque()      # _AsyncRun._scheduleImmediate 的目标队列
        self.schedule_count = 0        # 观测：总共排了几次 immediate
        self.log = []

    # ---- external static void _scheduleImmediate(void Function() callback)
    def schedule_immediate(self, callback):
        self.schedule_count += 1
        self.immediates.append(callback)

    def schedule_async_callback(self, callback):
        new_entry = _AsyncCallbackEntry(callback)
        # _beforeScheduleMicrotaskCallback —— VM 里用于触发 scheduler，这里留空
        last = self.last_callback
        if last is None:
            self.next_callback = self.last_callback = new_entry
            if not self.is_in_callback_loop:
                self.schedule_immediate(self.start_microtask_loop)
        else:
            last.next = new_entry
            self.last_callback = new_entry

    def schedule_priority_async_callback(self, callback):
        if self.next_callback is None:
            self.schedule_async_callback(callback)
            self.last_priority_callback = self.last_callback
            return
        entry = _AsyncCallbackEntry(callback)
        last_priority = self.last_priority_callback
        if last_priority is None:
            entry.next = self.next_callback
            self.next_callback = self.last_priority_callback = entry
        else:
            nxt = last_priority.next
            entry.next = nxt
            last_priority.next = entry
            self.last_priority_callback = entry
            if nxt is None:
                self.last_callback = entry

    def microtask_loop(self):
        entry = self.next_callback
        while entry is not None:
            self.last_priority_callback = None   # 每轮开头清一次
            nxt = entry.next
            self.next_callback = nxt
            if nxt is None:
                self.last_callback = None
            entry.callback()
            entry = self.next_callback

    def start_microtask_loop(self):
        self.is_in_callback_loop = True
        try:
            self.microtask_loop()
        finally:
            self.last_priority_callback = None
            self.is_in_callback_loop = False
            if self.next_callback is not None:
                # 排空后仍有新来的：再排一次 immediate
                self.schedule_immediate(self.start_microtask_loop)

    def run_event_loop(self, max_turns=1000):
        turns = 0
        while self.immediates and turns < max_turns:
            cb = self.immediates.popleft()
            cb()
            turns += 1
        return turns

    def schedule_microtask(self, callback):
        self.schedule_async_callback(callback)


# -------------------------------------------------------------- Timer 二叉堆

ID_MASK = 0x1FFFFFFF
ZERO_EVENT = 1
TIMEOUT_EVENT = None
NO_TIMER = -1


class Timer:
    def __init__(self, callback, wakeup_time, milli_seconds, repeating, tid):
        self.callback = callback
        self.wakeup_time = wakeup_time
        self.milli_seconds = milli_seconds
        self.repeating = repeating
        self.index_or_next = None     # 堆内是下标，零延迟链表里是下一个 Timer
        self.id = tid
        self.tick = 0
        self.canceled = False

    def compare_to(self, other):
        c = self.wakeup_time - other.wakeup_time
        if c != 0:
            return c
        return self.id - other.id


class TimerHeap:
    """timer_impl.dart:22 —— 二叉小顶堆，初值 7，扩容 2n+1。"""

    def __init__(self, init_size=7):
        self.list = [None] * init_size
        self.used = 0

    @property
    def is_empty(self):
        return self.used == 0

    @property
    def first(self):
        return self.list[0]

    def is_first(self, timer):
        return timer.index_or_next == 0

    def add(self, timer):
        if self.used == len(self.list):
            self._resize()
        index = self.used
        self.used += 1
        timer.index_or_next = index
        self.list[index] = timer
        self._bubble_up(timer)

    def remove_first(self):
        f = self.first
        self.remove(f)
        return f

    def remove(self, timer):
        self.used -= 1
        if self.is_empty:
            self.list[0] = None
            timer.index_or_next = None
            return
        last = self.list[self.used]
        if last is not timer:
            index = timer.index_or_next
            last.index_or_next = index
            self.list[index] = last
            if last.compare_to(timer) < 0:
                self._bubble_up(last)
            else:
                self._bubble_down(last)
        self.list[self.used] = None
        timer.index_or_next = None

    def _resize(self):
        new_list = [None] * (len(self.list) * 2 + 1)
        new_list[0:self.used] = self.list[0:self.used]
        self.list = new_list

    def _bubble_up(self, timer):
        while not self.is_first(timer):
            parent = self._parent(timer)
            if timer.compare_to(parent) < 0:
                self._swap(timer, parent)
            else:
                break

    def _bubble_down(self, timer):
        while True:
            li = 2 * timer.index_or_next + 1
            ri = 2 * timer.index_or_next + 2
            newest = timer
            if li < self.used and self.list[li].compare_to(newest) < 0:
                newest = self.list[li]
            if ri < self.used and self.list[ri].compare_to(newest) < 0:
                newest = self.list[ri]
            if newest is timer:
                break
            self._swap(newest, timer)

    def _swap(self, a, b):
        ia, ib = b.index_or_next, a.index_or_next
        a.index_or_next, b.index_or_next = ia, ib
        self.list[ia], self.list[ib] = a, b

    @staticmethod
    def _parent_index(i):
        return (i - 1) // 2

    def _parent(self, timer):
        return self.list[self._parent_index(timer.index_or_next)]


class TimerRuntime:
    """timer_impl.dart 的静态状态：堆 + 零延迟链表 + 消息队列。"""

    def __init__(self, async_rt=None):
        self.heap = TimerHeap()
        self.first_zero_timer = None
        self.last_zero_timer = None
        self.messages = deque()       # 发给 timer port 的消息
        self.id_count = 0
        self.handling_callbacks = False
        self.scheduled_wakeup_time = 0
        self.now = 0
        self.async_rt = async_rt
        self.log = []

    def next_id(self):
        result = self.id_count
        self.id_count = (self.id_count + 1) & ID_MASK
        return result

    def create_timer(self, callback, millis, repeating=False):
        if millis < 0:                # 负超时按 0 处理
            millis = 0
        t = Timer(callback, self.now + millis, millis, repeating, self.next_id())
        t.index_or_next = None
        self._enqueue(t)
        return t

    def _enqueue(self, timer):
        if timer.milli_seconds == 0:
            if self.first_zero_timer is None:
                self.last_zero_timer = self.first_zero_timer = timer
            else:
                self.last_zero_timer.index_or_next = timer
                self.last_zero_timer = timer
            # 每个零延迟计时器都独占一个消息
            self.messages.append(ZERO_EVENT)
        else:
            self.heap.add(timer)
            if self.heap.is_first(timer):
                self._notify_event_handler()

    def _notify_event_handler(self):
        if self.handling_callbacks:
            return
        if self.first_zero_timer is None and self.heap.is_empty:
            self.scheduled_wakeup_time = 0
            return
        if self.heap.is_empty:
            self.scheduled_wakeup_time = 0
            return
        wakeup = self.heap.first.wakeup_time
        if self.scheduled_wakeup_time == 0 or wakeup != self.scheduled_wakeup_time:
            self.scheduled_wakeup_time = wakeup

    def queue_from_zero_event(self):
        pending = []
        first = self.first_zero_timer
        if first is not None:
            while (not self.heap.is_empty) and self.heap.first.compare_to(first) < 0:
                pending.append(self.heap.remove_first())
            self.first_zero_timer = first.index_or_next
            first.index_or_next = None
            pending.append(first)
        return pending

    def queue_from_timeout_event(self):
        pending = []
        first = self.first_zero_timer
        if first is not None:
            while (not self.heap.is_empty) and self.heap.first.compare_to(first) < 0:
                pending.append(self.heap.remove_first())
        else:
            while (not self.heap.is_empty) and self.heap.first.wakeup_time <= self.now:
                pending.append(self.heap.remove_first())
        return pending

    def run_timers(self, pending):
        if self.heap.is_empty and self.first_zero_timer is None:
            self.id_count = 0        # id 空间回收
        if len(pending) == 0:
            return
        self.handling_callbacks = True
        i = 0
        try:
            while i < len(pending):
                timer = pending[i]
                i += 1
                timer.index_or_next = None
                overdue = self.now - timer.wakeup_time
                if timer.callback is None:
                    continue
                if not timer.repeating:
                    cb, timer.callback = timer.callback, None
                elif timer.milli_seconds > 0 and overdue > timer.milli_seconds:
                    missed = overdue // timer.milli_seconds
                    timer.wakeup_time += missed * timer.milli_seconds
                    timer.tick += missed
                    cb = timer.callback
                else:
                    cb = timer.callback
                timer.tick += 1
                cb(timer)
                self.log.append(("tick", timer.id, timer.tick))
                if timer.repeating and timer.callback is not None:
                    # _advanceWakeupTime：非零周期累加，零周期取当前时刻；id 重新分配
                    if timer.milli_seconds > 0:
                        timer.wakeup_time += timer.milli_seconds
                    else:
                        timer.wakeup_time = self.now
                    timer.id = self.next_id()
                    self._enqueue(timer)
                # 每个回调后执行挂起的微任务
                if self.async_rt is not None:
                    self.async_rt.start_microtask_loop()
        finally:
            self.handling_callbacks = False
            # 没跑到的（只有抛异常时才有）重新入队
            for j in range(i, len(pending)):
                self._enqueue(pending[j])
            self._notify_event_handler()

    def handle_message(self, msg):
        if msg == ZERO_EVENT:
            pending = self.queue_from_zero_event()
        else:
            self.scheduled_wakeup_time = 0
            pending = self.queue_from_timeout_event()
        self.run_timers(pending)
        self._notify_event_handler()

    def deliver(self, steps=100):
        n = 0
        while self.messages and n < steps:
            self.handle_message(self.messages.popleft())
            n += 1
        return n


def main():
    rt = AsyncRuntime()
    order = []
    rt.schedule_microtask(lambda: order.append("m1"))
    rt.schedule_microtask(lambda: order.append("m2"))
    rt.schedule_microtask(lambda: order.append("m3"))
    rt.run_event_loop()
    print("微任务顺序:", order, " immediate 次数:", rt.schedule_count)

    rt2 = AsyncRuntime()
    order2 = []
    rt2.schedule_microtask(lambda: order2.append("a"))
    rt2.schedule_priority_async_callback(lambda: order2.append("p1"))

    def p1_body():
        order2.append("p1")
        rt2.schedule_priority_async_callback(lambda: order2.append("p2"))
        rt2.schedule_microtask(lambda: order2.append("b"))

    rt2.next_callback.callback = p1_body
    rt2.run_event_loop()
    print("优先级插队:", order2)

    tr = TimerRuntime()
    for w in (5, 3, 9, 1, 7):
        tr.create_timer(lambda t: None, w)
    seq = []
    while not tr.heap.is_empty:
        seq.append(tr.heap.remove_first().wakeup_time)
    print("堆出队顺序:", seq, " 容量:", len(tr.heap.list))

    tr2 = TimerRuntime()
    fired = []
    for _ in range(3):
        tr2.create_timer(lambda t: fired.append("z"), 0)
    print("零延迟计时器消息数:", len(tr2.messages))
    tr2.now = 0
    tr2.deliver()
    print("零延迟触发:", fired)


if __name__ == "__main__":
    main()
