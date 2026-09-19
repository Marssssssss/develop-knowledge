"""jobsystem.py — Unity C# Job System + Burst 关键语义的最小模型与自检.

事实来源（均为本机 curl 落地后实读的 Unity 官方文档）：
  * docs.unity3d.com/Manual/job-system.html                     —— job 系统总览
  * docs.unity3d.com/Manual/job-system-jobs.html                —— IJob / IJobParallelFor / IJobFor
  * docs.unity3d.com/Manual/job-system-creating-jobs.html       —— Schedule/Complete、job 数据被复制
  * docs.unity3d.com/Manual/job-system-job-dependencies.html    —— JobHandle 依赖与 CombineDependencies
  * docs.unity3d.com/Manual/job-system-parallel-for-jobs.html   —— 分批与「一次偷一半」的 work stealing
  * docs.unity3d.com/Manual/job-system-thread-safe-types.html   —— NativeContainer 安全系统 / [ReadOnly] / 三种分配器
  * docs.unity3d.com/Packages/com.unity.burst@1.8/manual/...    —— Burst = LLVM 编译 HPC# 子集，禁托管对象
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

_ASSERTIONS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _ASSERTIONS
    if not cond:
        raise AssertionError(f"{label} 失败: {detail}")
    _ASSERTIONS += 1
    print(f"ok {_ASSERTIONS:>2} {label}" + (f"  [{detail}]" if detail else ""))


class SafetyException(Exception):
    """Unity 安全系统在「调度时」抛出的错误（官方文档：第二份 job 调度时即抛）。"""


# ---------------------------------------------------------------- 内存容器
class NativeArray:
    """NativeContainer：托管壳 + 非托管内存。注意它**没有 ref return**。"""

    _seq = 0

    def __init__(self, size: int, allocator: str = "TempJob") -> None:
        NativeArray._seq += 1
        self.uid = NativeArray._seq
        self.size = size
        self.allocator = allocator
        self.buffer: List[float] = [0.0] * size
        self.disposed = False

    def __getitem__(self, i: int) -> float:
        return self.buffer[i]          # 返回副本：arr[0] += 1 写不回去

    def __setitem__(self, i: int, v: float) -> None:
        self.buffer[i] = v

    def readonly(self) -> "View":
        return View(self, write=False)

    def readwrite(self) -> "View":
        return View(self, write=True)


@dataclass(frozen=True)
class View:
    array: NativeArray
    write: bool                        # 无 [ReadOnly] 标注时默认可写


# ---------------------------------------------------------------- job 系统
@dataclass
class Job:
    name: str
    reads: Set[int] = field(default_factory=set)
    writes: Set[int] = field(default_factory=set)
    deps: List["JobHandle"] = field(default_factory=list)
    parallel_length: int = 0
    batch_count: int = 0
    data: Dict[str, float] = field(default_factory=dict)   # job 结构里的普通字段
    run: Optional[object] = None


@dataclass
class JobHandle:
    job: Job
    id: int


class JobSystem:
    def __init__(self) -> None:
        self.next_handle = 0
        self.pending_writers: Dict[int, List[JobHandle]] = {}
        self.pending_readers: Dict[int, List[JobHandle]] = {}
        self.executed: List[str] = []
        self.done: Set[int] = set()
        self.leaked: List[str] = []     # 到帧末仍未 Complete 的 handle

    # ---- 调度即检查（safety system 在 Schedule 时抛错） --------------
    def schedule(self, job: Job) -> JobHandle:
        for uid in job.writes:
            for h in self.pending_writers.get(uid, []):
                if h.id not in self.done and h not in job.deps:
                    raise SafetyException(f"{job.name} 与 {h.job.name} 同时写同一 NativeArray")
            for h in self.pending_readers.get(uid, []):
                if h.id not in self.done and h not in job.deps:
                    raise SafetyException(f"{job.name} 写 / {h.job.name} 读 冲突")
        for uid in job.reads:
            for h in self.pending_writers.get(uid, []):
                if h.id not in self.done and h not in job.deps:
                    raise SafetyException(f"{job.name} 读 / {h.job.name} 写 冲突")

        self.next_handle += 1
        handle = JobHandle(job, self.next_handle)
        for uid in job.writes:
            self.pending_writers.setdefault(uid, []).append(handle)
        for uid in job.reads:
            self.pending_readers.setdefault(uid, []).append(handle)
        self.leaked.append(job.name)
        return handle

    @staticmethod
    def combine(handles: List[JobHandle]) -> List[JobHandle]:
        """JobHandle.CombineDependencies：把多个依赖合并成一个逻辑依赖。"""
        merged: List[JobHandle] = []
        for h in handles:
            merged.extend(h.job.deps)
            merged.append(h)
        return merged

    def complete(self, handle: JobHandle) -> None:
        if handle.id in self.done:
            return
        for dep in handle.job.deps:
            self.complete(dep)
        self._execute(handle.job)
        self.done.add(handle.id)
        if handle.job.name in self.leaked:
            self.leaked.remove(handle.job.name)   # Complete 同时清理安全系统状态
        for uid in handle.job.writes:
            self.pending_writers[uid] = [h for h in self.pending_writers.get(uid, []) if h.id != handle.id]
        for uid in handle.job.reads:
            self.pending_readers[uid] = [h for h in self.pending_readers.get(uid, []) if h.id != handle.id]

    def _execute(self, job: Job) -> None:
        # 官方文档：调度时 job 数据被复制一份，只有 NativeContainer 与原对象共享内存
        copied = dict(job.data)
        if job.parallel_length and job.run:
            for start, end in split_batches(job.parallel_length, job.batch_count):
                for i in range(start, end):
                    job.run(i, copied)              # type: ignore[operator]
        elif job.run:
            job.run(copied)                         # type: ignore[operator]
        self.executed.append(job.name)


# ---------------------------------------------------------------- 并行分批
def split_batches(length: int, batch_count: int) -> List[tuple]:
    """Schedule(length, batchCount)：按 batch_count 切成若干批。"""
    return [(s, min(s + batch_count, length)) for s in range(0, length, batch_count)]


def split_to_workers(batches: List[tuple], workers: int) -> List[List[tuple]]:
    """每个 worker 先领一段**连续**的批次（保缓存局部性），而不是轮流发牌。"""
    per = (len(batches) + workers - 1) // workers
    return [batches[i * per:(i + 1) * per] for i in range(workers)]


def batch_duration(idx: int) -> int:
    """演示用的批耗时（真实场景里各批负载天然不均，这里用确定性函数造出差异）。"""
    return 1 + (idx * 7) % 29


def steal_schedule(batches: List[tuple], workers: int = 4) -> Dict[str, object]:
    """work stealing：闲下来的 worker 一次偷走别人**剩余批次的一半**。

    官方文档原话：先干完的 worker 从其它 worker 那里偷批次，**一次只偷一半**，
    以保证缓存局部性。
    """
    queues = split_to_workers(batches, workers)
    durs: List[List[int]] = [[] for _ in range(workers)]
    cursor = 0
    for w in range(workers):
        durs[w] = [batch_duration(cursor + i) for i in range(len(queues[w]))]
        cursor += len(queues[w])

    steals: List[tuple] = []            # (worker, 被偷者, 偷到的批数, 被偷者当时剩余)
    processed = 0
    guard = 0
    while any(queues) and guard < 100000:
        guard += 1
        for w in range(workers):
            if durs[w]:
                durs[w][0] -= 1
                if durs[w][0] == 0:
                    durs[w].pop(0)
                    queues[w].pop(0)
                    processed += 1
            else:
                victim = max(range(workers), key=lambda j: len(queues[j]))
                remaining = len(queues[victim])
                if remaining >= 2:
                    take = max(1, remaining // 2)
                    steals.append((w, victim, take, remaining))
                    queues[w].extend(queues[victim][:take])
                    durs[w].extend(durs[victim][:take])
                    queues[victim] = queues[victim][take:]
                    durs[victim] = durs[victim][take:]
    return {"steals": steals, "processed": processed, "left": [len(q) for q in queues]}


# ---------------------------------------------------------------- 分配器
class AllocatorTracker:
    """Temp(≤1 帧) / TempJob(≤4 帧否则控制台告警) / Persistent(随应用)。"""

    LIMIT = {"Temp": 1, "TempJob": 4, "Persistent": None}

    def __init__(self) -> None:
        self.born: Dict[NativeArray, int] = {}
        self.warnings: List[str] = []
        self.frame = 0

    def track(self, arr: NativeArray) -> None:
        self.born[arr] = self.frame

    def end_frame(self) -> None:
        self.frame += 1
        for arr, born in list(self.born.items()):
            limit = self.LIMIT[arr.allocator]
            if limit is not None and self.frame - born > limit:
                self.warnings.append(f"{arr.allocator} 存活 {self.frame - born} 帧 > {limit}")


# ---------------------------------------------------------------- Burst/HPC#
BURST_UNSUPPORTED = {"char", "decimal", "string", "object", "class", "managed array",
                     "multi-dimensional array"}
BURST_SUPPORTED = {"bool", "byte", "sbyte", "double", "float", "int", "uint", "long",
                   "ulong", "short", "ushort"}


def burst_supports(t: str) -> bool:
    return t in BURST_SUPPORTED


def burst_field_ok(t: str) -> bool:
    """按官方文档的 HPC# 子集判定某个声明能否进 Burst 编译的 job。"""
    if t in ("static readonly managed array",):
        return True          # 特例：只读、且不能当参数传来传去
    if t in ("struct", "struct+fixed", "generic struct", "LayoutKind.Explicit struct"):
        return True
    return burst_supports(t)

