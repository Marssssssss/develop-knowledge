"""Go 网络轮询器（netpoll）的可执行模型。

逐行转写自官方源码 src/runtime/netpoll.go 与 netpoll_epoll.go（master 分支）。
核心：每个 fd 的 pollDesc 里有 rg/wg **两个二值信号量**，取值只有
pdNil(0) / pdReady(1) / pdWait(2) / G 指针(>2) 四种，用 CAS 迁移。
本模型去掉 mutex/atomic/写屏障，只保留状态机与计数。
"""

PD_NIL = 0
PD_READY = 1
PD_WAIT = 2

# G 指针用大于 PD_WAIT 的整数表示（官方：uintptr 存 *g）
G_BASE = 1000

MODE_R = ord('r')           # 114
MODE_W = ord('w')           # 119
MODE_RW = ord('r') + ord('w')  # 233

POLL_NO_ERROR = 0
POLL_ERR_CLOSING = 1
POLL_ERR_TIMEOUT = 2
POLL_ERR_NOT_POLLABLE = 3

MAX_DEADLINE = (1 << 63) - 1


class NetpollError(Exception):
    pass


def to_int64(v):
    """把任意整数折成有符号 int64：Go 会静默回绕，Python 必须显式模拟。"""
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= (1 << 63) else v


class PollDesc(object):
    """对应 runtime.pollDesc。"""

    _next_g = G_BASE

    def __init__(self, fd):
        self.fd = fd
        self.rg = PD_NIL
        self.wg = PD_NIL
        self.closing = False
        self.rd = 0            # 读 deadline（绝对时刻；-1 表示已过期）
        self.wd = 0
        self.expired_read = False
        self.expired_write = False
        self.rrun = False      # 读 deadline timer 是否在跑
        self.wrun = False
        self.rseq = 0
        self.wseq = 0
        self.fdseq = 1
        self.parked = []       # 模型附加：被 gopark 挂起的 g
        self.ready = []        # 模型附加：被 goready 唤醒的 g

    @classmethod
    def new_g(cls):
        cls._next_g += 1
        return cls._next_g

    def gpp(self, mode):
        return "rg" if mode == MODE_R else "wg"

    def load(self, mode):
        return getattr(self, self.gpp(mode))

    def store(self, mode, v):
        setattr(self, self.gpp(mode), v)


class NetpollRuntime(object):
    """对应 netpollWaiters 等全局状态。"""

    def __init__(self):
        self.waiters = 0
        self.wake_sig = 0      # netpollWakeSig：0/1，去重 netpollBreak

    def adjust_waiters(self, delta):
        if delta != 0:
            self.waiters += delta

    def netpoll_break(self):
        """netpoll_epoll.go:83 —— CAS(0,1) 去重，避免重复写 eventfd。"""
        if self.wake_sig == 0:
            self.wake_sig = 1
            return True
        return False


def netpollcheckerr(pd, mode):
    """netpoll.go 的 netpollcheckerr。"""
    if pd.closing:
        return POLL_ERR_CLOSING
    if (mode == MODE_R and pd.expired_read) or (mode == MODE_W and pd.expired_write):
        return POLL_ERR_TIMEOUT
    return POLL_NO_ERROR


def netpollblock(rt, pd, mode, waitio=False):
    """netpoll.go 的 netpollblock：返回 True 表示 IO 已就绪。"""
    gpp = pd.gpp(mode)
    while True:
        # 消费已挂起的通知
        if getattr(pd, gpp) == PD_READY:
            setattr(pd, gpp, PD_NIL)
            return True
        if getattr(pd, gpp) == PD_NIL:
            setattr(pd, gpp, PD_WAIT)
            break
        if getattr(pd, gpp) != PD_READY:  # 不是 pdReady 也不是 pdNil
            raise NetpollError("runtime: double wait")

    # gopark(netpollblockcommit, ...)：commit 里 CAS(pdWait → gp)
    # 注意：gopark **之后**的代码只在 goroutine 被唤醒后才继续执行，
    # 所以「信号量仍是 G 指针」= 还没人唤醒，此时它依旧是阻塞态。
    if waitio or netpollcheckerr(pd, mode) == POLL_NO_ERROR:
        g = pd.new_g()
        if getattr(pd, gpp) == PD_WAIT:
            setattr(pd, gpp, g)
            rt.adjust_waiters(1)   # netpollblockcommit 里 +1
            pd.parked.append(g)
        cur = getattr(pd, gpp)
        if cur > PD_WAIT:
            return False  # 仍在阻塞

    old = getattr(pd, gpp)
    setattr(pd, gpp, PD_NIL)
    if old > PD_WAIT:
        raise NetpollError("runtime: corrupted polldesc")
    return old == PD_READY


def netpollunblock(pd, mode, ioready, delta):
    """netpoll.go 的 netpollunblock：返回被唤醒的 g（可能为 None）与新 delta。"""
    gpp = pd.gpp(mode)
    while True:
        old = getattr(pd, gpp)
        if old == PD_READY:
            return None, delta
        if old == PD_NIL and not ioready:
            # 只有真 IO 就绪才设 pdReady；超时/关闭留给 pollWait 自己检查
            return None, delta
        new = PD_NIL
        if ioready:
            new = PD_READY
        if True:  # CAS 在单线程模型里恒成功
            setattr(pd, gpp, new)
            if old == PD_WAIT:
                old = PD_NIL
            elif old != PD_NIL:
                delta -= 1
            return (old if old > PD_WAIT else None), delta


def netpollgoready(rt, pd, g, delta):
    if g is not None:
        pd.ready.append(g)
    rt.adjust_waiters(delta)


def netpollready(rt, pd, mode):
    """netpoll.go 的 netpollready：IO 就绪时由平台相关 netpoll 调用。"""
    to_run = []
    delta = 0
    if mode == MODE_R or mode == MODE_RW:
        g, delta = netpollunblock(pd, MODE_R, True, delta)
        if g is not None:
            to_run.append(g)
    if mode == MODE_W or mode == MODE_RW:
        g, delta = netpollunblock(pd, MODE_W, True, delta)
        if g is not None:
            to_run.append(g)
    rt.adjust_waiters(delta)
    return to_run, delta


def poll_runtime_poll_reset(pd, mode):
    """poll_runtime_pollReset：先查错，再清信号量。"""
    errcode = netpollcheckerr(pd, mode)
    if errcode != POLL_NO_ERROR:
        return errcode
    pd.store(mode, PD_NIL)
    return POLL_NO_ERROR


def poll_runtime_poll_wait(rt, pd, mode):
    """poll_runtime_pollWait：循环重试，直到就绪或出错。"""
    errcode = netpollcheckerr(pd, mode)
    if errcode != POLL_NO_ERROR:
        return errcode
    while not netpollblock(rt, pd, mode, False):
        errcode = netpollcheckerr(pd, mode)
        if errcode != POLL_NO_ERROR:
            return errcode
        # 注释里的场景：timeout 已唤醒我们但还没跑到，deadline 又被重置了 → 重试
    return POLL_NO_ERROR


def poll_runtime_poll_set_deadline(rt, pd, d, mode, now):
    """poll_runtime_pollSetDeadline：d<=0 表示取消，d>0 是相对时长。"""
    rd0, wd0 = pd.rd, pd.wd
    combo0 = rd0 > 0 and rd0 == wd0
    if d > 0:
        d = to_int64(d + now)   # Go 的 int64 会回绕，Python 不会，必须显式模拟
        if d <= 0:  # 回绕成负数 = 溢出
            d = MAX_DEADLINE
    if mode == MODE_R or mode == MODE_RW:
        pd.rd = d
    if mode == MODE_W or mode == MODE_RW:
        pd.wd = d
    combo = pd.rd > 0 and pd.rd == pd.wd
    if not pd.rrun:
        if pd.rd > 0:
            pd.rseq += 1
            pd.rrun = True
    elif pd.rd != rd0 or combo != combo0:
        pd.rseq += 1  # 让旧 timer 失效
        if pd.rd > 0:
            pass
        else:
            pd.rrun = False
    if not pd.wrun:
        if pd.wd > 0 and not combo:
            pd.wseq += 1
            pd.wrun = True
    elif pd.wd != wd0 or combo != combo0:
        pd.wseq += 1
        if pd.wd > 0 and not combo:
            pass
        else:
            pd.wrun = False
    # 新 deadline 落在过去 → 立刻解除阻塞
    delta = 0
    if pd.rd < 0:
        g, delta = netpollunblock(pd, MODE_R, False, delta)
        netpollgoready(rt, pd, g, 0)
    if pd.wd < 0:
        g, delta = netpollunblock(pd, MODE_W, False, delta)
        netpollgoready(rt, pd, g, 0)
    rt.adjust_waiters(delta)
    return combo


def netpolldeadlineimpl(rt, pd, seq, read, write):
    """netpoll.go 的 netpolldeadlineimpl：timer 到点的回调。"""
    if read:
        if pd.rd <= 0 or not pd.rrun:
            raise NetpollError("runtime: inconsistent read deadline")
        if seq != pd.rseq:
            return False  # 描述符被复用或 timer 被重置 → 忽略
        pd.rd = -1
        pd.expired_read = True
    if write:
        if pd.wd <= 0 or (not pd.wrun and not read):
            raise NetpollError("runtime: inconsistent write deadline")
        if seq != pd.wseq:
            return False
        pd.wd = -1
        pd.expired_write = True
    delta = 0
    if read:
        g, delta = netpollunblock(pd, MODE_R, False, delta)
        netpollgoready(rt, pd, g, 0)
    if write:
        g, delta = netpollunblock(pd, MODE_W, False, delta)
        netpollgoready(rt, pd, g, 0)
    rt.adjust_waiters(delta)
    return True


def poll_runtime_poll_unblock(rt, pd):
    """poll_runtime_pollUnblock：close 前的解阻塞。"""
    if pd.closing:
        raise NetpollError("runtime: unblock on closing polldesc")
    pd.closing = True
    pd.rseq += 1
    pd.wseq += 1
    delta = 0
    rg, delta = netpollunblock(pd, MODE_R, False, delta)
    wg, delta = netpollunblock(pd, MODE_W, False, delta)
    if pd.rrun:
        pd.rrun = False
    if pd.wrun:
        pd.wrun = False
    if rg is not None:
        pd.ready.append(rg)
    if wg is not None:
        pd.ready.append(wg)
    rt.adjust_waiters(delta)
    return rg, wg
