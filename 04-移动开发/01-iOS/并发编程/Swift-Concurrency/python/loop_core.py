#!/usr/bin/env python3
r"""协作式调度内核 —— 用生成器承载 Swift async/await 与 actor 的执行语义。

与真实 Swift 并发的对应关系(权威依据见同目录 README「参考资料」):

1. 一个 Task = 一个可被挂起的执行体。Swift 官方书明说
   "suspension is never implicit or preemptive" —— 只有 `await` 处才可能挂起。
   两个挂起点之间的一段代码是**同步段**:本模型里 `next(gen)` 一次跑完、不可被打断。
   这就是 actor 能保护数据不变量的物理基础(不是"锁",而是"不被打断")。
2. 挂起点会释放 actor(SE-0306 的 *reentrancy*):同一 actor 的其它 job 可以在
   这一刻进入并运行自己的同步段。`Actor(reentrant=False)` 用来对照"不可重入会死锁"。
3. 取消是**协作式**的:`Task.cancel()` 只置标志并级联子任务,
   只有任务自己调用 `check_cancellation()` 才会抛出 CancellationError。
4. 结构化并发:父任务持有一组子任务,必须 `await` 完它们才能结束;
   子任务优先级高于父任务时,父任务优先级被**抬高**(priority escalation)。
5. 虚拟时钟:所有 await 时长都是确定性数字,不受机器负载影响,便于断言。
"""

from __future__ import annotations

import heapq

ALL_CHILDREN = object()          # yield ("await", ALL_CHILDREN) → 等所有子任务


class CancellationError(Exception):
    """对应 Swift 的 CancellationError。"""


class Deadlock(Exception):
    """所有任务都在互相等待。真实 Swift 里表现为**永久挂起**:不报错、不崩溃、不释放资源。"""


class Task:
    """一个任务 = 一个生成器 + 一颗优先级 + 可选的所属 actor + 可选的父任务。"""

    _seq = 0

    def __init__(self, name: str, gen=None, priority: int = 5, actor: "Actor | None" = None) -> None:
        Task._seq += 1
        self.id = Task._seq
        self.name, self.gen = name, gen
        self.priority, self.actor = priority, actor
        self.parent: Task | None = None
        self.children: list[Task] = []
        self.done = False
        self.result = None
        self.cancelled = False
        self.sync_segments = 0                  # 被执行过的同步段数(= 被 next() 的次数)

    # ---- 取消:只置标志,不做抢占 ------------------------------------------
    def cancel(self) -> None:
        self.cancelled = True
        for c in self.children:                 # 级联取消(结构化并发的取消传播)
            c.cancel()

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled

    def check_cancellation(self) -> None:
        """对应 Task.checkCancellation() —— 不调用它,任务就永远不会停。"""
        if self.cancelled:
            raise CancellationError(self.name)

    def __repr__(self) -> str:
        return f"<Task {self.name} {'done' if self.done else 'live'}>"


class Actor:
    """actor = 串行执行域。

    reentrant=True  : 挂起点释放 actor(SE-0306 的语义,默认)
    reentrant=False : job 结束后才释放(对照模型,用于复现死锁)
    """

    def __init__(self, name: str, reentrant: bool = True) -> None:
        self.name, self.reentrant = name, reentrant
        self.mailbox: list[Task] = []
        self.holder: Task | None = None         # 非重入模式下:当前独占 actor 的 job
        self.segments = 0                       # 该 actor 上累计执行过的同步段数

    def send(self, loop: "Loop", name: str, body, priority: int = 5) -> Task:
        """从外部进入 actor(对应 `await someActor.method()`):投递一个 job。"""
        job = make_task(f"{self.name}.{name}", body, priority, actor=self)
        self.mailbox.append(job)
        return loop.spawn(job)

    def __repr__(self) -> str:
        return f"<Actor {self.name} mailbox={len(self.mailbox)}>"


class SerialQueue:
    """对照模型:DispatchQueue 串行队列(actor 之前的那个世界)。

    真实语义(Apple 文档):
      * 串行队列严格 FIFO,一次只跑一个 block;
      * block 内部**没有**"挂起并让出队列"的概念 —— 一旦开始就必须跑到返回;
      * 因此在串行队列的 block 里 `dispatch_sync` 回**同一个队列** = 队列等自己 → 死锁。
    这正是 actor 选择**可重入**的原因:挂起点释放执行权,不会自我等待。
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.q: list[tuple[str, object]] = []
        self.log: list[str] = []
        self.on_queue = False

    def async_(self, name: str, body) -> "SerialQueue":
        self.q.append((name, body))
        return self

    def sync_(self, name: str, body):
        if self.on_queue:
            raise Deadlock(f"{self.name} 上的 block 试图 sync 回同一个队列(队列在等自己)")
        return self._invoke(name, body)

    def _invoke(self, name: str, body):
        self.on_queue = True
        try:
            self.log.append(f"{name} 开始")
            out = body(self)
            self.log.append(f"{name} 结束")
            return out
        finally:
            self.on_queue = False

    def drain(self) -> None:
        while self.q:
            name, body = self.q.pop(0)
            self._invoke(name, body)


class Loop:
    """虚拟时钟 + 就绪队列 + 等待图。单线程、确定性、可复现。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.ready: list[Task] = []
        self.timers: list[tuple[float, int, Task]] = []
        self.waiting: dict[int, list[Task]] = {}     # 任务 id -> 它在等谁
        self.blocked: list[Task] = []                # 被 actor 独占挡在门外的 job
        self.by_id: dict[int, Task] = {}
        self.trace: list[tuple[float, str, str]] = []
        self.suspensions = 0
        self.deferrals = 0
        self.max_steps = 100_000

    # ---- 提交 -------------------------------------------------------------
    def spawn(self, t: Task) -> Task:
        self.by_id[t.id] = t
        self.ready.append(t)
        return t

    # ---- 主循环 -----------------------------------------------------------
    def run_until_idle(self) -> None:
        steps = 0
        while self.ready or self.timers or self.waiting or self.blocked:
            steps += 1
            if steps > self.max_steps:
                raise Deadlock("调度次数超限(可能有任务无限 yield)")
            if not self.ready:
                if self.timers:
                    self.now = self.timers[0][0]
                    while self.timers and self.timers[0][0] <= self.now:
                        _, _, t = heapq.heappop(self.timers)
                        if not t.done:
                            self.ready.append(t)
                    continue
                raise Deadlock("无可推进的任务: " + self._stuck())
            # 高优先级先跑;同优先级按创建顺序(FIFO)
            self.ready.sort(key=lambda t: (-t.priority, t.id))
            self._run_one(self.ready.pop(0))

    def _stuck(self) -> str:
        names = [self.by_id[i].name for i in self.waiting]
        names += [t.name + "(等 actor 释放)" for t in self.blocked]
        return ", ".join(names)

    # ---- 单步:一个同步段 --------------------------------------------------
    def _run_one(self, t: Task) -> None:
        act = t.actor
        if act is not None:
            if act.holder is not None and act.holder is not t:
                self.deferrals += 1
                self.blocked.append(t)          # actor 被别人的同步段占着 → 先排队
                return
            act.holder = t
        t.sync_segments += 1
        if act is not None:
            act.segments += 1
        try:
            cmd = next(t.gen)
        except StopIteration as e:
            self._finish(t, e.value)
            return
        except CancellationError as e:
            self._finish(t, f"已取消({e})")
            return
        self._dispatch(t, cmd)

    def _dispatch(self, t: Task, cmd) -> None:
        kind = cmd[0]
        if kind == "sleep":
            self.suspensions += 1
            self.trace.append((self.now, t.name, f"挂起 await {cmd[1]:g}ms"))
            heapq.heappush(self.timers, (self.now + cmd[1], t.id, t))
        elif kind == "yield":
            self.suspensions += 1
            self.ready.append(t)                # 只让出执行权,不等待任何东西
        elif kind == "await":
            if cmd[1] is ALL_CHILDREN:
                deps = list(t.children)
            elif isinstance(cmd[1], (list, tuple)):
                deps = list(cmd[1])
            else:
                deps = [cmd[1]]                 # 单个任务:await someTask
            pending = [d for d in deps if not d.done]
            if pending:
                self.suspensions += 1
                self.trace.append((self.now, t.name, f"挂起 await {len(pending)} 个任务"))
                self.waiting[t.id] = pending
            else:
                self.ready.append(t)            # 依赖已完成 → await 立即返回,不算挂起
        else:
            raise AssertionError(f"未知指令 {kind!r}")
        self._release(t)

    def _release(self, t: Task) -> None:
        """挂起点释放 actor(可重入);非重入模式要等整个 job 结束才释放。"""
        act = t.actor
        if act is None or not (act.reentrant or t.done):
            return
        act.holder = None
        for x in list(self.blocked):            # 放开后,让排队的 job 重新进就绪队列
            if x.actor is act:
                self.blocked.remove(x)
                self.ready.append(x)

    def _finish(self, t: Task, value) -> None:
        t.done, t.result = True, value
        self.trace.append((self.now, t.name, f"完成 ← {value!r}"))
        for tid, deps in list(self.waiting.items()):
            if t in deps:
                deps.remove(t)
                if not deps:
                    del self.waiting[tid]
                    self.ready.append(self.by_id[tid])
        self._release(t)


# ---------------------------------------------------------------------------
# 构造辅助:生成器体在首次 next() 前不会执行,所以 body 可以拿到自己的 Task
# ---------------------------------------------------------------------------
def make_task(name: str, body, priority: int = 5, actor: Actor | None = None) -> Task:
    t = Task(name, None, priority, actor)
    t.gen = body(t)
    return t


def spawn_root(loop: Loop, name: str, body, priority: int = 5) -> Task:
    """相当于 `Task { ... }` 或 `async let`:起一个顶层任务。"""
    return loop.spawn(make_task(name, body, priority))


def spawn_child(loop: Loop, parent: Task, name: str, body, priority: int = 5) -> Task:
    """相当于 TaskGroup.addTask:子任务登记到父任务上(父任务必须等它)。"""
    t = make_task(f"{parent.name}>{name}", body, priority)   # 名字带父级前缀,便于读 trace
    t.parent = parent
    parent.children.append(t)
    if parent.priority < priority:               # priority escalation
        parent.priority = priority
    return loop.spawn(t)


def async_io(name: str, ms: float, log: list):
    """一个"异步 I/O":await 期间挂起 ms 毫秒,并往 log 里留痕。"""
    def body(_me):
        log.append(f"{name} 开始")
        yield ("sleep", ms)
        log.append(f"{name} 结束")
        return ms
    return body


def finished_at(loop: Loop, name: str) -> float | None:
    """从 trace 里取某个任务的完成时刻(精确匹配名字)。"""
    for now, who, what in loop.trace:
        if who == name and what.startswith("完成"):
            return now
    return None
