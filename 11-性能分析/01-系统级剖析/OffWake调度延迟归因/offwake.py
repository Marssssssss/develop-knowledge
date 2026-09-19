"""Off-Wake 剖析：把 off-CPU 时间按「阻塞栈 + 唤醒者栈」联合归因 —— 模型层。

权威来源：Brendan Gregg, "Off-CPU Analysis"
https://www.brendangregg.com/offcpuanalysis.html

原文确立的事实（README §2 逐条对应）：
1. off-CPU time = 线程**阻塞**到**再次开始运行**之间的时间，**包含调度延迟**
   （"Off-CPU time consists of everything from when a thread blocked to when it
   began running again, including scheduler delay."）—— 这叫**时间膨胀**。
2. 插桩点是唯一的：上下文切换例程的结尾 `finish_task_switch(prev)`，
   在**下一个线程（next / cur）的上下文**中调用。原文伪代码：

       on context switch finish:
           sleeptime[prev_thread_id] = timestamp
           if !sleeptime[thread_id]
               return
           delta = timestamp - sleeptime[thread_id]
           totaltime[pid, execname, user stack, kernel stack] += delta
           sleeptime[thread_id] = 0

   之所以只测一次栈就够：**"Application stack traces don't change while off-CPU."**
3. 状态过滤：`TASK_RUNNING`(0) 是非自愿切换，`TASK_INTERRUPTIBLE`(1) /
   `TASK_UNINTERRUPTIBLE`(2) 才是通常关心的阻塞；`--state 2` 可只留 2。
4. 线程池陷阱：大部分阻塞时间落在「等待工作」的栈里（MySQL 的
   `do_nanosleep`/`futex_wait_queue_me`/`read_events`），要用请求上下文过滤
   （按折叠格式 `grep do_command`）只留请求同步路径。
5. 唤醒者视角：很多 off-CPU 栈只显示**阻塞路径**，真正的原因在**另一个线程**
   （发起 wakeup 的 waker）。工具 `wakeuptime` 测唤醒栈，`offwaketime` 把二者
   关联；宽度仍然是 off-CPU 时间（微秒）。
6. 火焰图左右顺序**没有意义**（"The left-to-right ordering has no meaning."）。
7. 开销：调度器事件可能每秒数百万，必须内核内聚合（eBPF），并警惕反馈回路；
   建议先只追踪 0.1 秒。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

TASK_RUNNING = 0
TASK_INTERRUPTIBLE = 1
TASK_UNINTERRUPTIBLE = 2

STATE_NAME = {0: "RUNNING", 1: "INTERRUPTIBLE", 2: "UNINTERRUPTIBLE"}


@dataclass
class Sample:
    """一段完整的 off-CPU 区间。"""

    tid: int
    start: float
    end: float
    state: int
    stack: str
    woken_at: Optional[float] = None
    waker_stack: str = ""

    @property
    def off_cpu(self) -> float:
        """阻塞到重新开跑的墙钟时间（原文口径，含调度延迟）。"""
        return self.end - self.start

    @property
    def blocked(self) -> float:
        """真正被阻塞的时间（不含运行队列等待）；无唤醒点时等于 off_cpu。"""
        if self.woken_at is None:
            return self.off_cpu
        return self.woken_at - self.start

    @property
    def sched_latency(self) -> float:
        """运行队列延迟 = 调度延迟，是时间膨胀的来源。"""
        if self.woken_at is None:
            return 0.0
        return self.end - self.woken_at

    @property
    def inflation(self) -> float:
        """膨胀比 = off_cpu / blocked，1.0 表示没有调度延迟。"""
        b = self.blocked
        return 1.0 if b <= 0 else self.off_cpu / b


class OffCpuTracer:
    """按原文伪代码实现的 off-CPU 记账器。"""

    def __init__(self, state_filter: Optional[int] = None):
        self.state_filter = state_filter  # None = 不过滤；2 = --state 2
        self.sleeptime: Dict[int, float] = {}
        self.state: Dict[int, int] = {}
        self.wakets: Dict[int, float] = {}
        self.waker_stack: Dict[int, str] = {}
        self.samples: List[Sample] = []

    # ---------------------------------------------------------------- 事件 ---
    def on_wakeup(self, tid: int, ts: float, waker_stack: str = "") -> None:
        """`try_to_wake_up()`：记录唤醒时刻与唤醒者栈。"""
        self.wakets[tid] = ts
        self.waker_stack[tid] = waker_stack

    def on_switch_finish(
        self, prev_tid: int, prev_state: int, cur_tid: int, cur_stack: str, ts: float
    ) -> Optional[Sample]:
        """`finish_task_switch(prev)` 结尾：给 prev 打起点，结算 cur 的等待。"""
        # ① prev 开始睡
        self.sleeptime[prev_tid] = ts
        self.state[prev_tid] = prev_state
        # ② cur 若是被唤醒后才跑到这里，结算它这一段 off-CPU
        start = self.sleeptime.get(cur_tid)
        if not start:  # 0 或不存在 ⇒ 本次不是阻塞后的唤醒
            return None
        state = self.state.get(cur_tid, TASK_INTERRUPTIBLE)
        sample = Sample(
            tid=cur_tid,
            start=start,
            end=ts,
            state=state,
            stack=cur_stack,
            woken_at=self.wakets.pop(cur_tid, None),
            waker_stack=self.waker_stack.pop(cur_tid, ""),
        )
        self.sleeptime[cur_tid] = 0  # 原文：清零而不是删除
        self.samples.append(sample)
        return sample

    # ---------------------------------------------------------------- 查询 ---
    def filtered(self) -> List[Sample]:
        if self.state_filter is None:
            return list(self.samples)
        return [s for s in self.samples if s.state == self.state_filter]

    def by_stack(self, samples: Optional[Sequence[Sample]] = None) -> Dict[str, float]:
        """按阻塞栈聚合 off-CPU 时间（折叠格式的 `count` 列）。"""
        out: Dict[str, float] = {}
        for s in (self.filtered() if samples is None else samples):
            out[s.stack] = out.get(s.stack, 0.0) + s.off_cpu
        return out

    def by_stack_count(self, samples: Optional[Sequence[Sample]] = None) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in (self.filtered() if samples is None else samples):
            out[s.stack] = out.get(s.stack, 0) + 1
        return out

    def request_sync(self, marker: str) -> List[Sample]:
        """线程池陷阱的解药：只保留含请求上下文标记的栈（等价 `grep do_command`）。"""
        return [s for s in self.filtered() if marker in s.stack]

    def off_wake(self, samples: Optional[Sequence[Sample]] = None) -> Dict[Tuple[str, str], float]:
        """off-wake 联合归因：键是 (唤醒者栈, 阻塞栈)，值仍是 off-CPU 时间。"""
        out: Dict[Tuple[str, str], float] = {}
        for s in (self.filtered() if samples is None else samples):
            k = (s.waker_stack or "(unknown waker)", s.stack)
            out[k] = out.get(k, 0.0) + s.off_cpu
        return out


def folded(stack: str, count: float) -> str:
    """折叠格式：一行一个栈，分号分隔，末尾是指标值。"""
    return f"{stack} {count:g}"


def parse_folded(lines: Sequence[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        frames, _, cnt = ln.rpartition(" ")
        out[frames] = out.get(frames, 0.0) + float(cnt)
    return out


def parse_differential(lines: Sequence[str]) -> Dict[str, Tuple[float, float]]:
    """difffolded.pl 的三列输出：`stack before after`（本 demo 只用它做回归比对）。"""
    out: Dict[str, Tuple[float, float]] = {}
    for ln in lines:
        parts = ln.rstrip().rsplit(" ", 2)
        if len(parts) != 3:
            continue
        out[parts[0]] = (float(parts[1]), float(parts[2]))
    return out


def elapsed_split(sample: Sample) -> Dict[str, float]:
    """把一个 off-CPU 区间拆成「真阻塞 / 调度延迟 / 膨胀比」三段。"""
    return {
        "off_cpu": sample.off_cpu,
        "blocked": sample.blocked,
        "sched_latency": sample.sched_latency,
        "inflation": sample.inflation,
    }
