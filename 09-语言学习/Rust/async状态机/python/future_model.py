"""Future / Waker / Executor / Pin 的可运行模型。

依据：
  * std::future::Future —— "Futures alone are **inert**; they must be actively
    `poll`ed for the underlying computation to make progress."
  * 同上 —— "on multiple calls to poll, only the **Waker from the Context passed
    to the most recent call** should be scheduled to receive a wakeup."
  * 同上 —— "Once a future has finished, clients should not poll it again."
  * async-book 02_execution/02_future —— SimpleFuture 的 `poll(&mut self, wake: fn())`、
    真实签名改为 `poll(self: Pin<&mut Self>, cx: &mut Context<'_>)` 的两个理由
    （允许不可移动的自引用 future；fn() 记不住是谁在等，所以要 Waker）、
    Join / AndThenFut 的 "allocation-free state machines" 写法。
  * std::pin —— pinning 用于 self-referential types；`PhantomPinned` 用来摘掉自动 Unpin。
"""

# ---------------------------------------------------------------- Poll

def ready(v):
    return ("Ready", v)


def pending():
    return ("Pending", None)


def is_ready(p):
    return p[0] == "Ready"


# ---------------------------------------------------------------- Waker / 反应器

class Waker:
    """`Waker` = 唤醒**某个具体 task** 的句柄。

    async-book 原文：`wake: fn()` 是个纯函数指针，"it can't store any data about
    which Future called wake"；真实实现换成 `Context` 提供 `Waker`，
    才可能在上千个连接里只唤醒该唤醒的那个。
    """

    def __init__(self, name, on_wake):
        self.name = name
        self.on_wake = on_wake
        self.calls = 0

    def wake(self):
        self.calls += 1
        self.on_wake()

    def __repr__(self):
        return f"Waker({self.name})"


class Context:
    def __init__(self, waker, reactor):
        self.waker = waker
        self.reactor = reactor


class Reactor:
    """模拟事件源。它只保存**最近一次** poll 传进来的那个 Waker。"""

    def __init__(self):
        self.now = 0
        self.store = {}          # id(timer) -> (timer, waker)
        self.wake_count = 0

    def bind(self, timer, waker):
        self.store[id(timer)] = (timer, waker)

    def advance(self, n=1):
        """时间前进；到点的 timer 唤醒它**最近一次**登记的 waker。"""
        self.now += n
        for key in list(self.store):
            timer, waker = self.store[key]
            if timer.deadline <= self.now:
                del self.store[key]
                self.wake_count += 1
                waker.wake()

    def pending_count(self):
        return len(self.store)


# ---------------------------------------------------------------- 叶子 Future

class Timer:
    """async-book 的 SocketRead 类比：没数据就登记回调并返回 Pending。"""

    def __init__(self, reactor, ticks, label="timer"):
        self.reactor = reactor
        self.deadline = reactor.now + ticks
        self.label = label
        self.polls = 0
        self.registrations = 0

    def poll(self, cx):
        self.polls += 1
        cx.reactor.bind(self, cx.waker)      # 覆盖式登记：只保留最近一次
        self.registrations += 1
        if cx.reactor.now >= self.deadline:
            return ready(f"{self.label}@t{cx.reactor.now}")
        return pending()


# ---------------------------------------------------------------- 组合子

class Join:
    """async-book 的 Join：两个 future 并发跑完。

    关键细节（原文注释）：完成的字段被设成 `None`，
    "This prevents us from polling a future after it has completed, which would
    violate the contract of the Future trait."
    """

    def __init__(self, a, b):
        self.a, self.b = a, b
        self.ra = self.rb = None

    def poll(self, cx):
        done = True
        if self.a is not None:
            r = self.a.poll(cx)
            if is_ready(r):
                self.ra = r[1]
                self.a = None
            else:
                done = False
        if self.b is not None:
            r = self.b.poll(cx)
            if is_ready(r):
                self.rb = r[1]
                self.b = None
            else:
                done = False
        return ready((self.ra, self.rb)) if done else pending()


class AndThen:
    """async-book 的 AndThenFut：先跑完 first 再跑 second（顺序状态机）。"""

    def __init__(self, first, second):
        self.first = first
        self.second = second

    def poll(self, cx):
        if self.first is not None:
            r = self.first.poll(cx)
            if is_ready(r):
                self.first = None
            else:
                return pending()          # 注意这里用 return 打断流程
        return self.second.poll(cx)


# ---------------------------------------------------------------- Executor

class Executor:
    """最小执行器：Pending 的 task **不会**被自动重新入队，只等 waker 把它放回来。"""

    def __init__(self, reactor):
        self.reactor = reactor
        self.ready = []
        self.tasks = {}
        self.results = {}
        self.polls = 0
        self.spurious_wakes = 0
        self._next = 0

    def spawn(self, fut):
        tid = self._next
        self._next += 1
        self.tasks[tid] = fut
        self.ready.append(tid)
        return tid

    def enqueue(self, tid):
        if tid in self.tasks:
            self.ready.append(tid)
        else:
            self.spurious_wakes += 1       # 已完成的任务被唤醒 —— 应当被忽略

    def run(self, max_rounds=10_000, drive_reactor=True):
        rounds = 0
        while self.tasks and rounds < max_rounds:
            rounds += 1
            if not self.ready:
                if not drive_reactor or self.reactor.pending_count() == 0:
                    break                  # 没人会再唤醒 → 永久挂起
                self.reactor.advance()
                continue
            tid = self.ready.pop(0)
            fut = self.tasks[tid]
            waker = Waker(f"t{tid}", lambda tid=tid: self.enqueue(tid))
            r = fut.poll(Context(waker, self.reactor))
            self.polls += 1
            if is_ready(r):
                self.results[tid] = r[1]
                del self.tasks[tid]
        return rounds


def busy_poll(fut, reactor, limit=10_000):
    """对照：没有 waker 时执行器只能每轮轮询每个 future（async-book 原文说的
    "would have to be constantly polling every future"）。"""
    polls = 0
    sink = []
    waker = Waker("busy", lambda: sink.append(1))
    while polls < limit:
        polls += 1
        r = fut.poll(Context(waker, reactor))
        if is_ready(r):
            return polls, r[1]
        reactor.advance()
    return polls, None


# ---------------------------------------------------------------- Pin

class SelfRefFut:
    """async-book 的 `struct MyFut { a: i32, ptr_to_a: *const i32 }`。

    用 slot（模拟地址）+ slot_at_construction（模拟那个自引用指针）来建模：
    一旦对象被"搬"到新地址，ptr_to_a 就与真实地址不符 = 悬垂。
    """

    UNPIN = False                     # 有 PhantomPinned 字段 → 不再是 Unpin

    def __init__(self, a, slot):
        self.slot = slot
        self.a = a
        self.ptr_to_a = slot          # 自引用

    def pointer_is_valid(self):
        return self.ptr_to_a == self.slot

    def moved_to(self, new_slot):
        """模拟一次 move：逐字段拷贝，指针字段也被原样拷过去。"""
        clone = SelfRefFut(self.a, new_slot)
        clone.ptr_to_a = self.ptr_to_a     # 指针值是拷贝来的，仍指向旧地址
        return clone


class PlainFut:
    """不含自引用的普通 future：是 Unpin，搬动无所谓。"""

    UNPIN = True

    def __init__(self, v, slot=0):
        self.slot = slot
        self.v = v

    def moved_to(self, new_slot):
        clone = PlainFut(self.v, new_slot)
        return clone


class PinBox:
    """`Pin<&mut T>` 的最小模型：能拿到 &mut，但拿不出所有权。"""

    def __init__(self, obj):
        self._obj = obj

    def as_mut(self):
        return self._obj

    def move_out(self):
        if getattr(self._obj, "UNPIN", False):
            return self._obj                       # T: Unpin → 允许搬
        raise RuntimeError(
            f"cannot move out of pinned value: "
            f"{type(self._obj).__name__} does not implement Unpin")
