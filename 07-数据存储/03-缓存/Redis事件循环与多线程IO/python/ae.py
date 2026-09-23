"""Redis ae.c 事件循环（文件事件部分）的可执行转写。

转写对象：
  - redis/src/ae.h            （AE_* 常量、aeEventLoop / aeFileEvent 结构）
  - redis/src/ae.c            （aeCreateEventLoop / aeCreateFileEvent / aeDeleteFileEvent /
                                aeProcessEvents / aeMain）

时间事件在 ae_time.py。与 C 的差异（显式落地，见 README「注意事项」）：
  - C 用函数指针，这里用可调用对象；`proc_a == proc_b` 等价于 C 的指针相等。
  - 真实 epoll 被替换为注入的 `poll_fn`，返回本轮就绪的 (fd, mask) 列表，保证确定性。
  - 时钟由 `clock.get_monotonic_us()` 提供，测试可手动推进。
"""

from ae_const import (
    AE_ALL_EVENTS,
    AE_BARRIER,
    AE_CALL_AFTER_SLEEP,
    AE_CALL_BEFORE_SLEEP,
    AE_DELETED_EVENT_ID,
    AE_DONT_WAIT,
    AE_ERR,
    AE_FILE_EVENTS,
    AE_NONE,
    AE_NOMORE,
    AE_OK,
    AE_READABLE,
    AE_TIME_EVENTS,
    AE_WRITABLE,
    INITIAL_EVENT,
)
from ae_time import process_time_events, us_until_earliest_timer


class Clock:
    """可手动推进的单调时钟，单位微秒。"""

    def __init__(self, now=0):
        self.now = now

    def get_monotonic_us(self):
        return self.now

    def advance(self, us):
        self.now += us


class FileEvent:
    """对应 C 的 aeFileEvent。"""

    def __init__(self):
        self.mask = AE_NONE
        self.rfile_proc = None
        self.wfile_proc = None
        self.client_data = None


class FiredEvent:
    def __init__(self, fd, mask):
        self.fd = fd
        self.mask = mask


class EventLoop:
    """对应 C 的 aeEventLoop。"""

    def __init__(self, setsize, clock, poll_fn):
        self.clock = clock
        self.poll_fn = poll_fn
        self.nevents = min(setsize, INITIAL_EVENT)
        self.events = [FileEvent() for _ in range(self.nevents)]
        self.fired = []
        self.setsize = setsize
        self.time_event_head = None
        self.time_event_next_id = 0
        self.stop = 0
        self.maxfd = -1
        self.beforesleep = None
        self.aftersleep = None
        self.flags = 0
        # 观测点：poll 实际使用的超时（None 表示无限等待；"unset" 表示没进 poll）
        self.last_tvp = "unset"


def _grow_events(el, fd):
    """aeCreateFileEvent 中的扩容逻辑：容量翻倍，但不超过 setsize。"""
    newnevents = el.nevents
    newnevents = (newnevents * 2) if (newnevents * 2 > fd + 1) else (fd + 1)
    newnevents = min(newnevents, el.setsize)
    el.events = el.events + [FileEvent() for _ in range(newnevents - el.nevents)]
    el.nevents = newnevents


def ae_create_file_event(el, fd, mask, proc, client_data):
    if fd >= el.setsize:
        return AE_ERR
    if fd >= el.nevents:
        _grow_events(el, fd)
    fe = el.events[fd]
    fe.mask |= mask
    if mask & AE_READABLE:
        fe.rfile_proc = proc
    if mask & AE_WRITABLE:
        fe.wfile_proc = proc
    fe.client_data = client_data
    if fd > el.maxfd:
        el.maxfd = fd
    return AE_OK


def ae_delete_file_event(el, fd, mask):
    if fd >= el.setsize:
        return
    fe = el.events[fd]
    if fe.mask == AE_NONE:
        return
    # 删除 AE_WRITABLE 时，AE_BARRIER 一并清除
    if mask & AE_WRITABLE:
        mask |= AE_BARRIER
    fe.mask = fe.mask & (~mask)
    if fd == el.maxfd and fe.mask == AE_NONE:
        j = fd - 1
        while j >= 0:
            if el.events[j].mask != AE_NONE:
                break
            j -= 1
        el.maxfd = j


def ae_process_events(el, flags):
    """ae.c:365。返回本轮处理的文件/时间事件总数。"""
    processed = 0

    if not (flags & AE_TIME_EVENTS) and not (flags & AE_FILE_EVENTS):
        return 0

    el.last_tvp = "unset"

    if el.maxfd != -1 or ((flags & AE_TIME_EVENTS) and not (flags & AE_DONT_WAIT)):
        tvp = None  # None 表示无限等待

        if el.beforesleep is not None and (flags & AE_CALL_BEFORE_SLEEP):
            el.beforesleep(el)

        # beforesleep 可以改 el.flags，但参数 flags 优先级最高：
        # 只要参数里带了 AE_DONT_WAIT，就一定是零超时。
        if (flags & AE_DONT_WAIT) or (el.flags & AE_DONT_WAIT):
            tvp = 0
        elif flags & AE_TIME_EVENTS:
            us = us_until_earliest_timer(el)
            if us >= 0:
                tvp = us

        el.last_tvp = tvp
        fired = el.poll_fn(el, tvp)

        if not (flags & AE_FILE_EVENTS):
            fired = []

        if el.aftersleep is not None and (flags & AE_CALL_AFTER_SLEEP):
            el.aftersleep(el)

        for fe_fired in fired:
            fd = fe_fired.fd
            mask = fe_fired.mask
            fe = el.events[fd]
            n_fired = 0
            invert = fe.mask & AE_BARRIER

            if (not invert) and (fe.mask & mask & AE_READABLE):
                fe.rfile_proc(el, fd, fe.client_data, mask)
                n_fired += 1
                fe = el.events[fd]  # 回调可能触发扩容，重新取址

            if fe.mask & mask & AE_WRITABLE:
                if n_fired == 0 or fe.wfile_proc != fe.rfile_proc:
                    fe.wfile_proc(el, fd, fe.client_data, mask)
                    n_fired += 1

            if invert:
                fe = el.events[fd]
                if (fe.mask & mask & AE_READABLE) and (
                    n_fired == 0 or fe.wfile_proc != fe.rfile_proc
                ):
                    fe.rfile_proc(el, fd, fe.client_data, mask)
                    n_fired += 1

            processed += 1

    if flags & AE_TIME_EVENTS:
        processed += process_time_events(el)

    return processed


def ae_main(el, max_iterations=None):
    """ae.c:497：固定以 ALL_EVENTS|CALL_BEFORE_SLEEP|CALL_AFTER_SLEEP 循环。"""
    el.stop = 0
    n = 0
    while not el.stop:
        ae_process_events(
            el,
            AE_ALL_EVENTS | AE_CALL_BEFORE_SLEEP | AE_CALL_AFTER_SLEEP,
        )
        n += 1
        if max_iterations is not None and n >= max_iterations:
            break
    return n
