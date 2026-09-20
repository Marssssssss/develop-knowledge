"""Linux futex 原语的可执行模型。

依据（均为本轮实读）：
  * futex(2) / futex(7) / FUTEX_WAIT(2const) 手册页
  * include/uapi/linux/futex.h —— FUTEX_WAITERS / FUTEX_OWNER_DIED / FUTEX_TID_MASK / FUTEX_OP() 宏
  * kernel/futex/waitwake.c   —— futex_atomic_op_inuser() 的解码口径与 futex_wake_op() 的唤醒顺序

模型口径（凡官方未定量处，此处显式标注）：
  * futex word 一律 32 位无符号，写入即 & 0xFFFFFFFF；
  * 「阻塞」用 Waiter 对象表示：futex_wait 成功返回 Blocked(waiter)，被唤醒后 waiter.woken 置真；
  * 时间用离散时钟：kernel.advance(dt) 推进并结算超时。FUTEX_WAIT 的 timeout 是相对值，
    其它操作的 timeout 是绝对值（手册 CAVEATS 明文），本模型按同样口径实现。
"""

from collections import defaultdict

MASK32 = 0xFFFFFFFF
FUTEX_WAITERS = 0x80000000
FUTEX_OWNER_DIED = 0x40000000
FUTEX_TID_MASK = 0x3FFFFFFF
FUTEX_OP_OPARG_SHIFT = 8  # 该位在 op 字段的最高位（<<28 后为 0x80000000）

# 本模型用到的 errno（Linux x86-64 值）
EAGAIN, EINTR, EPERM, ETIMEDOUT, EINVAL = 11, 4, 1, 110, 22

# FUTEX_OP_*（uapi/linux/futex.h）
OP_SET, OP_ADD, OP_OR, OP_ANDN, OP_XOR = 0, 1, 2, 3, 4
CMP_EQ, CMP_NE, CMP_LT, CMP_LE, CMP_GT, CMP_GE = 0, 1, 2, 3, 4, 5


def futex_op_encode(op, oparg, cmp_, cmparg):
    """与 uapi/linux/futex.h 的 FUTEX_OP() 宏逐位一致。"""
    return (((op & 0xF) << 28) | ((cmp_ & 0xF) << 24)
            | ((oparg & 0xFFF) << 12) | (cmparg & 0xFFF))


def sign_extend32(v, bits):
    sign = 1 << bits
    return (v & (sign - 1)) - (v & sign)


def futex_op_decode(encoded):
    """与 kernel/futex/waitwake.c::futex_atomic_op_inuser() 逐行一致。"""
    op = (encoded & 0x70000000) >> 28           # 只用低 3 位
    cmp_ = (encoded & 0x0F000000) >> 24
    oparg = sign_extend32((encoded & 0x00FFF000) >> 12, 11)
    if encoded & (FUTEX_OP_OPARG_SHIFT << 28):  # 0x80000000
        oparg = 1 << (oparg & 31)
    return op, oparg, cmp_, encoded & 0xFFF


def futex_op_apply(op, oparg, old):
    """arch_futex_atomic_op_inuser()：oldval OP OPARG。"""
    if op == OP_SET:
        return oparg & MASK32
    if op == OP_ADD:
        return (old + oparg) & MASK32
    if op == OP_OR:
        return (old | oparg) & MASK32
    if op == OP_ANDN:
        return (old & ~oparg) & MASK32
    if op == OP_XOR:
        return (old ^ oparg) & MASK32
    raise ValueError("bad futex op %d" % op)


def futex_op_cmp(cmp_, old, cmparg):
    return {CMP_EQ: old == cmparg, CMP_NE: old != cmparg, CMP_LT: old < cmparg,
            CMP_LE: old <= cmparg, CMP_GT: old > cmparg, CMP_GE: old >= cmparg}[cmp_]


class Raised:
    """系统调用返回 -1 并设置 errno。"""

    def __init__(self, errno):
        self.errno = errno

    def is_err(self):
        return True


class Blocked:
    """系统调用把调用者挂进了等待队列。"""

    def __init__(self, waiter):
        self.waiter = waiter

    def is_err(self):
        return False


class Ok:
    """系统调用成功返回。"""

    def __init__(self, value):
        self.value = value

    def is_err(self):
        return False


class FutexWord:
    """一个 4 字节对齐的 futex word；不同进程里虚拟地址可不同，内核按物理页+偏移建 key。"""

    def __init__(self, value=0, name="w"):
        self.value = value & MASK32
        self.name = name

    def set(self, v):
        self.value = v & MASK32
        return self.value

    def cas(self, expected, new):
        """用户态原子 compare-and-swap：返回是否成功（失败时把实际值写回调用方）。"""
        if self.value == expected:
            self.value = new & MASK32
            return True
        return False

    @property
    def tid(self):
        return self.value & FUTEX_TID_MASK

    @property
    def has_waiters(self):
        return bool(self.value & FUTEX_WAITERS)

    @property
    def owner_died(self):
        return bool(self.value & FUTEX_OWNER_DIED)

    def __repr__(self):
        return "FutexWord(%s=0x%08x)" % (self.name, self.value)


class Waiter:
    def __init__(self, tid, word, abs_deadline=None):
        self.tid = tid
        self.word = word
        self.abs_deadline = abs_deadline
        self.woken = False
        self.timedout = False
        self.requeued_to = None


class FutexKernel:
    """内核侧：只维护「哪个 word 上有哪些等待者」，其余全在用户态。"""

    def __init__(self):
        self.waitq = defaultdict(list)
        self.now = 0
        self.syscalls = 0
        self.wakeups = 0

    # ---- 基础操作 ----
    def futex_wait(self, word, val, tid, rel_timeout=None, absolute=False):
        """FUTEX_WAIT：原子「比较-并-阻塞」。值不符立即 EAGAIN（这正是防丢失唤醒的关键）。"""
        self.syscalls += 1
        if word.value != val:
            return Raised(EAGAIN)
        deadline = None
        if rel_timeout is not None:
            deadline = (rel_timeout if absolute else self.now + rel_timeout)
        wt = Waiter(tid, word, deadline)
        if deadline is not None and deadline <= self.now:
            wt.timedout = True
            return Raised(ETIMEDOUT)
        self.waitq[id(word)].append(wt)
        return Blocked(wt)

    def futex_wait_naive(self, word, tid):
        """对照组：没有「比较」这一步的朴素阻塞（会丢失唤醒）。"""
        self.syscalls += 1
        wt = Waiter(tid, word)
        self.waitq[id(word)].append(wt)
        return Blocked(wt)

    def futex_wake(self, word, n):
        """FUTEX_WAKE：唤醒至多 n 个，返回**实际唤醒数**（不是剩余等待者数）。"""
        self.syscalls += 1
        q = self.waitq[id(word)]
        woken = []
        while q and len(woken) < n:
            wt = q.pop(0)
            wt.woken = True
            woken.append(wt)
        self.wakeups += len(woken)
        return len(woken)

    def futex_requeue(self, w1, nwake, nrequeue, w2):
        """FUTEX_REQUEUE：先唤醒 nwake 个，再把 nrequeue 个搬到 w2（不校验 w1 的值）。"""
        woken = self.futex_wake(w1, nwake)
        q = self.waitq[id(w1)]
        moved = []
        while q and len(moved) < nrequeue:
            wt = q.pop(0)
            wt.requeued_to = w2
            moved.append(wt)
        self.waitq[id(w2)].extend(moved)
        return woken, len(moved)

    def futex_cmp_requeue(self, w1, nwake, nrequeue, w2, val):
        """FUTEX_CMP_REQUEUE：值不符直接 EAGAIN，一个都不搬。"""
        self.syscalls += 1
        if w1.value != val:
            return Raised(EAGAIN)
        return self.futex_requeue(w1, nwake, nrequeue, w2)

    def futex_wake_op(self, w1, nwake1, nwake2, w2, encoded):
        """FUTEX_WAKE_OP：原子地对 w2 做 OP 再按**旧值**比较；
        注意 uaddr1 上的 nwake1 个等待者是**无条件**唤醒的（waitwake.c 先唤醒再判比较）。"""
        self.syscalls += 1
        op, oparg, cmp_, cmparg = futex_op_decode(encoded)
        old = w2.value
        w2.set(futex_op_apply(op, oparg, old))
        woken1 = self.futex_wake(w1, nwake1)
        woken2 = 0
        if futex_op_cmp(cmp_, old, cmparg):
            woken2 = self.futex_wake(w2, nwake2)
        return woken1, woken2, old

    # ---- 时钟 ----
    def advance(self, dt):
        """推进时钟并结算超时；返回本次因超时被踢醒的等待者。"""
        self.now += dt
        out = []
        for q in self.waitq.values():
            for wt in list(q):
                if wt.abs_deadline is not None and wt.abs_deadline <= self.now:
                    wt.timedout = True
                    q.remove(wt)
                    out.append(wt)
        return out

    def pending(self, word):
        return len(self.waitq[id(word)])


# ---------------- futex(7) 描述的裸 futex 计数协议 ----------------
def futex_up(kernel, counter):
    """up：原子 +1；若是从 0 变 1，说明没有等待者，一次系统调用都不用做。"""
    old = counter.value
    counter.set((old + 1) & MASK32)
    if old == 0:
        return ("fast", 0)
    counter.set(1)  # 有等待者：置 1 并唤醒
    return ("wake", kernel.futex_wake(counter, 1))


def futex_down(kernel, counter, tid):
    """down：原子 -1；若变 0 就走完；否则置 -1（0xFFFFFFFF）并请求等待。"""
    old = counter.value
    counter.set((old - 1) & MASK32)
    if counter.value == 0:
        return ("fast", None)
    counter.set(0xFFFFFFFF)
    return ("wait", kernel.futex_wait(counter, 0xFFFFFFFF, tid))
