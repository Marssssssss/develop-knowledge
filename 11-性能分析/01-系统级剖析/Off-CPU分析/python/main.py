#!/usr/bin/env python3
"""Off-CPU 分析 demo:Python 版 —— 上下文切换记账模拟器。

完整实现 Gregg offcputime 的核心伪代码(见 README),并刻意构造出
原文描述的两大坑,让它们肉眼可见:

  1. 线程池时间膨胀:N 个线程执行相同路径,聚合 off-CPU 时间
     之和 > 流逝墙钟(原 mysqld 案例中 io_handler_thread 列宽超过 30s);
  2. --state=2 过滤:只统计 TASK_UNINTERRUPTIBLE(真正阻塞),
     剔除非自愿切换(CPU 争抢被调度出去的 R 状态)。

抽象层级:不执行真实负载,只按时间轴重放"切换事件";
每个 CPU 核有一个 idle 线程兜底,保证任何 switch(off, on) 中
off 确实在核上、on 的记账语义正确。
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field

TASK_RUNNING = "R"                    # 可运行(含被抢占)
TASK_UNINTERRUPTIBLE = "D"            # 不可中断睡眠(--state=2 保留)

random.seed(42)
IDLE = 999


@dataclass
class Thread:
    tid: int
    pid: int
    execname: str
    user_stack: list[str]
    state: str = TASK_RUNNING          # 切换出去时的任务状态


@dataclass
class Tracee:
    """单个被追踪线程的记账状态(伪代码数组下标即 tid)。"""
    thread: Thread
    sleeptime: float | None = None     # 切换出去的时刻(None = 清零/从未睡)


@dataclass
class Trace:
    """offcputime 追踪器:totaltime[(pid,execname,stack,state)] += delta。"""
    tracees: dict[int, Tracee] = field(default_factory=dict)
    totaltime: dict[tuple, float] = defaultdict(float)

    def register(self, t: Thread) -> None:
        self.tracees[t.tid] = Tracee(t)

    # ---- Gregg offcputime 伪代码,逐行对应 ----
    def switch_finish(self, ts: float, off_tid: int, on_tid: int, state: str) -> None:
        # on context switch finish(处于 *下一个* 线程的上下文):
        prev = self.tracees[off_tid]
        prev.sleeptime = ts                    # sleeptime[prev_thread_id] = timestamp
        prev.thread.state = state
        nxt = self.tracees[on_tid]
        if nxt.sleeptime is None:              # if !sleeptime[thread_id]: return
            return
        delta = ts - nxt.sleeptime             # delta = timestamp - sleeptime[...]
        th = nxt.thread
        # bcc offcputime:总是抓内核栈+用户栈;-d 时两者之间插 "-"
        stack = ";".join(["finish_task_switch", "__schedule", "-"] + th.user_stack)
        self.totaltime[(th.pid, th.execname, stack, th.state)] += delta
        nxt.sleeptime = None                   # sleeptime[thread_id] = 0

    def exit_dump(self, only_state_d: bool = False) -> dict[str, float]:
        """tracer 退出:输出 folded(可选 --state=2 过滤非 D 状态)。"""
        folded: dict[str, float] = defaultdict(float)
        for (pid, execname, stack, state), secs in self.totaltime.items():
            if only_state_d and state != TASK_UNINTERRUPTIBLE:
                continue
            folded[f"{execname};{stack}"] += secs
        return folded


def build_mysqld_like() -> tuple[Trace, dict[int, int]]:
    """构造:8 个空闲等任务的线程池 + 2 个干活线程(双核各占一个)。"""
    tr = Trace()
    for i in range(8):
        tr.register(Thread(tid=100 + i, pid=6612, execname="mysqld",
                           user_stack=["io_handler_thread", "os_event_wait",
                                       "epoll_wait"]))
    tr.register(Thread(tid=200, pid=6612, execname="mysqld",
                       user_stack=["handle_connection", "do_command",
                                   "read_row", "pread"]))
    tr.register(Thread(tid=201, pid=6612, execname="mysqld",
                       user_stack=["handle_connection", "do_command",
                                   "row_serialize", "malloc"]))
    # 每核一个 idle 兜底线程(tid 9990/9991/9992):永不作为 on 被结算
    for core in (0, 1, 2):
        tr.register(Thread(tid=9990 + core, pid=0, execname="idle",
                           user_stack=["cpu_idle"]))
    core_of = {200: 0, 201: 1}
    for tid in range(100, 108):                       # 空闲线程池共用核 2
        core_of[tid] = 2
    return tr, core_of


def gen_events(duration: float) -> list[tuple[float, str, int]]:
    """生成调度事件流:block/wake(IO 阻塞,D 状态)+ preempt/resume(抢占,R)
    + 线程池的周期性长睡眠(空闲等待,演示时间膨胀)。"""
    events: list[tuple[float, str, int]] = []
    for tid in (200, 201):
        t = random.uniform(0.2, 0.6)
        while t < duration:
            io_delay = random.uniform(0.05, 0.3)     # pread 的 IO 延迟
            events.append((t, "block", tid))
            events.append((t + io_delay, "wake", tid))
            t += io_delay + random.uniform(0.2, 0.7)  # 干活间隔
    t = random.uniform(0.4, 0.9)
    while t < duration:                               # 非自愿切换(抢占)
        victim = random.choice([200, 201])
        hold = random.uniform(0.01, 0.12)
        events.append((t, "preempt", victim))
        events.append((t + hold, "resume", victim))
        t += random.uniform(0.3, 0.8)
    for tid in range(100, 108):                        # 线程池:长睡眠等任务
        t = 0.001
        while t < duration:
            wake = t + random.uniform(10, 28)          # 大部分窗口都在睡
            events.append((t, "block", tid))
            events.append((wake, "wake", tid))
            t = wake + 0.001                           # 醒来 1ms 又睡去
    events.sort()
    return events


def replay(tr: Trace, core_of: dict[int, int], duration: float) -> None:
    """重放事件流。每核维护 owner:block/preempt 时核让给 idle,
    wake/resume 时 tid 回核 —— 保证 switch(off,on) 语义正确。"""
    owner = {0: 200, 1: 201}
    for ts, kind, tid in gen_events(duration):
        c = core_of[tid]
        idle = 9990 + c
        if kind == "block":
            tr.switch_finish(ts, tid, idle, TASK_UNINTERRUPTIBLE)
            owner[c] = idle
        elif kind == "wake":
            tr.switch_finish(ts, idle, tid, TASK_RUNNING)   # idle 让出,tid 回核
            owner[c] = tid
        elif kind == "preempt":
            if owner[c] == tid:                        # 只有在核上才可被抢占
                tr.switch_finish(ts, tid, idle, TASK_RUNNING)
                owner[c] = idle
        else:                                          # resume
            if owner[c] == idle:
                tr.switch_finish(ts, idle, tid, TASK_RUNNING)
                owner[c] = tid


def main() -> int:
    duration = 30.0
    tr, core_of = build_mysqld_like()
    replay(tr, core_of, duration)

    print(f"== offcputime 模拟({duration:.0f}s 追踪窗口)==\n")
    folded = tr.exit_dump()
    total_s = sum(folded.values())
    print(f"{'off-CPU 总时间':>16} | 墙钟 | 线程数 | 膨胀比")
    print(f"{total_s:13.1f}s | {duration:4.0f}s | {len(tr.tracees) - 3} | "
          f"{total_s / duration:.2f}x  <- 聚合等待 > 流逝时间(线程池叠加,Gregg mysqld 案例)\n")

    print("---- folded 输出(countname=us,可喂 flamegraph.pl --color=io)----")
    for stack in sorted(folded, key=lambda s: -folded[s])[:6]:
        print(f"{stack} {int(folded[stack] * 1e6)}")

    print("\n---- --state=2 过滤(只留 TASK_UNINTERRUPTIBLE,剔除非自愿切换)----")
    folded_d = tr.exit_dump(only_state_d=True)
    for stack in sorted(folded_d, key=lambda s: -folded_d[s])[:4]:
        print(f"{stack} {int(folded_d[stack] * 1e6)}")
    dropped = total_s - sum(folded_d.values())
    print(f"\n被过滤掉的非自愿切换(R 状态)时间: {dropped:.1f}s")
    print("\n读图要点(Gregg): 宽 = 总 off-CPU;线程池空闲塔宽于窗口属正常;")
    print("'等在 pread' 解释不了为何久 —— 因果要看 wakeup/off-wake 栈。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
