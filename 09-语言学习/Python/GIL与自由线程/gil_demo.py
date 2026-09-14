#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CPython GIL 与自由线程(PEP 703)教学 demo。

  §1 运行时事实:切换间隔、递归限制、是否已禁用 GIL
  §2 CPU 密集:线程不加速(GIL 串行),进程/并行才加速
  §3 I/O 密集:线程可重叠(等待期间释放 GIL)
  §4 复合操作不是原子的:切换间隔决定竞争窗口
  §5 自由线程构建自检(PEP 703 的 --disable-gil / PYTHONGIL / ABI 't')

来源:docs.python.org/3/library/sys.html、PEP 703、PEP 684。数值为本机实测(3.13 标准构建,
16 逻辑核),不同机器/版本结果会变;demo 只断言"数量级"与"结论方向"。
运行:python gil_demo.py
"""

import concurrent.futures as futures
import os
import sys
import sysconfig
import threading
import time

SEP = "=" * 74
WORK = 12_000_000         # 单份 CPU 工作的迭代数(本机约 0.4~0.5 秒)


def header(title):
    print(f"\n{SEP}\n{title}\n{SEP}")


def cpu_work(n=WORK):
    """纯 Python 计算:没有 I/O、没有显式释放 GIL 的机会。"""
    total = 0
    for i in range(n):
        total += (i * i) % 7
    return total


# ---------------------------------------------------------------------------
# §1 运行时事实
# ---------------------------------------------------------------------------
def demo_facts():
    header("[1] 运行时事实:切换间隔、递归限制、GIL 状态")
    print(f"  Python {sys.version.split()[0]} / 逻辑核数 {os.cpu_count()}")
    print(f"  sys.getswitchinterval() = {sys.getswitchinterval()} 秒(线程时间片的理想长度)")
    print(f"  sys.getrecursionlimit() = {sys.getrecursionlimit()}(防止无限递归撑爆 C 栈)")
    print("  sys 文档:切换间隔的文档值不由解释器兑现 —— 'the actual value can be higher ..."
          " which thread becomes scheduled at the end of the interval is the operating"
          " system's decision. The interpreter doesn't have its own scheduler.'")
    print(f"  sys._is_gil_enabled() = {sys._is_gil_enabled()}(标准构建恒为 True)")
    print("  GIL 的定义(PEP 703):'CPython's global interpreter lock (\"GIL\") prevents multiple"
          " threads from executing Python code at the same time.'")


def _timed(fn):
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def run_threads(fn, count):
    """起 count 个线程跑同一函数,返回墙钟耗时。"""
    threads = [threading.Thread(target=fn) for _ in range(count)]
    begin = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return time.perf_counter() - begin


# ---------------------------------------------------------------------------
# §2 CPU 密集
# ---------------------------------------------------------------------------
def demo_cpu_bound():
    header("[2] CPU 密集:多线程被 GIL 串行化,多进程才能吃到多核")
    base, _ = _timed(cpu_work)
    print(f"  单线程一次 cpu_work():{base:.3f}s(基准)")

    n = 4
    thread_time = run_threads(cpu_work, n)
    with futures.ProcessPoolExecutor(max_workers=n) as pool:
        proc_time, _ = _timed(lambda: list(pool.map(cpu_work, [WORK] * n)))
    print(f"  {n} 线程总耗时 {thread_time:.3f}s -> 相对串行加速比 {base * n / thread_time:.2f}x")
    print(f"  {n} 进程总耗时 {proc_time:.3f}s -> 相对串行加速比 {base * n / proc_time:.2f}x")
    print("  结论:线程版加速比贴近 1x(同一时刻只有一个线程在执行 Python 代码),"
          "进程版加速明显(每个进程各有自己的解释器与 GIL),但要额外付出进程启动与序列化代价")
    print(f"  (进程版含 ProcessPoolExecutor 的启动/结果回传开销,共 {n} 个任务)")


# ---------------------------------------------------------------------------
# §3 I/O 密集
# ---------------------------------------------------------------------------
def demo_io_bound():
    header("[3] I/O 密集:线程在等待时释放 GIL,所以能重叠")
    delay = 0.25
    base, _ = _timed(lambda: time.sleep(delay))
    begin = time.perf_counter()
    threads = [threading.Thread(target=time.sleep, args=(delay,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - begin
    print(f"  单线程 sleep({delay}) = {base:.3f}s;8 线程各 sleep({delay}) 共 {elapsed:.3f}s")
    print(f"  总等待量 {delay * 8:.2f}s 压缩到 {elapsed:.3f}s(重叠比 {delay * 8 / elapsed:.1f}x)")
    print("  PEP 703 对替代方案的说明:'The multiprocessing library ... allows for parallelism"
          " because each subprocess has its own Python interpreter';"
          " 并给出代价:'Starting a thread takes ~100 us, while spawning a subprocess takes"
          " ~50 ms (50,000 us) due to Python re-initialization.'")


# ---------------------------------------------------------------------------
# §4 复合操作不是原子的
# ---------------------------------------------------------------------------
def demo_atomicity():
    header("[4] 复合操作不是原子的:切换点出现在哪里,决定是否丢更新")
    n = 4
    per_thread = 1_000_000
    for interval in (0.005, 0.000001):
        sys.setswitchinterval(interval)
        counter = 0

        def tight():
            nonlocal counter
            for _ in range(per_thread):
                counter += 1

        run_threads(tight, n)
        print(f"  (a) 紧凑的 counter += 1,切换间隔 {interval:<9} 秒:丢失 "
              f"{per_thread * n - counter} / {per_thread * n}")
    sys.setswitchinterval(0.005)
    print("      原因:CPython 只在带中断检查的字节码上才可能切换线程,典型是循环回跳 ——"
          " dis 文档:JUMP_BACKWARD 'Checks for interrupts.' 而 JUMP_BACKWARD_NO_INTERRUPT"
          " 'Does not check for interrupts.' 紧凑循环里的 += 因此极少被从中间打断")

    per_thread = 50_000
    counter = 0

    def racy():
        nonlocal counter
        for _ in range(per_thread):
            tmp = counter
            time.sleep(0)          # 释放 GIL 并立即让出:给临界区制造必然的切开点
            counter = tmp + 1

    run_threads(racy, n)
    print(f"  (b) 临界区内插入 time.sleep(0):丢失 {per_thread * n - counter} / "
          f"{per_thread * n} —— 只要临界区里出现任何释放 GIL 的点(调用 C 函数、I/O、sleep),"
          "丢失更新立刻大量出现")
    print("  结论:不要依赖'+= 大概安全'。跨线程共享可变状态请用 Lock / queue / 原子结构;"
          "GIL 只保证单条字节码不被切开,不保证复合操作的原子性")


# ---------------------------------------------------------------------------
# §5 自由线程自检
# ---------------------------------------------------------------------------
def demo_free_threading():
    header("[5] 自由线程(PEP 703)自检:当前构建是否禁用 GIL")
    flag = sysconfig.get_config_var("Py_GIL_DISABLED")
    print(f"  sysconfig.get_config_var('Py_GIL_DISABLED') = {flag}")
    print(f"  sys._is_gil_enabled() = {sys._is_gil_enabled()} -> "
          f"{'当前启用 GIL(标准构建)' if sys._is_gil_enabled() else '当前禁用 GIL(自由线程构建)'}")
    print("  PEP 703:'The global interpreter lock will remain the default for CPython builds"
          " and python.org downloads.' 只有 configure 时加 --disable-gil 才会生成自由线程构建:")
    print("    - 定义宏 Py_GIL_DISABLED;ABI 标签加字母 't'(threading)")
    print("    - 运行期可用环境变量 PYTHONGIL=0/1 或模块槽 Py_mod_gil 覆盖")
    print("    - 实现要点:偏向引用计数(ob_ref_local/ob_ref_shared/ob_tid)、延迟引用计数、"
          "immortal 对象、mimalloc、每对象轻量锁 + 临界区(类 RCU)")
    print("    - 代价:单线程约 5~8% 开销,主要来自偏向引用计数与每对象锁")
    print("    - 与 PEP 684(每解释器 GIL)目标重叠但取舍不同,可并存")


def main():
    demo_facts()
    demo_cpu_bound()
    demo_io_bound()
    demo_atomicity()
    demo_free_threading()
    print("\n全部章节执行完毕。")


if __name__ == "__main__":
    main()
