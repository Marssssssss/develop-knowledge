# -*- coding: utf-8 -*-
"""PI futex 与优先级继承（从 futex_model 拆出，供自检与主模型共用）。

语义见 futex_model 顶部注释，此处只承载 futex(2) 的 PI 取值策略与继承传播。
"""

from futex_model import (
    FutexWord, Waiter, Ok, Raised, Blocked,
    FUTEX_WAITERS, FUTEX_OWNER_DIED, MASK32,
    EPERM, EAGAIN,
)

# ---------------- PI futex ----------------
class PIFutex:
    """优先级继承 futex：word = 0 | TID | (FUTEX_WAITERS|TID) [| FUTEX_OWNER_DIED]。"""

    def __init__(self, kernel):
        self.kernel = kernel
        self.w = FutexWord(0, "pi")
        self.owner = None

    def trylock_userspace(self, tid):
        """无竞争：用户态 cmpxchg 0 → TID 即可，全程不进内核。"""
        if self.w.cas(0, tid):
            self.owner = tid
            return Ok("acquired")
        return Raised(EAGAIN)

    def lock_pi(self, tid):
        """竞争路径：内核置 FUTEX_WAITERS 后阻塞，唤醒时由内核把 owner 填成自己。"""
        fast = self.trylock_userspace(tid)
        if not fast.is_err():
            return fast
        self.w.set(self.w.value | FUTEX_WAITERS)
        self.kernel.syscalls += 1
        wt = Waiter(tid, self.w)
        self.kernel.waitq[id(self.w)].append(wt)
        return Blocked(wt)

    def unlock_pi(self, tid):
        """只能由持有者调用，否则 EPERM（futex(2) 明文）。"""
        self.kernel.syscalls += 1
        if self.w.tid != tid:
            return Raised(EPERM)
        q = self.kernel.waitq[id(self.w)]
        if q:
            nxt = q.pop(0)
            nxt.woken = True
            self.kernel.wakeups += 1
            self.w.set(nxt.tid | (FUTEX_WAITERS if q else 0))
            self.owner = nxt.tid
            return Ok(nxt.tid)
        self.w.set(0)
        self.owner = None
        return Ok(0)

    def owner_dies(self):
        """持有者猝死：内核置 FUTEX_OWNER_DIED 并把锁交给下一个等待者，同时唤醒它。"""
        q = self.kernel.waitq[id(self.w)]
        if not q:
            self.w.set(0)
            return None
        nxt = q.pop(0)
        nxt.woken = True
        self.kernel.wakeups += 1
        self.w.set(FUTEX_OWNER_DIED | nxt.tid | (FUTEX_WAITERS if q else 0))
        self.owner = nxt.tid
        return nxt.tid


def pi_state_valid(word):
    """futex(2)：『无主却有 FUTEX_WAITERS』是非法状态。"""
    if word.value & FUTEX_WAITERS and word.tid == 0:
        return False
    return True


# ---------------- 优先级继承的传递性 ----------------
class PIManager:
    """任务 -> 优先级；锁 -> 持有者；阻塞图 -> 传递提升。"""

    def __init__(self):
        self.prio = {}
        self.eff = {}
        self.owner = {}      # lock -> tid
        self.blocked_on = {}  # tid -> lock

    def add(self, tid, prio):
        self.prio[tid] = prio
        self.eff[tid] = prio

    def acquire(self, tid, lock):
        self.owner[lock] = tid

    def block(self, tid, lock):
        self.blocked_on[tid] = lock
        self._recompute()

    def _recompute(self):
        for t in self.prio:
            self.eff[t] = self.prio[t]
        changed = True
        while changed:  # 传递提升：沿「等待者 -> 持有者」边反复传播，直到不动点
            changed = False
            for waiter, lock in self.blocked_on.items():
                owner = self.owner.get(lock)
                if owner is None:
                    continue
                if self.eff[owner] < self.eff[waiter]:
                    self.eff[owner] = self.eff[waiter]
                    changed = True

    def highest_runnable(self):
        runnable = [t for t in self.prio if t not in self.blocked_on]
        return max(runnable, key=lambda t: (self.eff[t], t)) if runnable else None
