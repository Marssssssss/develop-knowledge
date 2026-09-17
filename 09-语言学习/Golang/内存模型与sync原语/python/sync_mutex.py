#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync.Mutex 的状态机模型（对齐 Go 1.24 `internal/sync/mutex.go`）。

state 位布局（源码 `1 << iota`）：
    bit0 mutexLocked = 1
    bit1 mutexWoken  = 2
    bit2 mutexStarving = 4
    bit3.. 等待者计数（mutexWaiterShift = 3）
官方注释（逐字要点）：
  * 正常模式：等待者 FIFO 排队，但被唤醒的等待者**不拥有** mutex，须与新到的 goroutine 竞争
    （新到的正在 CPU 上跑，胜率占优）；输了的等待者被插回**队首**。
  * 某个等待者连续失败超过 **1ms**（`starvationThresholdNs = 1e6`）→ 切换饥饿模式。
  * 饥饿模式：所有权由 Unlock 者**直接移交**给队首等待者；新到者既不抢也不自旋，直接排到队尾。
  * 离开饥饿模式的条件：拿到所有权的等待者发现**自己是最后一个等待者**，或**等待时间 < 1ms**。
  * 正常模式性能显著更好；饥饿模式用于抑制尾延迟的长尾。
被 python/main.py 以 `from sync_mutex import *` 使用。
"""

LOCKED = 1 << 0
WOKEN = 1 << 1
STARVING = 1 << 2
WAITER_SHIFT = 3
STARVATION_THRESHOLD_NS = 10 ** 6        # 1ms


class MutexFatal(Exception):
    """对应源码里的 fatal() / throw()：不可恢复的运行时错误。"""


class Waiter:
    __slots__ = ("gid", "wait_start", "starving", "awoke", "iter", "queue_lifo", "counted")

    def __init__(self, gid):
        self.gid = gid
        self.wait_start = 0
        self.starving = False
        self.awoke = False
        self.iter = 0
        self.queue_lifo = False
        self.counted = False        # 是否已被计入 state 的等待者计数（避免重复计数）


def can_spin(iter, max_spin=4):
    """runtime_canSpin 的简化：有自旋预算且未达上限。"""
    return iter < max_spin


class Mutex:
    def __init__(self, max_spin=4):
        self.state = 0
        self.queue = []                  # FIFO 等待队列（gid）
        self.wmap = {}                   # gid -> Waiter（用于唤醒时回写 awoke/counted）
        self.max_spin = max_spin
        self.spin_events = 0
        self.handoffs = 0
        self.barges = 0
        self.lifo_requeues = 0
        self.log = []

    # ------------------------------------------------------------ 只读视图
    @property
    def locked(self):
        return bool(self.state & LOCKED) or bool(self.state & STARVING)

    @property
    def waiters(self):
        return self.state >> WAITER_SHIFT

    @property
    def starving(self):
        return bool(self.state & STARVING)

    # ------------------------------------------------------------ 尝试加锁
    def try_lock(self):
        """TryLock：已加锁或饥饿模式 → false（且**不建立任何同步关系**）。"""
        if self.state & (LOCKED | STARVING):
            return False
        self.state |= LOCKED
        return True

    def lock(self, gid, now, w=None):
        """返回 (是否拿到锁, 是否需要阻塞)。w 为等待者的可变状态（首次为 None）。"""
        if w is not None:
            self.wmap[gid] = w
        if self.state & (LOCKED | STARVING):
            return self.lock_slow(gid, now, w)
        # 快路径：CAS(0, LOCKED)，含"新到者抢在等待者被唤醒之前拿到锁"
        self.state |= LOCKED
        if gid not in self.queue and self.waiters > 0:
            self.barges += 1
            self.log.append("t=%d G%d 快路径 CAS 抢到锁（越过了等待者）" % (now, gid))
        return True, False

    def lock_slow(self, gid, now, w):
        """cas 失败后的慢路径：自旋 → 计数等待者 →（可能）切饥饿模式 → 排队。"""
        if w is None:
            w = Waiter(gid)
        # 源码里 awoke 的清除发生在**下一轮循环**（自旋分支会 continue）；
        # 本模型是单趟执行，故用入口快照区分「本轮才认领」与「上一轮被唤醒」。
        was_awoke = w.awoke
        # 自旋：仅在「已加锁但未饥饿」时才有意义（饥饿模式所有权靠移交，抢不到）
        if (self.state & (LOCKED | STARVING)) == LOCKED and can_spin(w.iter, self.max_spin):
            # 源码条件：!awoke && 未置 WOKEN && 已有等待者 → 才认领唤醒权
            if (not w.awoke) and not (self.state & WOKEN) and self.waiters != 0:
                self.state |= WOKEN
                w.awoke = True
            self.spin_events += 1
            w.iter += 1
            self.log.append("t=%d G%d 自旋（iter=%d）" % (now, gid, w.iter))
        new = self.state
        if not (self.state & STARVING):
            new |= LOCKED                    # 非饥饿模式才尝试拿锁
        if not w.counted:
            new += 1 << WAITER_SHIFT         # 首次排队才计入等待者
            w.counted = True
        if w.starving and self.state & LOCKED:
            new |= STARVING                  # 只有当前确实被锁住才切饥饿模式
        if was_awoke:
            if not (new & WOKEN):
                raise MutexFatal("sync: inconsistent mutex state")
            new &= ~WOKEN
            w.awoke = False
        self.state = new
        # 排队：之前等过的插队首（LIFO 重排），首次到达排队尾
        w.queue_lifo = w.wait_start != 0
        if w.wait_start == 0:
            w.wait_start = now
        if gid not in self.queue:
            if w.queue_lifo:
                self.queue.insert(0, gid)
                self.lifo_requeues += 1
            else:
                self.queue.append(gid)
        w.starving = w.starving or (now - w.wait_start > STARVATION_THRESHOLD_NS)
        self.log.append("t=%d G%d 排队（%s，等待者=%d%s）"
                        % (now, gid, "队首/重排" if w.queue_lifo else "队尾",
                           self.waiters, "，已置饥饿" if w.starving else ""))
        return False, True

    # ------------------------------------------------------------ 解锁
    def unlock(self, now):
        new = self.state - LOCKED
        if new & LOCKED == LOCKED:               # 未加锁就解锁
            raise MutexFatal("sync: unlock of unlocked mutex")
        self.state = new
        if new != 0:
            return self.unlock_slow(now)
        return None

    def unlock_slow(self, now):
        if not (self.state & STARVING):
            if self.waiters == 0 or self.state & (LOCKED | WOKEN | STARVING):
                return None
            self.state = (self.state - (1 << WAITER_SHIFT)) | WOKEN
            who = self.queue.pop(0) if self.queue else None
            if who is not None and who in self.wmap:
                self.wmap[who].counted = False   # 唤醒时已减计数，稍后若重排会重新计入
                self.wmap[who].awoke = True      # 被唤醒者下次 CAS 时负责清 WOKEN
            self.log.append("t=%d 正常模式唤醒 G%s（它须与新到者竞争）" % (now, who))
            return who
        # 饥饿模式：直接移交所有权，且让出时间片
        self.handoffs += 1
        who = self.queue.pop(0) if self.queue else None
        if who is not None:
            self.queue.insert(0, who)            # 由 acquire_handoff 消费
        self.log.append("t=%d 饥饿模式**移交**所有权给 G%s" % (now, who))
        return who

    def acquire_handoff(self, gid, now, w):
        """饥饿模式下被移交的等待者：设置 LOCKED、退出等待者计数、按条件退出饥饿模式。"""
        if self.state & (LOCKED | WOKEN) or self.waiters == 0:
            raise MutexFatal("sync: inconsistent mutex state")
        delta = LOCKED - (1 << WAITER_SHIFT)
        w.counted = False
        leaving = (not w.starving) or self.waiters == 1
        if leaving:
            delta -= STARVING                    # 最后一个等待者 / 等得不久 → 退出饥饿模式
        self.state += delta
        if self.queue and self.queue[0] == gid:
            self.queue.pop(0)
        return leaving
