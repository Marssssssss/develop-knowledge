"""Go 运行时定时器堆的**堆维护**部分（四叉堆）。

从 timermodel.py 拆出：常量、timer/timerWhen、less、siftUp/siftDown/initHeap、
addHeap/deleteMin 与 minWhen 维护。状态位操作见 timermodel.py。
来源同前：src/runtime/time.go。
"""

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


class TimerHeap(object):
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
