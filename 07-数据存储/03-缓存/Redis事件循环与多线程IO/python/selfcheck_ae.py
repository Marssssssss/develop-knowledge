"""ae.c 文件事件部分自检：全部断言均由可执行的转写实跑得出。

每条断言都成对构造（只差一个开关 / 一个参数），避免「通过 ≠ 验到」。
时间事件相关断言见 selfcheck_time.py。
"""

from ae import (
    AE_ALL_EVENTS,
    AE_BARRIER,
    AE_CALL_AFTER_SLEEP,
    AE_CALL_BEFORE_SLEEP,
    AE_DELETED_EVENT_ID,
    AE_DONT_WAIT,
    AE_ERR,
    AE_FILE_EVENTS,
    AE_NOMORE,
    AE_NONE,
    AE_OK,
    AE_READABLE,
    AE_TIME_EVENTS,
    AE_WRITABLE,
    Clock,
    EventLoop,
    FiredEvent,
    INITIAL_EVENT,
    ae_create_file_event,
    ae_delete_file_event,
    ae_process_events,
)

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL {name}: got {got!r} want {want!r}")


def make_loop(setsize=64, ready=(), now=1_000_000):
    """ready: 本轮就绪的 (fd, mask) 列表；poll 记录收到的 tvp。"""
    clock = Clock(now)
    log = []

    def poll_fn(el, tvp):
        log.append(("poll", tvp))
        return [FiredEvent(fd, mask) for fd, mask in ready]

    el = EventLoop(setsize, clock, poll_fn)
    return el, clock, log


def recorder(tag, log):
    def proc(el, fd, data, mask):
        log.append(tag)

    return proc


# ---------------------------------------------------------------- 常量
check("AE_READABLE", AE_READABLE, 1)
check("AE_WRITABLE", AE_WRITABLE, 2)
check("AE_BARRIER", AE_BARRIER, 4)
check("AE_ALL_EVENTS = FILE|TIME", AE_ALL_EVENTS, 3)
check("AE_DONT_WAIT", AE_DONT_WAIT, 4)
check("AE_CALL_BEFORE_SLEEP", AE_CALL_BEFORE_SLEEP, 8)
check("AE_CALL_AFTER_SLEEP", AE_CALL_AFTER_SLEEP, 16)
check("AE_NOMORE 与 AE_DELETED_EVENT_ID 数值相同（语义不同）", AE_NOMORE == AE_DELETED_EVENT_ID, True)

# ---------------------------------------------------------------- 初始化
el, clock, _ = make_loop(setsize=64)
check("初始 maxfd", el.maxfd, -1)
check("初始 timeEventNextId", el.time_event_next_id, 0)
check("初始 stop", el.stop, 0)
check("初始 nevents = min(setsize, INITIAL_EVENT)", el.nevents, INITIAL_EVENT)
check("小 setsize 时 nevents = setsize", EventLoop(4, Clock(), lambda e, t: []).nevents, 4)

# ---------------------------------------------------------------- 文件事件注册 / 扩容
el, _, _ = make_loop(setsize=64)
check("fd >= setsize → AE_ERR", ae_create_file_event(el, 64, AE_READABLE, lambda *a: None, None), AE_ERR)
check("fd = setsize-1 → AE_OK", ae_create_file_event(el, 63, AE_READABLE, lambda *a: None, None), AE_OK)
check("maxfd 取最大 fd", el.maxfd, 63)
check("扩容后 nevents 被 setsize 截断", el.nevents, 64)

el, _, _ = make_loop(setsize=64)
ae_create_file_event(el, 20, AE_READABLE, lambda *a: None, None)
check("fd=20 触发翻倍：16*2=32 > 21", el.nevents, 32)
ae_create_file_event(el, 40, AE_READABLE, lambda *a: None, None)
check("fd=40 再翻倍：32*2=64 > 41", el.nevents, 64)

# ---------------------------------------------------------------- 删除文件事件
proc = lambda *a: None
el, _, _ = make_loop(setsize=64)
ae_create_file_event(el, 3, AE_READABLE | AE_WRITABLE | AE_BARRIER, proc, None)
check("注册后 mask 含 BARRIER", el.events[3].mask & AE_BARRIER, AE_BARRIER)
ae_delete_file_event(el, 3, AE_WRITABLE)
check("删 WRITABLE 时 BARRIER 一并清除", el.events[3].mask & AE_BARRIER, 0)
check("删 WRITABLE 后 READABLE 仍在", el.events[3].mask, AE_READABLE)

el, _, _ = make_loop(setsize=64)
ae_create_file_event(el, 3, AE_READABLE, proc, None)
ae_create_file_event(el, 5, AE_READABLE, proc, None)
ae_delete_file_event(el, 5, AE_READABLE)
check("删最高 fd 后 maxfd 回退到 3", el.maxfd, 3)
ae_delete_file_event(el, 3, AE_READABLE)
check("全部删空后 maxfd = -1", el.maxfd, -1)
check("删空后 mask = AE_NONE", el.events[3].mask, AE_NONE)
ae_delete_file_event(el, 3, AE_READABLE)
check("对 AE_NONE 再删是 no-op（不抛错）", el.events[3].mask, AE_NONE)

# ---------------------------------------------------------------- 早退：既无 FILE 也无 TIME
el, _, log = make_loop(ready=[(1, AE_READABLE)])
ae_create_file_event(el, 1, AE_READABLE, recorder("r", log), None)
check("flags=0 → 直接返回 0", ae_process_events(el, 0), 0)
check("flags=0 时不 poll", log, [])

el, _, log = make_loop(ready=[(1, AE_READABLE)])
ae_create_file_event(el, 1, AE_READABLE, recorder("r", log), None)
check("flags=AE_DONT_WAIT(4) 仍缺 FILE/TIME → 返回 0", ae_process_events(el, AE_DONT_WAIT), 0)
check("flags=AE_DONT_WAIT(4) 时不 poll", log, [])

# ---------------------------------------------------------------- poll 门控与超时
el, _, log = make_loop()
check("maxfd=-1 且 TIME|DONT_WAIT → 不 poll", ae_process_events(el, AE_TIME_EVENTS | AE_DONT_WAIT), 0)
check("  未 poll 则不会产生 poll 记录", log, [])

el, _, log = make_loop()
ae_process_events(el, AE_TIME_EVENTS)
check("maxfd=-1 但有 TIME 且无 DONT_WAIT → 仍 poll（为了睡到下一个定时器）", len(log), 1)
check("  无定时器时 tvp = None（无限等待）", log[0], ("poll", None))

el, clock, log = make_loop(now=0)
check("无定时器时 usUntilEarliestTimer = -1 → tvp 保持 None", ae_process_events(el, AE_TIME_EVENTS), 0)
check("  tvp 仍是 None", log[0], ("poll", None))

# ---------------------------------------------------------------- beforesleep / aftersleep
def run_with_sleep(flags, el_flags=0, beforesleep_mutate=None):
    el, clock, log = make_loop(now=0)

    def bs(loop):
        log.append("beforesleep")
        if beforesleep_mutate is not None:
            loop.flags = beforesleep_mutate

    def as_(loop):
        log.append("aftersleep")

    el.beforesleep = bs
    el.aftersleep = as_
    el.flags = el_flags
    ae_create_file_event(el, 1, AE_READABLE, recorder("file", log), None)
    ae_process_events(el, flags)
    return log


lg = run_with_sleep(AE_ALL_EVENTS | AE_CALL_BEFORE_SLEEP | AE_CALL_AFTER_SLEEP)
check("首项是 beforesleep", lg[0], "beforesleep")
check("第二项是 poll（即 poll 夹在 beforesleep 与 aftersleep 之间）", lg[1][0], "poll")
check("末项是 aftersleep", lg[-1], "aftersleep")

lg = run_with_sleep(AE_ALL_EVENTS)
check("不带 CALL_BEFORE_SLEEP → 不调 beforesleep", "beforesleep" in lg, False)
check("不带 CALL_AFTER_SLEEP → 不调 aftersleep", "aftersleep" in lg, False)

lg = run_with_sleep(AE_ALL_EVENTS | AE_CALL_BEFORE_SLEEP | AE_CALL_AFTER_SLEEP, beforesleep_mutate=AE_DONT_WAIT)
check("beforesleep 里设 AE_DONT_WAIT → tvp=0", lg[1], ("poll", 0))

lg = run_with_sleep(AE_ALL_EVENTS | AE_DONT_WAIT | AE_CALL_BEFORE_SLEEP | AE_CALL_AFTER_SLEEP, beforesleep_mutate=0)
check("参数带 DONT_WAIT 而 beforesleep 想清掉 → 参数优先，tvp=0", lg[1], ("poll", 0))

lg = run_with_sleep(AE_ALL_EVENTS | AE_CALL_BEFORE_SLEEP, el_flags=AE_DONT_WAIT)
check("参数无 DONT_WAIT 但 el.flags 有 → tvp=0", lg[1], ("poll", 0))

# ---------------------------------------------------------------- 关闭 FILE 事件
el, clock, log = make_loop(ready=[(1, AE_READABLE)], now=0)
ae_create_file_event(el, 1, AE_READABLE, recorder("r", log), None)
n = ae_process_events(el, AE_TIME_EVENTS)
check("flags 无 AE_FILE_EVENTS → 就绪事件被丢弃（processed 只算时间事件）", n, 0)
check("  回调未被调用", "r" in log, False)
check("  但 poll 仍发生了", log[0][0], "poll")

# ---------------------------------------------------------------- 读写顺序与 AE_BARRIER
def run_rw(barrier):
    el, clock, log = make_loop(ready=[(7, AE_READABLE | AE_WRITABLE)], now=0)
    ae_create_file_event(el, 7, AE_READABLE, recorder("r", log), None)
    ae_create_file_event(el, 7, AE_WRITABLE, recorder("w", log), None)
    if barrier:
        el.events[7].mask |= AE_BARRIER
    ae_process_events(el, AE_FILE_EVENTS | AE_DONT_WAIT)
    return [x for x in log if isinstance(x, str)]


check("无 BARRIER：先读后写", run_rw(False), ["r", "w"])
check("有 BARRIER：先写后读", run_rw(True), ["w", "r"])


def run_same_proc(barrier):
    el, clock, log = make_loop(ready=[(7, AE_READABLE | AE_WRITABLE)], now=0)
    shared = recorder("shared", log)
    ae_create_file_event(el, 7, AE_READABLE, shared, None)
    ae_create_file_event(el, 7, AE_WRITABLE, shared, None)
    if barrier:
        el.events[7].mask |= AE_BARRIER
    ae_process_events(el, AE_FILE_EVENTS | AE_DONT_WAIT)
    return [x for x in log if isinstance(x, str)]


check("同一 proc 且无 BARRIER：只读一次（写被 fired 去重抑制）", run_same_proc(False), ["shared"])
check("同一 proc 且有 BARRIER：只写一次（读被抑制）", run_same_proc(True), ["shared"])
check("  两种情形调用次数相同，都是 1", len(run_same_proc(True)), 1)

# ---------------------------------------------------------------- 回调内改 mask 的重新检查
el, clock, log = make_loop(ready=[(7, AE_READABLE | AE_WRITABLE)], now=0)


def r_kills_w(el_, fd, data, mask):
    log.append("r")
    ae_delete_file_event(el_, fd, AE_WRITABLE)


ae_create_file_event(el, 7, AE_READABLE, r_kills_w, None)
ae_create_file_event(el, 7, AE_WRITABLE, recorder("w", log), None)
ae_process_events(el, AE_FILE_EVENTS | AE_DONT_WAIT)
check("读回调里删掉 WRITABLE → 写回调不再触发", [x for x in log if isinstance(x, str)], ["r"])

el, clock, log = make_loop(ready=[(7, AE_READABLE | AE_WRITABLE)], now=0)


def r_kills_r(el_, fd, data, mask):
    log.append("r")
    ae_delete_file_event(el_, fd, AE_READABLE)


ae_create_file_event(el, 7, AE_READABLE, r_kills_r, None)
ae_create_file_event(el, 7, AE_WRITABLE, recorder("w", log), None)
ae_process_events(el, AE_FILE_EVENTS | AE_DONT_WAIT)
check("读回调里删掉 READABLE → 不影响同轮写回调", [x for x in log if isinstance(x, str)], ["r", "w"])

print(f"\nae.c 文件事件自检：{PASS} 条通过，{FAIL} 条失败")
if FAIL:
    raise SystemExit(1)
