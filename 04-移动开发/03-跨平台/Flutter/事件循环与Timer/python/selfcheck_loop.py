"""Dart 事件循环与 Timer 自检：微任务链表 / 二叉堆 / 零延迟 FIFO / _runTimers。

运行： python selfcheck_loop.py
"""

from main import (
    AsyncRuntime, TimerRuntime, Timer, TimerHeap,
    ZERO_EVENT, TIMEOUT_EVENT, ID_MASK,
)

PASS = 0


def ok(cond, label, actual=None):
    global PASS
    assert cond, "FAIL: {} -> {!r}".format(label, actual)
    PASS += 1


def rt_with(order):
    rt = AsyncRuntime()
    for name in order:
        rt.schedule_microtask(lambda n=name: order_rt(n, rt))
    return rt


LOGS = []


def order_rt(name, rt):
    LOGS.append(name)


def fresh():
    LOGS.clear()
    return LOGS


# ======================================================= A. 微任务单链表
log = fresh()
rt = AsyncRuntime()
for n in ("m1", "m2", "m3"):
    rt.schedule_microtask(lambda n=n: log.append(n))
rt.run_event_loop()
ok(log == ["m1", "m2", "m3"], "A1 微任务 FIFO", log)
ok(rt.schedule_count == 1, "A2 三个微任务只排了一次 immediate（一次 loop 排空）",
   rt.schedule_count)

log = fresh()
rt = AsyncRuntime()
rt.schedule_microtask(lambda: (log.append("m1"),
                               rt.schedule_microtask(lambda: log.append("m1.1"))))
rt.run_event_loop()
ok(log == ["m1", "m1.1"], "A3 loop 中新增的微任务在同一轮里跑完", log)
ok(rt.schedule_count == 1, "A4 排空前新增不会触发新的 immediate（_isInCallbackLoop 保证）",
   rt.schedule_count)

log = fresh()
rt = AsyncRuntime()
rt.schedule_microtask(lambda: log.append("m1"))
rt.run_event_loop()
rt.schedule_microtask(lambda: log.append("m2"))
ok(rt.schedule_count == 2, "A5 loop 已退出后再排 -> 需要新的 immediate", rt.schedule_count)
rt.run_event_loop()
ok(log == ["m1", "m2"], "A6 第二个 immediate 里执行 m2", log)

log = fresh()
rt = AsyncRuntime()
rt.schedule_microtask(lambda: log.append("a"))
rt.schedule_priority_async_callback(lambda: log.append("p1"))
rt.run_event_loop()
ok(log == ["p1", "a"], "A7 优先级回调插到已排队微任务之前", log)

log = fresh()
rt = AsyncRuntime()
rt.schedule_microtask(lambda: log.append("a"))


def body_p1():
    log.append("p1")
    rt.schedule_priority_async_callback(lambda: log.append("p2"))
    rt.schedule_priority_async_callback(lambda: log.append("p3"))
    rt.schedule_microtask(lambda: log.append("b"))


rt.schedule_priority_async_callback(body_p1)
rt.run_event_loop()
ok(log == ["p1", "p2", "p3", "a", "b"],
   "A8 多个优先级回调之间仍按调度顺序（插在上一个优先级之后、普通之前）", log)

rt = AsyncRuntime()
rt.schedule_priority_async_callback(lambda: None)
ok(rt.last_priority_callback is rt.next_callback,
   "A9 队列空时走的其实是普通路径，并把该项记为 _lastPriorityCallback")

rt = AsyncRuntime()
rt.schedule_microtask(lambda: None)
rt.run_event_loop()
ok(rt.next_callback is None and rt.last_callback is None,
   "A10 排空后 _nextCallback 与 _lastCallback 都置 null")

# ======================================================= B. Timer 二叉堆
h = TimerHeap()
ok(len(h.list) == 7, "B1 堆初始容量 7", len(h.list))
for w in range(8):
    t = Timer(None, w, 1, False, w)
    h.add(t)
ok(len(h.list) == 15, "B2 扩容公式 2n+1：7 -> 15", len(h.list))
ok(TimerHeap._parent_index(3) == 1 and TimerHeap._parent_index(1) == 0,
   "B3 父下标 (i-1)~/2")

h = TimerHeap()
for w in (5, 3, 9, 1, 7):
    h.add(Timer(None, w, 1, False, w))
seq = []
while not h.is_empty:
    seq.append(h.remove_first().wakeup_time)
ok(seq == [1, 3, 5, 7, 9], "B4 小顶堆出队有序", seq)

h = TimerHeap()
h.add(Timer(None, 5, 1, False, 0))
h.add(Timer(None, 5, 1, False, 1))
ok([h.remove_first().id, h.remove_first().id] == [0, 1],
   "B5 唤醒时刻相同 -> 按 _id 排序（FIFO）")

h = TimerHeap()
for w in (4, 2, 6, 1, 3, 5):
    h.add(Timer(None, w, 1, False, w))
h.remove_first()
seq = []
while not h.is_empty:
    seq.append(h.remove_first().wakeup_time)
ok(seq == [2, 3, 4, 5, 6], "B6 remove 尾部元素上浮/下沉后堆序仍成立", seq)

tr = TimerRuntime()
tr.id_count = ID_MASK
first = tr.next_id()
second = tr.next_id()
ok(first == ID_MASK and second == 0,
   "B7 _nextId 按 _ID_MASK=0x1fffffff 回绕（源码注释：接受碰撞与乱序）", (first, second))

# ======================================================= C. 零延迟计时器
log = fresh()
tr = TimerRuntime()
for _ in range(3):
    tr.create_timer(lambda t: log.append("z"), 0)
ok(len(tr.messages) == 3 and all(m == ZERO_EVENT for m in tr.messages),
   "C1 每个零延迟计时器各占一个 _ZERO_EVENT 消息", list(tr.messages))
tr.deliver()
ok(log == ["z", "z", "z"], "C2 零延迟计时器按创建顺序触发", log)

tr = TimerRuntime()
tr.now = 1000
tz = Timer(None, 1000, 0, False, 9)
tr._enqueue(tz)
early = Timer(None, 900, 100, False, 0)
tr.heap.add(early)
pending = tr.queue_from_zero_event()
ok([t.id for t in pending] == [0, 9],
   "C3 _queueFromZeroEvent 先把比零延迟更早就到期的堆顶取出，再取零延迟本身",
   [t.id for t in pending])

tr = TimerRuntime()
tr.now = 1000
tz = Timer(None, 1000, 0, False, 9)
tr._enqueue(tz)
late = Timer(None, 1100, 100, False, 0)
tr.heap.add(late)
pending = tr.queue_from_zero_event()
ok([t.id for t in pending] == [9],
   "C4 堆顶比零延迟更晚时不取，零延迟等自己的消息", [t.id for t in pending])

# ======================================================= D. 超时事件
tr = TimerRuntime()
tr.now = 1000
for w in (900, 950, 1100):
    tr.heap.add(Timer(None, w, 100, False, w))
pending = tr.queue_from_timeout_event()
ok([t.wakeup_time for t in pending] == [900, 950],
   "D1 无零延迟计时器时按当前时刻取所有已到期项", [t.wakeup_time for t in pending])

tr = TimerRuntime()
tr.now = 2000
tz = Timer(None, 1000, 0, False, 9)
tr._enqueue(tz)
tr.heap.add(Timer(None, 1500, 100, False, 1500))
pending = tr.queue_from_timeout_event()
ok([t.id for t in pending] == [],
   "D2 有零延迟时只看「是否早于零延迟」，不看当前时刻（1500 > 1000 不取）",
   [t.id for t in pending])

# ======================================================= E. _runTimers
tr = TimerRuntime()
tr.id_count = 5
tr.run_timers([])
ok(tr.id_count == 0, "E1 堆与零延迟都空时回收 id 空间", tr.id_count)

log = fresh()
tr = TimerRuntime()
tr.now = 0
t1 = tr.create_timer(lambda t: log.append("t1"), 10)
tr.now = 10
tr.messages.append(TIMEOUT_EVENT)
tr.deliver()
ok(log == ["t1"], "E2 到期后触发一次", log)
ok(t1.callback is None, "E3 非周期计时器触发后把 callback 置空（isActive 变 false）")

log = fresh()
tr = TimerRuntime()
tr.now = 0
t2 = tr.create_timer(lambda t: log.append("tick"), 10)
tr.now = 10
tr.messages.append(TIMEOUT_EVENT)
tr.deliver()
tr.deliver()
ok(log == ["tick"], "E4 没有新消息就不会重复触发", log)

tr = TimerRuntime()
tr.now = 1000            # 唤醒时刻 = 1100
rep = tr.create_timer(lambda t: None, 100, repeating=True)
tr.now = 1350            # 逾期 250ms
tr.handle_message(TIMEOUT_EVENT)
ok(rep.tick == 3, "E5 周期计时器逾期 250ms：missedTicks=2，tick = 2 + 1 = 3", rep.tick)
ok(rep.wakeup_time == 1400, "E6 先补到 1300（1100+2×100）再加一个周期 -> 1400",
   rep.wakeup_time)

log = fresh()
tr = TimerRuntime()
tr.now = 0
created = []


def outer(t):
    log.append("outer")
    created.append(tr.create_timer(lambda s: log.append("inner"), 5))


tr.create_timer(outer, 10)
tr.now = 10
tr.messages.append(TIMEOUT_EVENT)
tr.deliver()
ok(log == ["outer"], "E7 回调里新建的计时器不会在同一轮触发（要等下一次事件）", log)
tr.now = 15
tr.messages.append(TIMEOUT_EVENT)
tr.deliver()
ok(log == ["outer", "inner"], "E8 下一次事件才轮到它", log)

log = fresh()
tr = TimerRuntime()
tr.now = 0
tr.create_timer(lambda t: log.append("rep"), 10)
tr.now = 10
tr.messages.append(TIMEOUT_EVENT)


def probe():
    tr.handling_callbacks = True
    tr.scheduled_wakeup_time = 7
    tr._notify_event_handler()
    return tr.scheduled_wakeup_time


ok(probe() == 7, "E9 _handlingCallbacks 期间 _notifyEventHandler 直接返回")

# ======================================================= F. 微任务与计时器交错
log = fresh()
ar = AsyncRuntime()
tr = TimerRuntime(async_rt=ar)
tr.now = 0
tr.create_timer(lambda t: (log.append("timer"),
                           ar.schedule_microtask(lambda: log.append("micro"))), 10)
tr.now = 10
tr.messages.append(TIMEOUT_EVENT)
tr.deliver()
ok(log == ["timer", "micro"], "F1 每个计时器回调后都会跑一次挂起的微任务", log)

# ======================================================= G. Isolate.spawn 默认值
SPAWN_DEFAULTS = {"paused": False, "errorsAreFatal": True}  # isolate_patch.dart:366
ok(SPAWN_DEFAULTS["paused"] is False, "G1 Isolate.spawn 的 paused 默认 false")
ok(SPAWN_DEFAULTS["errorsAreFatal"] is True, "G2 Isolate.spawn 的 errorsAreFatal 默认 true")

print("Dart 事件循环与 Timer 自检：{} 项断言全部通过".format(PASS))
