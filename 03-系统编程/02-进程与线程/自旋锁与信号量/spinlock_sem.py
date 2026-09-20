"""自旋锁与 POSIX 信号量的可执行模型。

依据（本轮实读）：
  * IEEE Std 1003.1-2017 pthread_spin_lock / pthread_spin_trylock
      - 持有者再次上锁 **undefined**（Issue 7 已把 EDEADLK 从「必须」降级为 UB）
      - trylock 被持有时 **shall fail** with EBUSY
      - 二者 **shall not return EINTR**
      - APPLICATION USAGE 明文：*"Applications using this function may be subject to priority inversion"*
  * sem_overview(7)：值永不为负；命名信号量 /somename，名字最长 NAME_MAX-4 = 251，
    落在 /dev/shm 下形如 sem.somename；**内核持久性**（不 unlink 就活到关机）
  * sem_wait(3)：值为 0 时阻塞；*on error, the value of the semaphore is left unchanged*；
    trywait 返回 EAGAIN；sem_post 可在信号处理器里调用（async-signal-safe）

模型口径（官方未定量处显式标注）：
  * 「一个 tick」= 一次被调度上 CPU 的机会；自旋不计睡眠、不主动让出；
  * 抢占 quantum=0 表示不可抢占（自旋者永不放手），这正是单核自旋死锁的成因。
"""

EAGAIN, EBUSY, EINTR, ENOENT, EINVAL = 11, 16, 4, 2, 22
NAME_MAX = 255


class Raised:
    def __init__(self, errno):
        self.errno = errno

    def is_err(self):
        return True


class Blocked:
    def __init__(self, waiter):
        self.waiter = waiter

    def is_err(self):
        return False


class Ok:
    def __init__(self, value=None):
        self.value = value

    def is_err(self):
        return False


# ---------------- 自旋锁 ----------------
class TASLock:
    """test-and-set 自旋锁：谁抢到算谁的，没有排队概念。"""

    def __init__(self):
        self.held_by = None
        self.spins = 0
        self.log = []

    def trylock(self, tid):
        if self.held_by is None:
            self.held_by = tid
            self.log.append(tid)
            return True
        return False

    def lock(self, tid, budget=None):
        n = 0
        while not self.trylock(tid):
            self.spins += 1
            n += 1
            if budget is not None and n > budget:
                return "deadlock"      # 递归上锁 / 单核死等的观测点
        return True

    def unlock(self, tid):
        self.held_by = None
        return True


class TicketLock:
    """排队自旋锁：先取号再等叫号，先来后到。"""

    def __init__(self):
        self.next_ticket = 0
        self.now_serving = 0
        self.spins = 0
        self.log = []

    def take_ticket(self, tid):
        t = self.next_ticket
        self.next_ticket += 1
        return t

    def acquire_with(self, tid, ticket):
        while self.now_serving != ticket:
            self.spins += 1
        self.log.append(tid)

    def unlock(self):
        self.now_serving += 1


def contest(lock_kind, nthreads, order):
    """nthreads 个线程按 0..n-1 顺序到达争抢；随后按 order 指定的顺序被调度尝试。
    返回 (获取顺序, 插队次数, 空转次数)。插队 = 晚到者反而先拿到锁。"""
    acquired = []
    if lock_kind == "tas":
        lock = TASLock()
        remaining = list(order)
        while remaining:
            tid = remaining.pop(0)
            lock.trylock(tid)
            acquired.append(tid)
            lock.unlock(tid)
            lock.spins += len(remaining)      # 没轮到的继续空转
    else:
        lock = TicketLock()
        tickets = {tid: lock.take_ticket(tid) for tid in range(nthreads)}
        remaining = list(order)
        while remaining:
            tid = remaining[0]
            if tickets[tid] == lock.now_serving:
                lock.acquire_with(tid, tickets[tid])
                acquired.append(tid)
                lock.unlock()
                remaining.pop(0)
            else:
                remaining.append(remaining.pop(0))   # 没叫到号，转下一轮
                lock.spins += 1
                if lock.spins > 1000:
                    break
    pos = {t: i for i, t in enumerate(acquired)}
    overtakes = sum(1 for a in acquired for b in acquired if a < b and pos[a] > pos[b])
    return acquired, overtakes, lock.spins


def single_cpu(quantum, hold_ticks, max_ticks=10000):
    """单核：持有者还需 hold_ticks 个 tick 出临界区，自旋者占着 CPU 空转。
    quantum=0 表示不可抢占。返回 (持有者是否完成, 经过 tick, 空转 tick)。"""
    ticks = prog = spins = 0
    while ticks < max_ticks:
        if quantum == 0:
            ticks += 1
            spins += 1
            continue
        for _ in range(quantum):          # 自旋者的时间片
            ticks += 1
            spins += 1
            if ticks >= max_ticks:
                return False, ticks, spins
        for _ in range(quantum):          # 轮转到持有者
            ticks += 1
            prog += 1
            if prog >= hold_ticks:
                return True, ticks, spins
    return False, ticks, spins


def priority_inversion(pi_aware, hold_ticks, medium_ticks):
    """L(低) 持锁、H(高) 自旋、M(中) 可运行。返回 (H 等到锁的 tick, M 实际跑的 tick)。"""
    ticks = prog = 0
    medium_left = medium_ticks
    while True:
        ticks += 1
        eff_L = 10 if pi_aware else 1        # PI：持有者被抬到等待者的优先级
        if medium_left > 0 and eff_L < 5:    # 中优先级抢占了没被提升的持有者
            medium_left -= 1
            continue
        prog += 1
        if prog >= hold_ticks:
            return ticks, medium_ticks - medium_left


# ---------------- POSIX 信号量 ----------------
class Waiter:
    def __init__(self, tid):
        self.tid = tid
        self.woken = False


class Semaphore:
    """值永不为负的计数信号量。"""

    def __init__(self, value=0, name=None):
        self.value = value
        self.name = name
        self.queue = []

    def wait(self, tid=0, signal=False):
        if signal and self.value == 0:
            return Raised(EINTR)             # on error：值保持不变，不减
        if self.value > 0:
            self.value -= 1
            return Ok("acquired")
        wt = Waiter(tid)
        self.queue.append(wt)
        return Blocked(wt)                   # 值为 0：阻塞

    def trywait(self):
        if self.value > 0:
            self.value -= 1
            return Ok("acquired")
        return Raised(EAGAIN)

    def post(self):
        """值 +1；若有等待者则唤醒其一（被唤醒者随即把它拿走）。"""
        self.value += 1
        if self.queue:
            wt = self.queue.pop(0)
            wt.woken = True
            self.value -= 1
            return Ok(wt.tid)
        return Ok(None)

    def getvalue(self):
        return self.value


def valid_named_sem(name):
    """sem_overview(7)：/somename，最长 NAME_MAX-4 = 251，除开头外不含斜杠。"""
    if not name or not name.startswith("/"):
        return False
    if len(name) > NAME_MAX - 4:
        return False
    return "/" not in name[1:]


class NamedSemTable:
    """/dev/shm 下的命名信号量：内核持久 + unlink 后不能再按同名打开。"""

    def __init__(self):
        self.table = {}
        self.unlinked = set()

    def open(self, name, value=0):
        if not valid_named_sem(name):
            return Raised(EINVAL)
        if name in self.unlinked:
            return Raised(ENOENT)
        if name not in self.table:
            self.table[name] = Semaphore(value, name)
        return Ok(self.table[name])

    def unlink(self, name):
        """unlink 只是解除名字；已打开者继续可用，新打开者拿不到同一个对象。"""
        self.unlinked.add(name)
        self.table.pop(name, None)

    def shm_path(self, name):
        return "/dev/shm/sem." + name[1:]


def bounded_buffer(capacity, ops):
    """empty=capacity / full=0 / mutex=1 三信号量有界缓冲。
    返回 (生产数, 消费数, 余量, 历史最大长度, 被挡住的生产, 被挡住的消费)。"""
    empty, full, mutex = Semaphore(capacity), Semaphore(0), Semaphore(1)
    buf = []
    produced = consumed = blocked_p = blocked_c = 0
    maxlen = 0
    for op in ops:
        if op == "P":
            if empty.trywait().is_err():
                blocked_p += 1               # 满：正确实现是阻塞，不是越界写入
                continue
            mutex.wait()
            buf.append(1)
            produced += 1
            maxlen = max(maxlen, len(buf))
            mutex.post()
            full.post()
        else:
            if full.trywait().is_err():
                blocked_c += 1               # 空：正确实现是阻塞
                continue
            mutex.wait()
            buf.pop()
            consumed += 1
            mutex.post()
            empty.post()
    return produced, consumed, len(buf), maxlen, blocked_p, blocked_c
