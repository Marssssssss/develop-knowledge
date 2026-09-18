#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""连接池容量规划：把 HikariCP 官方 wiki 的两条结论（经验公式 + pool-locking 下界）程序化。

口径来源（先联网实读再写）：HikariCP wiki "About Pool Sizing"
（https://github.com/brettwooldridge/HikariCP/wiki/About-Pool-Sizing）。文档给出的关键结论：
  - 公式（转述自 PostgreSQL 项目）："connections = ((core_count * 2) + effective_spindle_count)"，
    "Core count should not include HT threads, even if hyperthreading is enabled."；
  - 四核 + 单盘的算例："9 = ((4 * 2) + 1). Call it 10 as a nice round number."；
  - 反直觉点："reducing the connection pool size alone ... decreased the response times of the
    application from ~100ms to ~2ms -- over 50x improvement"，以及 "Once the number of threads
    exceeds the number of CPU cores, you're going slower by adding more threads, not faster."；
  - SSD 的推论："Faster, no seeks, no rotational delays means less blocking and therefore fewer
    threads [closer to core count] will perform better than more threads."；
  - 目标形态："You want a small pool, saturated with threads waiting for connections."；
  - pool-locking 下界："pool size = Tn x (Cm - 1) + 1"，例：Tn=3/Cm=4 → 10；Tn=8/Cm=3 → 17，
    并注明 "This is not necessarily the optimal pool size, but the minimum required to avoid deadlock."

本文件用一个**显式的简化队列模型**复现上列定性结论：K 个核时间片轮转、每个请求 = C 个 CPU
tick + W 个 I/O tick（I/O 不占 CPU），并发请求数超过核数时按比例扣除上下文切换税。
这是"形状复现"，不是数据库基准 —— 数值结论以 README 标注为准。
运行：python3 pool_sizing.py （仅标准库）
"""
from __future__ import annotations

SWITCH_TAX = 0.2          # 每个超出核数的并发任务，每 tick 扣除的核时间片比例


def formula_pool_size(core_count: int, spindle_count: int) -> int:
    """官方公式：((core_count * 2) + effective_spindle_count)，HT 线程不计入 core_count。"""
    if core_count <= 0 or spindle_count < 0:
        raise ValueError("core_count 必须为正、spindle_count 不可为负")
    return core_count * 2 + spindle_count


def pool_locking_min(threads: int, max_conns: int) -> int:
    """官方资源分配公式：pool size = Tn x (Cm - 1) + 1（避免死锁的最小值）。"""
    if threads <= 0 or max_conns <= 0:
        raise ValueError("threads / max_conns 必须为正")
    return threads * (max_conns - 1) + 1


def simulate(cores: int, pool: int, cpu_ticks: int, io_ticks: int,
             ticks: int = 400, switch_tax: float = SWITCH_TAX) -> float:
    """返回稳态吞吐（完成的请求数 / tick）。模型说明见文件头。

    每 tick 的推进顺序：① 池未满则补入新请求（模拟「客户端线程阻塞在池上」）；
    ② 已过 CPU 阶段的请求推进 I/O（不占核）；③ 把可用核时间片按队列顺序发给需要 CPU 的请求；
    ④ 收尾：CPU 与 I/O 都归零的请求记为完成并移出。
    """
    in_flight: list[list[int]] = []       # [剩余 CPU tick, 剩余 I/O tick]
    done = 0
    for _ in range(ticks):
        while len(in_flight) < pool:
            in_flight.append([cpu_ticks, io_ticks])
        waste = min(float(cores), (len(in_flight) - cores) * switch_tax) if len(in_flight) > cores else 0.0
        grants = max(0, int(cores - waste + 1e-9))
        for task in in_flight:                        # ② I/O 并行推进
            if task[0] == 0 and task[1] > 0:
                task[1] -= 1
        for task in in_flight:                        # ③ 核时间片（只在 CPU 阶段）
            if grants > 0 and task[0] > 0:
                task[0] -= 1
                grants -= 1
        rest = []
        for task in in_flight:                        # ④ 收尾与完成计数
            if task[0] == 0 and task[1] == 0:
                done += 1
            else:
                rest.append(task)
        in_flight = rest
    return done / ticks


def sweep(cores: int, sizes: list[int], cpu_ticks: int, io_ticks: int) -> dict[int, float]:
    return {p: round(simulate(cores, p, cpu_ticks, io_ticks), 4) for p in sizes}


def pool_locking_trace(threads: int, max_conns: int, pool: int) -> tuple[int, bool]:
    """模拟「每个线程一次只申请一个连接、持满 Cm 个才开始干活并释放」的最坏交错。

    返回 (完成线程数, 是否死锁)。死锁判据：某一轮里没有任何线程拿到新连接、
    也没有线程达到 Cm —— 官方公式要防的正是「全部线程各持 Cm-1 个」的僵局。
    每个线程只计一次完成（完成后不再参与后续轮次）。
    """
    held = [0] * threads
    finished = [False] * threads
    free = pool
    completed = 0
    for _ in range(max_conns * threads + 8):     # 轮数上限：足够走完正常流程
        gained = 0
        released = 0
        for i in range(threads):
            if finished[i]:
                continue
            if free > 0:
                held[i] += 1
                free -= 1
                gained += 1
            if held[i] == max_conns:             # 持满 → 干活 → 释放
                free += held[i]
                held[i] = 0
                finished[i] = True
                completed += 1
                released += 1
        if gained == 0 and released == 0:
            break                                # 僵局
    return completed, completed < threads


def peak_concurrency(pool: int, clients: int) -> int:
    """同时在飞的请求数 = min(池大小, 客户端线程数)；其余线程阻塞在池上等待。"""
    return min(pool, clients)


def main() -> int:
    ok, bad = 0, 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok, bad
        if cond:
            ok += 1
            print(f"  [PASS] {label}")
        else:
            bad += 1
            print(f"  [FAIL] {label} :: {detail}")

    print("=" * 74)
    print("1) 官方经验公式 connections = ((core_count * 2) + effective_spindle_count)")
    check("4 核 + 1 盘 → 9（官方算例）", formula_pool_size(4, 1) == 9, str(formula_pool_size(4, 1)))
    check("官方建议取整为 10", formula_pool_size(4, 1) + 1 == 10)
    check("SSD / 全命中缓存（spindle=0）→ 8，比机械盘更少",
          formula_pool_size(4, 0) == 8 and formula_pool_size(4, 0) < formula_pool_size(4, 1))
    check("16 核 + 1 盘 → 33（官方说的“96 已经偏高”同向）", formula_pool_size(16, 1) == 33,
          str(formula_pool_size(16, 1)))
    check("HT 线程不计入 core_count（8 核 16 线程按 8 算）", formula_pool_size(8, 1) == 17)

    print("\n2) 简化队列模型：吞吐随池大小的形状（K=4，每请求 5 CPU tick + 20 I/O tick）")
    sizes = [1, 2, 4, 6, 8, 10, 16, 32, 100, 1000]
    curve = sweep(4, sizes, cpu_ticks=5, io_ticks=20)
    print("     P -> 吞吐:", {k: v for k, v in curve.items()})
    best = max(curve, key=lambda p: curve[p])
    check("峰值出现在「略大于核数」的小池区间（4 < P* ≤ 16）", 4 < best <= 16, f"best={best}")
    check("P=10 达到峰值的 90% 以上", curve[10] >= 0.9 * curve[best],
          f"{curve[10]} vs {curve[best]}")
    check("P=4（等于核数）不是最优：I/O 阻塞没被其他请求填上", curve[4] < curve[best],
          f"{curve[4]} vs {curve[best]}")
    check("P=1000 远差于 P=10（官方 '1000 still horrible'）", curve[1000] < 0.1 * curve[10],
          f"{curve[1000]} vs {curve[10]}")
    check("P=100 已经比小池差（官方 'Even 100 connections, overkill'）", curve[100] < curve[10],
          f"{curve[100]} vs {curve[10]}")

    print("\n3) 阻塞越少，最优池越小（SSD 推论的形状复现）")
    cpu_only = sweep(4, sizes, cpu_ticks=5, io_ticks=0)          # 纯 CPU：无 I/O 阻塞
    print("     P -> 吞吐:", {k: v for k, v in cpu_only.items()})
    best_cpu = max(cpu_only, key=lambda p: cpu_only[p])
    check("纯 CPU 场景下最优池 = 核数(4)", best_cpu == 4, f"best={best_cpu}")
    check("纯 CPU 下超过核数即变慢（官方 'going slower by adding more threads'）",
          cpu_only[8] < cpu_only[4] and cpu_only[16] < cpu_only[8],
          f"{cpu_only[4]}/{cpu_only[8]}/{cpu_only[16]}")
    io_heavy = sweep(4, sizes, cpu_ticks=5, io_ticks=60)
    best_io = max(io_heavy, key=lambda p: io_heavy[p])
    check("I/O 占比更高时最优池比纯 CPU 大（阻塞创造机会）", best_io >= 8, f"best={best_io}")

    print("\n4) pool-locking 下界：pool size = Tn x (Cm - 1) + 1")
    check("Tn=3 / Cm=4 → 10（官方算例）", pool_locking_min(3, 4) == 10, str(pool_locking_min(3, 4)))
    check("Tn=8 / Cm=3 → 17（官方算例）", pool_locking_min(8, 3) == 17, str(pool_locking_min(8, 3)))
    done9, dead9 = pool_locking_trace(3, 4, 9)
    check("池=9（下界-1）→ 3 个线程全部卡死，完成 0", done9 == 0 and dead9, f"done={done9}")
    done10, dead10 = pool_locking_trace(3, 4, 10)
    check("池=10（下界）→ 全部完成且不死锁", done10 == 3 and not dead10, f"done={done10}")
    done5, _ = pool_locking_trace(3, 4, 5)
    check("池=5（远低于下界）同样死锁", done5 == 0, f"done={done5}")
    done17, dead17 = pool_locking_trace(8, 3, 17)
    check("Tn=8 / Cm=3 时池=17 恰好不死锁", done17 == 8 and not dead17, f"done={done17}")

    print("\n5) 「小池 + 排队」才是目标形态")
    check("3000 个前端用户 / 池=10 → 同时在飞请求只有 10，其余阻塞在池上",
          peak_concurrency(10, 3000) == 10, str(peak_concurrency(10, 3000)))
    check("池=1000 时 1000 个请求同时压向数据库（远超核数）",
          peak_concurrency(1000, 3000) == 1000 and peak_concurrency(1000, 3000) > 4 * 8)
    check("下界（防死锁）与最优（吞吐）是两个不同的数：10 vs 峰值区间 5~16",
          pool_locking_min(3, 4) == 10 and 4 < best <= 16)

    print("\n" + "=" * 74)
    print(f"断言结果：pass={ok} fail={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
