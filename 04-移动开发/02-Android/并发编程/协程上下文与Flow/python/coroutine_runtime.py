#!/usr/bin/env python3
"""最小协程运行时(教学用,配合 coroutine_check.py)。

复刻 kotlinx.coroutines 官方文档描述的四条语义:
  1. CoroutineContext 是"键值元素集合",用 `+` 合并时**右侧覆盖同键元素**
  2. CoroutineDispatcher:Default 的并行度上限 = CPU 核数;IO 允许更大并行度;
     Unconfined 先在调用者线程执行,首个挂起点之后在挂起函数所在线程恢复
  3. 结构化并发:父协程必须等待所有子协程结束才算完成;子协程失败会取消父与兄弟
  4. 挂起点之后线程可能变化(挂起函数决定恢复线程)

模型简化(不影响上述结论,但需明说):
  * `simulate_pool()` 把"占用线程"近似为整个协程生命周期,以便用峰值并发直接体现核数上限
  * `Sim` 只模拟协作式挂起与 Job 树,不模拟线程池排队
"""

from __future__ import annotations

import heapq


# ----------------------------------------------------------------- CoroutineContext
class Key:
    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


JOB, DISPATCHER, NAME = Key("Job"), Key("Dispatcher"), Key("CoroutineName")


class Ctx:
    """CoroutineContext:键值元素集合;`plus` 右侧覆盖同键元素。"""

    def __init__(self, elems: dict | None = None):
        self.elems = dict(elems or {})

    def plus(self, other: "Ctx") -> "Ctx":
        merged = dict(self.elems)
        merged.update(other.elems)          # 同键右侧覆盖
        return Ctx(merged)

    def __getitem__(self, key: Key):
        return self.elems[key]

    def get(self, key: Key, default=None):
        return self.elems.get(key, default)

    def __repr__(self) -> str:
        return " + ".join(f"{k.name}={v}" for k, v in self.elems.items())


class Dispatcher:
    def __init__(self, name: str, parallelism: int):
        self.name, self.parallelism = name, parallelism

    def __repr__(self) -> str:
        return self.name


def new_dispatchers(cores: int = 4) -> dict:
    """官方:Dispatchers.Default 的并行度上限 = CPU 核数(至少 2)。"""
    return {"default": Dispatcher("Dispatchers.Default", max(cores, 2)),
            "io": Dispatcher("Dispatchers.IO", 64),
            "unconfined": Dispatcher("Dispatchers.Unconfined", 10 ** 6),
            "main": Dispatcher("Dispatchers.Main", 1)}


def simulate_pool(parallelism: int, job_costs: list[int]) -> tuple[int, int]:
    """把 n 个 CPU 任务派到 parallelism 条线程:返回 (完成时刻, 峰值并发)。"""
    free_at = [0] * parallelism
    peak = 0
    for cost in job_costs:
        slot = free_at.index(min(free_at))
        free_at[slot] += cost
        peak = max(peak, sum(1 for t in free_at if t > 0))
    return max(free_at), peak


# ----------------------------------------------------------------- Job 树与协作式调度
class Job:
    def __init__(self, name: str, parent: "Job | None", dispatcher: Dispatcher | None):
        self.name, self.parent, self.children = name, parent, []
        # 未显式指定调度器 → 从父协程上下文继承(官方:builder 不传 context 时继承父的)
        self.dispatcher = dispatcher if dispatcher is not None else (parent.dispatcher if parent else None)
        self.state = "ACTIVE"
        self.exception: BaseException | None = None
        self.body_done = False
        self.started_at = self.finished_at = None
        self.thread_log: list[str] = []
        self.gen = None
        self.suspended = False

    @property
    def is_terminal(self) -> bool:
        return self.state in ("COMPLETED", "CANCELLED")

    def __repr__(self) -> str:
        return f"Job({self.name},{self.state})"


class Sim:
    """协作式调度:协程体是生成器,`yield ("delay", ms)` 表示挂起 ms 毫秒。"""

    def __init__(self):
        self.now, self._seq = 0, 0
        self._timers: list[tuple[int, int, Job]] = []
        self.journal: list[str] = []

    def launch(self, name: str, body, parent: Job | None = None,
               dispatcher: Dispatcher | None = None) -> Job:
        job = Job(name, parent, dispatcher)
        if parent is not None:
            parent.children.append(job)
        job.started_at = self.now
        job.gen = body(job)
        self.journal.append(f"@{self.now} launch {name} on {job.dispatcher}")
        self._resume(job)
        return job

    def _thread_of(self, job: Job) -> str:
        """Unconfined 先在调用者线程跑,挂起点之后落到挂起函数所用的线程。"""
        if job.dispatcher and job.dispatcher.name == "Dispatchers.Unconfined":
            return "kotlinx.coroutines.DefaultExecutor" if job.suspended else "main"
        return str(job.dispatcher)

    def _resume(self, job: Job) -> None:
        if job.state != "ACTIVE":
            return
        job.thread_log.append(self._thread_of(job))
        try:
            op = next(job.gen)
        except StopIteration:
            job.body_done = True
            self._maybe_complete(job)
            return
        except BaseException as exc:                       # 协程体抛出 → 取消整个作用域
            self._fail(job, exc)
            return
        kind, arg = op
        if kind == "delay":
            job.suspended = True
            self._later(job, arg)
        elif kind == "fail":
            self._fail(job, arg)
        else:
            raise ValueError(f"unknown suspension {kind}")

    def _later(self, job: Job, delay_ms: int) -> None:
        self._seq += 1
        heapq.heappush(self._timers, (self.now + delay_ms, self._seq, job))

    def _maybe_complete(self, job: Job) -> None:
        if job.is_terminal:
            return                                   # 已取消的协程不得被"复活"
        if not job.body_done:
            return                                   # 协程体还在跑,状态不改
        if any(not c.is_terminal for c in job.children):
            job.state = "COMPLETING"                 # 体已结束但仍在等子协程
            return
        job.state, job.finished_at = "COMPLETED", self.now
        self.journal.append(f"@{self.now} complete {job.name}")
        if job.parent is not None:
            self._maybe_complete(job.parent)

    def _cancel(self, job: Job) -> None:
        """取消 job **自身**及其整棵子树。只取消子树会让节点本身继续跑完。"""
        if job.is_terminal:
            return
        job.state, job.finished_at = "CANCELLED", self.now
        self.journal.append(f"@{self.now} cancel {job.name}")
        for child in job.children:
            self._cancel(child)

    def _fail(self, job: Job, exc: BaseException) -> None:
        job.exception, job.state, job.finished_at = exc, "CANCELLED", self.now
        self.journal.append(f"@{self.now} fail {job.name}: {exc}")
        for child in job.children:
            self._cancel(child)
        parent = job.parent
        if parent is None:
            return
        parent.exception = exc                       # 失败向上传播:取消父与兄弟
        for sib in parent.children:
            if sib is not job:
                self._cancel(sib)
        self._cancel(parent)
        if parent.parent is not None:
            self._fail(parent.parent, exc)

    def run(self) -> int:
        while self._timers:
            at, _, job = heapq.heappop(self._timers)
            self.now = max(self.now, at)
            if job.state == "ACTIVE":                       # 已取消的协程不再恢复
                self._resume(job)
        return self.now
