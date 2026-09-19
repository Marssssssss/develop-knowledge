"""CPU 利用率口径与 IPC 归因 —— 模型层。

口径来源（本 demo 唯一权威）：Brendan Gregg,
"CPU Utilization is Wrong", 2017-05-09
https://www.brendangregg.com/blog/2017-05-09/cpu-utilization-is-wrong.html

原文确立的几条事实（README §2 逐条对应）：
1. 所谓 "CPU utilization" 实际是 **non-idle time**：内核在上下文切换处记账，
   「非 idle 线程开始跑 → 停下」之间整段时间都被算作 utilized。
2. 现代 CPU 远快于主存，等待内存的周期被算进了 %CPU，所以高 %CPU
   **不等于** 处理器是瓶颈；作者建议改名 %CPU → %CYC。
3. 判别口径是 IPC = instructions / cycles：**IPC < 1.0 大概率内存停顿**，
   **IPC > 1.0 大概率指令受限**。作者明说 1.0 这条线是"我拍的"，
   正确做法是跑一个 CPU 密集 + 一个内存密集的哑负载取两者中点。
4. 4-wide（每周期最多退休 4 条指令）的机器上 IPC 0.78 = 峰值的 19.5%。
5. 其它误导源：温度降频、Turboboost / speedstep 变主频、平均值掩盖突发、
   自旋锁（%CPU 高且 IPC 高但逻辑上不前进）、超线程（停顿周期可被另一线程
   用掉，于是 %CPU 把实际可用的周期记成了已用）。
6. 作者明确："I'm not talking about iowait at all (that's disk I/O)" ——
   iowait 是磁盘 I/O 口径，**不是**内存停顿，别把它当 %STL 用。

本 demo 把上面 6 条做成可计算的函数，并给出「%CPU = 100 − idle」这一工具口径
下 iowait 归属的显式定义（见 `busy_pct` 的 docstring）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

# proc(5) 的 cpu 汇总行字段顺序（不含 cpu 名）
_STAT_FIELDS = (
    "user",
    "nice",
    "system",
    "idle",
    "iowait",
    "irq",
    "softirq",
    "steal",
    "guest",
    "guest_nice",
)

# 本 demo 参与配平的 8 个经典字段；guest/guest_nice 的归属口径官方未在此处给出，
# 因此只解析、不并入 total，避免无据断言。
_TOTAL_FIELDS = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")


@dataclass(frozen=True)
class ProcStat:
    """一次 /proc/stat 采样的 cpu 汇总行（单位 jiffies）。"""

    user: int = 0
    nice: int = 0
    system: int = 0
    idle: int = 0
    iowait: int = 0
    irq: int = 0
    softirq: int = 0
    steal: int = 0
    guest: int = 0
    guest_nice: int = 0

    def total(self) -> int:
        return sum(getattr(self, f) for f in _TOTAL_FIELDS)


def parse_procstat(line: str) -> ProcStat:
    """解析 `cpu  <10 个整数>` 形式的汇总行。"""
    parts = line.split()
    if not parts or parts[0] != "cpu":
        raise ValueError(f"不是 cpu 汇总行: {line!r}")
    vals = [int(x) for x in parts[1:]]
    if len(vals) < len(_STAT_FIELDS):
        raise ValueError(f"字段不足 {len(_STAT_FIELDS)} 个: {line!r}")
    return ProcStat(**dict(zip(_STAT_FIELDS, vals[: len(_STAT_FIELDS)])))


def delta_pct(prev: ProcStat, cur: ProcStat) -> Dict[str, float]:
    """两次采样之间的各态占比（百分比，和为 100）。"""
    total = cur.total() - prev.total()
    if total <= 0:
        raise ValueError("总 jiffies 没有增长，无法求差")
    out: Dict[str, float] = {}
    for f in _TOTAL_FIELDS:
        out[f] = (getattr(cur, f) - getattr(prev, f)) * 100.0 / total
    return out


def busy_pct(pcts: Dict[str, float]) -> float:
    """工具口径的 busy：100 − idle。

    注意这个定义把 iowait **算进** busy —— 因为 idle 与 iowait 在 proc(5) 里
    是两个独立列，而繁忙度通常按「非 idle 列」求和。这恰恰是 Gregg 说的
    「%CPU 是个会误导人的合成量」的第一层：它连"磁盘等待"也吞进去了。
    """
    return 100.0 - pcts["idle"]


def idle_thread_pct(pcts: Dict[str, float]) -> float:
    """真正在跑 idle 线程的时间 = idle + iowait。

    iowait 的定义是「CPU 空闲但有未完成 I/O」，它仍然属于空闲，只是被单独记账。
    这一列与 `busy_pct` 的差值就是 iowait，用来显式暴露「busy 里有多少其实在等盘」。
    """
    return pcts["idle"] + pcts["iowait"]


def ipc(instructions: int, cycles: int) -> float:
    """IPC = instructions / cycles，越高越好（作者的简化说法）。"""
    if cycles <= 0:
        raise ValueError("cycles 必须为正")
    return instructions / cycles


def verdict(ipc_value: float, threshold: float = 1.0) -> str:
    """按作者拍的 1.0 分界线定性：`memory` 或 `instruction`。"""
    return "memory" if ipc_value < threshold else "instruction"


def pct_of_peak(ipc_value: float, width: float = 4.0) -> float:
    """IPC 占该处理器峰值（width-wide）的百分比。4-wide 上 IPC 0.78 ⇒ 19.5%。"""
    if width <= 0:
        raise ValueError("width 必须为正")
    return ipc_value * 100.0 / width


def stall_split(cycles: int, instructions: int, width: float = 4.0) -> Dict[str, float]:
    """把 non-idle 周期拆成「退休周期」与「停顿周期」。

    retired = instructions / width（满速时这些指令本来只需这么多周期），
    stalled = cycles − retired。输出的 pct_stl 对应作者呼吁的 %STL。
    """
    if cycles <= 0:
        raise ValueError("cycles 必须为正")
    retired = instructions / width
    stalled = cycles - retired
    if stalled < 0:
        stalled = 0.0
        retired = float(cycles)
    return {
        "cycles": float(cycles),
        "retired_cycles": retired,
        "stalled_cycles": stalled,
        "pct_ins": retired * 100.0 / cycles,
        "pct_stl": stalled * 100.0 / cycles,
    }


def clock_ghz(cycles: int, task_clock_ms: float) -> float:
    """由 cycles 与 task-clock 反推平均主频（GHz）。

    原文 `perf stat` 输出里这一列写作 2.236 GHz，正是 cycles / task-clock。
    """
    if task_clock_ms <= 0:
        raise ValueError("task_clock_ms 必须为正")
    return cycles / (task_clock_ms / 1000.0) / 1e9


def cpus_utilized(task_clock_ms: float, elapsed_ms: float) -> float:
    """task-clock / 墙钟 = 平均占用了几个 CPU。原文为 64.116 CPUs utilized。"""
    if elapsed_ms <= 0:
        raise ValueError("elapsed_ms 必须为正")
    return task_clock_ms / elapsed_ms


def is_spin_lock(busy: float, ipc_value: float, progressed: bool) -> bool:
    """自旋锁特征：busy 高、IPC 也高、但逻辑上没有前进。

    这是 %CPU 最隐蔽的一类误导 —— 计数器和利用率口径都"正常"，
    只有业务吞吐能揭穿它。
    """
    return busy >= 90.0 and ipc_value >= 2.0 and not progressed


def avg_hides_burst(samples: Sequence[float]) -> Dict[str, float]:
    """平均值掩盖突发：返回均值、峰值、以及达到峰值的样本比例。"""
    if not samples:
        raise ValueError("samples 不能为空")
    peak = max(samples)
    avg = sum(samples) / len(samples)
    burst = sum(1 for s in samples if s >= 99.9) / len(samples)
    return {"avg": avg, "peak": peak, "burst_frac": burst, "peak_over_avg": peak - avg}


def hyperthread_headroom(sib_stalled_pct: Sequence[float]) -> float:
    """超线程可偷的余量：兄弟线程停顿周期占比的最小值。

    作者说：有了超线程，那些停顿周期可以被另一个线程用掉，于是 %CPU 会把
    「实际可用」的周期记成已用。这里只做保守估计（取最小的停顿占比）。
    """
    if not sib_stalled_pct:
        raise ValueError("至少需要一个兄弟线程")
    return min(sib_stalled_pct)


def summarize(pcts: Dict[str, float], ipc_value: float, width: float = 4.0) -> List[str]:
    """给出一份可直接贴进事故单的三行结论。"""
    return [
        f"busy={busy_pct(pcts):.1f}% (其中 iowait {pcts['iowait']:.1f}%)"
        f" 真正空闲线程 {idle_thread_pct(pcts):.1f}%",
        f"IPC={ipc_value:.2f} ⇒ {verdict(ipc_value)} bound"
        f"（峰值 {width:.0f}-wide 的 {pct_of_peak(ipc_value, width):.1f}%）",
        f"建议：{'查内存 I/O 与局部性' if verdict(ipc_value) == 'memory' else '查代码路径与指令数'}",
    ]
