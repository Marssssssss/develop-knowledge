"""Go 运行时定时器堆的可执行模型（状态位与触发部分）。

堆维护（四叉堆的上下沉、增删、minWhen）在 timerheap.py；
本文件是 TimerHeap 的子类，实现官方 time.go 里的
stop / modify / updateHeap / cleanHead / adjust / unlockAndRun / run。
"""

from timerheap import (  # noqa: F401  （重导出，供 selfcheck 直接 import）
    TIMER_HEAP_N, TIMER_HEAPED, TIMER_MODIFIED, TIMER_ZOMBIE, MAX_WHEN,
    BadTimer, bad_timer, Timer, TimerWhen, less, TimerHeap,
)


class Timers(TimerHeap):
    """对应 runtime.timers：在四叉堆之上加了三个状态位与延迟同步语义。"""

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

    
