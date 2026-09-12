#!/usr/bin/env python3
"""采样剖析原理 demo:进程内 SIGPROF 采样器(Python 版)。

模型:setitimer(ITIMER_PROF) 周期到期投递 SIGPROF -> 信号处理函数抓取
所有线程的调用栈 -> 折叠为单行("main;work;spin")计数 -> 输出 folded 样本,
可直接喂给火焰图生成器(见同目录 ../火焰图生成/go/main.go 或 python/main.py)。

对应 README「原理详解」第 1-6 步。
"""
from __future__ import annotations

import signal
import sys
import threading
import time
from collections import Counter

SAMPLE_HZ = 99          # 奇数频率:避免与周期性负载步调一致(Gregg 的经验)
INTERVAL = 1.0 / SAMPLE_HZ
DURATION = 3.0          # 采样时长(秒,墙钟;ITIMER_PROF 本身只计 CPU 时间)

_counts: Counter[str] = Counter()


def _fold_thread(frame) -> str:
    """把一条线程栈折叠为 "root;...;leaf" 单行(与 flamegraph.pl 输入格式一致)。"""
    names: list[str] = []
    f = frame
    depth = 0
    while f is not None and depth < 64:
        code = f.f_code
        names.append(f"{code.co_name}")
        f = f.f_back
        depth += 1
    # 栈是从叶到根取的,折叠输出要求根在前
    return ";".join(reversed(names))


def _on_sigprof(signum: int, frame) -> None:
    """SIGPROF 处理函数:抓线程栈并计数。

    注意:必须短。重入的 SIGPROF(同类信号只允许一个 pending)会丢失,
    这是 man7 BUGS 节明确的行为,无法在用户态消除,只能靠处理函数足够快来缓解。
    """
    for tid, f in sys._current_frames().items():
        _counts[_fold_thread(f)] += 1
        break  # 本 demo 只统计主采样视角的线程 0,其余线程栈单独聚合


def start_profiler() -> None:
    signal.signal(signal.SIGPROF, _on_sigprof)
    # setitimer 第二个参数:首次到期;第三个参数:周期间隔(均为秒)
    signal.setitimer(signal.ITIMER_PROF, INTERVAL, INTERVAL)


def stop_profiler() -> None:
    signal.setitimer(signal.ITIMER_PROF, 0.0, 0.0)  # 两字段均 0 = 拆除


# ---------------- 以下是被剖析的负载 ----------------

def _spin_kernels(n: int) -> int:
    """模拟纯 CPU 密集热点(应被采样为最宽的塔)。"""
    total = 0
    for i in range(n):
        total += (i * i) % 7
    return total


def _load_helper() -> None:
    for _ in range(200_000):
        _spin_kernels(400)


def _worker(stop_evt: threading.Event) -> None:
    """后台线程负载:占用另一部分 CPU 样本。"""
    while not stop_evt.is_set():
        _load_helper()


def run_workload(seconds: float) -> None:
    stop_evt = threading.Event()
    t = threading.Thread(target=_worker, args=(stop_evt,), name="worker")
    t.start()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _load_helper()
    stop_evt.set()
    t.join()


def main() -> int:
    print(f"[sampler] {SAMPLE_HZ} Hz ITIMER_PROF, 剖析 {DURATION:.0f}s ...")
    start_profiler()
    run_workload(DURATION)
    stop_profiler()

    total = sum(v for k, v in _counts.items())
    print(f"[sampler] 采集到 {total} 个样本(样本数 ~= CPU 时间占比)\n")
    print("---- folded 输出(可直接喂火焰图生成器)----")
    # 与 flamegraph.pl 一致:单行 "frame;frame;... 计数",按帧名字母排序
    for stack, n in sorted(_counts.items()):
        print(f"{stack} {n}")
    print("---- top 5(按样本数)----")
    for stack, n in _counts.most_common(5):
        pct = 100.0 * n / total if total else 0.0
        print(f"{pct:5.1f}%  {stack[-80:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
