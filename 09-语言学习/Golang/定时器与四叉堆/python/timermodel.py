"""Go 运行时定时器堆的可执行模型。

逐行转写自官方源码 src/runtime/time.go（master 分支，Go 1.2x 之后的 per-P timers 实现）。
约定：本模块只做**单线程**建模，所有 Go 里的 mutex 都去掉，只保留语义；
`t.astate` 是 Go 里 `t.unlock()` 时刻写出的 `t.state` 原子副本，这里用 unlock() 复现。
"""

TIMER_HEAP_N = 4  # timerHeapN：四叉堆，不是二叉堆

TIMER_HEAPED = 1 << 0  # timerHeaped：在某個 P 的堆里
TIMER_MODIFIED = 1 << 1  # timerModified：t.when 已改但 heap[i].when 还没同步
TIMER_ZOMBIE = 1 << 2  # timerZombie：已被 stop，但还留在堆里

MAX_WHEN = (1 << 63) - 1  # maxWhen


class BadTimer(Exception):
    pass


def bad_timer():
    raise BadTimer("timer data corruption")


class Timer(object):
    """对应 runtime.timer。"""

    def __init__(self, when, period=0, rand=0, fn=None, name=""):
        self.when = when
        self.period = period
        self.rand = rand  # 同一时刻的随机次序，只有 fake time 才设
        self.fn = fn
        self.name = name or "t"
        self.state = 0
        self.astate = 0
        self.ts = None

    def unlock(self):
        # Go: unlock 时把 state 刷进 astate
        self.astate = self.state

    def __repr__(self):
        return "<Timer %s when=%d state=%d>" % (self.name, self.when, self.state)


class TimerWhen(object):
    """对应 timerWhen{timer, when}：堆里存的是「时刻快照」+ 指针。"""

    __slots__ = ("timer", "when")

    def __init__(self, timer, when):
        self.timer = timer
        self.when = when

    def __repr__(self):
        return "<TW %s %d>" % (self.timer.name, self.when)


def less(a, b):
    """timerWhen.less：先比 when，完全相等时再比 timer.rand（官方原文）。"""
    if a.when < b.when:
        return True
    if a.when > b.when:
        return False
    return a.timer.rand < b.timer.rand


class Timers(object):
    """对应 runtime.timers：per-P 的定时器集合。"""

    def __init__(self):
        self.heap = []
        self.zombies = 0
        self.min_when_heap = 0
        self.min_when_modified = 0
        self.fired = []  # 模型附加：记录已触发的定时器
        self.zombie_peak = 0  # 模型附加：zombie 计数的峰值（瞬时状态）
        self.siftup_steps = 0  # 模型附加：统计上浮次数
        self.siftdown_steps = 0  # 模型附加：统计下沉次数

    # ---------- 堆维护 ----------

    def sift_up(self, i):
        heap = self.heap
        if i >= len(heap):
            bad_timer()
        tw = heap[i]
        if tw.when <= 0:
            bad_timer()
        while i > 0:
            p = (i - 1) // TIMER_HEAP_N  # parent
            if not less(tw, heap[p]):
                break
            heap[i] = heap[p]
            i = p
            self.siftup_steps += 1
        if heap[i].timer is not tw.timer:
            heap[i] = tw

    def sift_down(self, i):
        heap = self.heap
        n = len(heap)
        if i >= n:
            bad_timer()
        if i * TIMER_HEAP_N + 1 >= n:
            return  # 官方的提前返回：i 已经是叶子
        tw = heap[i]
        if tw.when <= 0:
            bad_timer()
        while True:
            left = i * TIMER_HEAP_N + 1
            if left >= n:
                break
            w = tw
            c = -1
            for j, twj in enumerate(heap[left:min(left + TIMER_HEAP_N, n)]):
                if less(twj, w):
                    w = twj
                    c = left + j
            if c < 0:
                break
            heap[i] = heap[c]
            i = c
            self.siftdown_steps += 1
        if heap[i].timer is not tw.timer:
            heap[i] = tw

    def init_heap(self):
        if len(self.heap) <= 1:
            return
        # 官方：最后一个需要下沉的元素是最后一个元素的父亲 = (n-1-1)/4
        for i in range((len(self.heap) - 2) // TIMER_HEAP_N, -1, -1):
            self.sift_down(i)

    # ---------- 增删 ----------

    def add_heap(self, t):
        # 注意：官方的 addHeap 本身不置位，timerHeaped 是在调用方 maybeAdd() 里
        # `t.state |= timerHeaped` 之后才 addHeap(t) 的（time.go:718）。
        # 本模型把这一步并进 add_heap，等价于走公开的 maybeAdd 路径。
        if t.ts is not None:
            raise BadTimer("ts set in timer")
        t.state |= TIMER_HEAPED
        t.ts = self
        self.heap.append(TimerWhen(t, t.when))
        self.sift_up(len(self.heap) - 1)
        if t is self.heap[0].timer:
            self.update_min_when_heap()

    def delete_min(self):
        t = self.heap[0].timer
        if t.ts is not self:
            raise BadTimer("wrong timers")
        t.ts = None
        last = len(self.heap) - 1
        if last > 0:
            self.heap[0] = self.heap[last]
        self.heap[last] = TimerWhen(None, 0)
        self.heap = self.heap[:last]
        if last > 0:
            self.sift_down(0)
        self.update_min_when_heap()
        if last == 0:
            self.min_when_modified = 0

    # ---------- 状态位操作 ----------

    def stop(self, t):
        """t.stop()：只打标记，不从堆里摘除。返回「是否抢在触发前停下」。"""
        if t.state & TIMER_HEAPED != 0:
            t.state |= TIMER_MODIFIED
            if t.state & TIMER_ZOMBIE == 0:
                t.state |= TIMER_ZOMBIE
                self.zombies += 1
        pending = t.when > 0
        t.when = 0
        t.unlock()
        return pending

    def modify(self, t, when, period=0):
        """t.modify()：Reset 走这里；堆里的 when 快照**延后**更新。"""
        if when <= 0:
            raise BadTimer("timer when must be positive")
        if period < 0:
            raise BadTimer("timer period must be non-negative")
        t.period = period
        pending = t.when > 0
        t.when = when
        wake = False
        if t.state & TIMER_HEAPED != 0:
            t.state |= TIMER_MODIFIED
            if t.state & TIMER_ZOMBIE != 0:
                # 已被 Stop 的定时器被 Reset 复活
                self.zombies -= 1
                t.state &= ~TIMER_ZOMBIE
            if self.min_when_modified == 0 or when < self.min_when_modified:
                wake = True
                t.astate = t.state  # 官方：先刷 astate 再改 minWhenModified
                self.update_min_when_modified(when)
        t.unlock()
        return pending, wake

    def update_heap(self, t):
        """t.updateHeap()：t 必须是 heap[0]，按 state 决定删除还是下沉。"""
        if t.ts is not self or self.heap[0].timer is not t:
            bad_timer()
        if t.state & TIMER_ZOMBIE != 0:
            t.state &= ~(TIMER_HEAPED | TIMER_ZOMBIE | TIMER_MODIFIED)
            self.zombies -= 1
            self.delete_min()
            return True
        if t.state & TIMER_MODIFIED != 0:
            t.state &= ~TIMER_MODIFIED
            self.heap[0].when = t.when
            self.sift_down(0)
            self.update_min_when_heap()
            return True
        return False

    def clean_head(self):
        """ts.cleanHead()：先清堆尾 zombie（零调整），再处理堆顶。"""
        while True:
            if len(self.heap) == 0:
                return
            n = len(self.heap)
            if self.heap[n - 1].timer is not None and \
                    self.heap[n - 1].timer.astate & TIMER_ZOMBIE != 0:
                t = self.heap[n - 1].timer
                if t.state & TIMER_ZOMBIE != 0:
                    t.state &= ~(TIMER_HEAPED | TIMER_ZOMBIE | TIMER_MODIFIED)
                    t.ts = None
                    self.zombies -= 1
                    self.heap[n - 1] = TimerWhen(None, 0)
                    self.heap = self.heap[:n - 1]
                t.unlock()
                continue
            t = self.heap[0].timer
            if t.ts is not self:
                raise BadTimer("bad ts")
            if t.astate & (TIMER_MODIFIED | TIMER_ZOMBIE) == 0:
                return  # 快路径：堆顶无需调整
            if not self.update_heap(t):
                return
            t.unlock()

    def adjust(self, now, force=False):
        """ts.adjust()：Scan 全堆，清 zombie 并把 modified 的 when 同步进堆。"""
        if not force:
            first = self.min_when_modified
            if first == 0 or first > now:
                return
        self.min_when_heap = self.wake_time()
        self.min_when_modified = 0
        changed = False
        i = 0
        while i < len(self.heap):
            tw = self.heap[i]
            t = tw.timer
            if t.ts is not self:
                raise BadTimer("bad ts")
            if t.astate & (TIMER_MODIFIED | TIMER_ZOMBIE) == 0:
                i += 1
                continue
            if t.state & TIMER_HEAPED == 0:
                bad_timer()
            if t.state & TIMER_ZOMBIE != 0:
                self.zombies -= 1
                t.state &= ~(TIMER_HEAPED | TIMER_ZOMBIE | TIMER_MODIFIED)
                n = len(self.heap)
                self.heap[i] = self.heap[n - 1]
                self.heap[n - 1] = TimerWhen(None, 0)
                self.heap = self.heap[:n - 1]
                t.ts = None
                t.unlock()
                i -= 1
                changed = True
            elif t.state & TIMER_MODIFIED != 0:
                tw.when = t.when
                t.state &= ~TIMER_MODIFIED
                t.unlock()
                changed = True
            i += 1
        if changed:
            self.init_heap()
        self.update_min_when_heap()

    # ---------- 触发 ----------

    def unlock_and_run(self, now, t):
        """t.unlockAndRun()：算下一次时刻、改状态、跑回调。"""
        delay = now - t.when
        if t.period > 0:
            # 官方：next = when + period*(1 + delay/period)，整数除法
            nxt = t.when + t.period * (1 + delay // t.period)
            if nxt < 0:
                nxt = MAX_WHEN
        else:
            nxt = 0
        t.when = nxt
        if t.state & TIMER_HEAPED != 0:
            t.state |= TIMER_MODIFIED
            if nxt == 0:
                t.state |= TIMER_ZOMBIE
                self.zombies += 1
                self.zombie_peak = max(self.zombie_peak, self.zombies)
            self.update_heap(t)
        t.unlock()
        self.fired.append((t.name, now, delay))
        if t.fn is not None:
            t.fn(now, delay)
        return 0

    def run(self, now):
        """ts.run()：返回值语义与官方一致（-1 空堆 / tw.when 未到 / 0 已触发）。"""
        while True:
            if len(self.heap) == 0:
                return -1
            tw = self.heap[0]
            t = tw.timer
            if t.ts is not self:
                raise BadTimer("bad ts")
            if t.astate & (TIMER_MODIFIED | TIMER_ZOMBIE) == 0 and tw.when > now:
                return tw.when
            if self.update_heap(t):  # 官方：t 加锁 → updateHeap → 解锁 → goto Redo
                t.unlock()
                continue
            if t.when > now:
                t.unlock()
                return t.when
            return self.unlock_and_run(now, t)

    # ---------- 最小值维护 ----------

    def update_min_when_heap(self):
        self.min_when_heap = 0 if len(self.heap) == 0 else self.heap[0].when

    def update_min_when_modified(self, when):
        old = self.min_when_modified
        if old != 0 and old < when:
            return
        self.min_when_modified = when

    def wake_time(self):
        m = self.min_when_modified
        if m != 0:
            return m
        return self.min_when_heap
