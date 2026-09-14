#!/usr/bin/env python3
"""pmc_multiplex.py — 模拟 PMU 多路复用(multiplexing)与计数缩放误差

问题:硬件计数器槽位有限(本 demo 取 4),事件一多就必须轮转 —— 事件只在一部分
时间真的挂在计数器上。perf_event_open(2) 给的解法是用
    count = quot * time_enabled + (rem * time_enabled) / time_running
做缩放估算,其中 quot = count / time_running, rem = count % time_running。

关键洞察(本 demo 想让你看到的两件事):
  1) 缩放【只在"事件速率平稳"的假设下】才准。真实负载是突发的,而多路复用意味着
     我们只观测到一部分时间窗口 —— 用一个窗口的速率去外推整段时间,必然有误差。
  2) 事件率极低时,int(round(rate*quantum)) 会把计数截断成 0,缩放救不回来
     (0 * 任何系数 = 0)。

约束来自 man7 perf_event_open(2):
  * 组(group)作为一个整体被调度,只有整组都能上 CPU 才会上 —— 组内比值才可信
  * time_enabled = 事件被启用的总时间,time_running = 真正在计数器上的时间
  * 两者不相等 => 发生了多路复用,必须缩放;只在此刻才需要缩放

用法: python3 pmc_multiplex.py
"""

from dataclasses import dataclass, field

PMC_SLOTS = 4          # 通用计数器槽位(真实数量由微架构决定,此处固定为 4)
TOTAL_US = 100_000     # 测量窗口:100 ms
QUANTUM_US = 100       # 调度量子:每 100 us 重排一次(近似内核轮转周期)
BURST_PERIOD = 3_300   # 负载节拍:每 3.3 ms 在高/低档之间切换(刻意与轮转周期不可通约)


@dataclass
class Event:
    group: str
    name: str
    base_rate: float            # 低档速率(次/us)
    burst_mult: float = 1.0     # 高档速率 = base_rate * burst_mult
    phase: int = 0              # 各事件的高档相位不同(负载不是齐步走的)
    enabled_us: int = 0
    running_us: int = 0
    count: float = 0.0          # read(2) 拿到的原始计数
    true_count: float = 0.0     # 同一时间函数的理想累计(一直挂在计数器上时的值)

    def rate_at(self, t: int) -> float:
        """突发的负载:每 BURST_PERIOD 在高档与低档之间切换"""
        hot = ((t + self.phase) // BURST_PERIOD) % 2
        return self.base_rate * (self.burst_mult if hot else 1.0)


@dataclass
class Group:
    name: str
    events: list = field(default_factory=list)


def build_scenario():
    """4 个组共 9 个事件 > 4 个槽位 => 必然多路复用"""
    specs = [  # (组, [(事件, 基础速率, 突发倍数)])
        ("G0", [("CPU_CYCLES", 3.0, 3.0)]),
        ("G1", [("INSTRUCTIONS", 2.4, 2.5), ("CACHE_REFERENCES", 0.9, 4.0)]),
        ("G2", [("CACHE_MISSES", 0.05, 20.0), ("BRANCH_INSTRUCTIONS", 0.4, 3.0),
                ("BRANCH_MISSES", 0.02, 25.0)]),
        ("G3", [("PAGE_FAULTS_MIN", 0.03, 10.0), ("PAGE_FAULTS_MAJ", 0.001, 50.0),
                ("CONTEXT_SWITCHES", 0.002, 30.0)]),
    ]
    groups = []
    idx = 0
    for gname, evs in specs:
        g = Group(gname)
        for name, base, mult in evs:
            idx += 1
            g.events.append(Event(gname, name, base, mult, phase=idx * 700))
        groups.append(g)
    return groups


def simulate(groups, slots=PMC_SLOTS, quantum=QUANTUM_US, total=TOTAL_US):
    """轮转放置:每个量子从不同起点尝试把【整组】放进槽位。

    真实内核用更复杂的灵活调度,这里只保留两个本质约束:组原子性 + 槽位容量。
    同时累计 true_count(理想值:一直挂在计数器上会数到多少)。
    """
    log, cursor = [], 0
    for t in range(0, total, quantum):
        for g in groups:
            for e in g.events:
                e.true_count += e.rate_at(t) * quantum
                e.enabled_us += quantum
        used, placed = 0, []
        for k in range(len(groups)):
            g = groups[(cursor + k) % len(groups)]
            if used + len(g.events) <= slots:
                used += len(g.events)
                placed.append(g.name)
                for e in g.events:
                    e.running_us += quantum
                    e.count += e.rate_at(t) * quantum
        cursor = (cursor + 1) % len(groups)
        if len(log) < 6:
            log.append((t, placed, used))
    return log


def scale_integer(count: float, enabled: int, running: int) -> int:
    """man7 perf_event_open(2) 给出的整数缩放公式(与 rdpmc 那段代码一致)"""
    if running <= 0:
        return 0
    c = int(round(count))
    quot, rem = divmod(c, running)
    return quot * enabled + (rem * enabled) // running


def scale_float(count: float, enabled: int, running: int) -> float:
    return count * enabled / running if running else 0.0


def report(groups):
    rows = [e for g in groups for e in g.events]
    print(f"=== 场景:{len(groups)} 组 / {len(rows)} 事件,槽位 {PMC_SLOTS} 个"
          f" => 必然多路复用 ===")
    print(f"窗口 {TOTAL_US} us,量子 {QUANTUM_US} us,负载每 {BURST_PERIOD} us 切换一次高低档\n")
    print(f"{'event':<20}{'真值':>11}{'测量值':>11}{'缩放(整)':>11}{'缩放(浮)':>11}"
          f"{'enabled/run':>12}{'误差%':>9}")
    errs = []
    for e in rows:
        si = scale_integer(e.count, e.enabled_us, e.running_us)
        sf = scale_float(e.count, e.enabled_us, e.running_us)
        ratio = e.enabled_us / e.running_us if e.running_us else float("inf")
        err = 100.0 * (sf - e.true_count) / e.true_count if e.true_count else 0.0
        errs.append(abs(err))
        print(f"{e.name:<20}{e.true_count:>11.1f}{e.count:>11.1f}{si:>11d}{sf:>11.1f}"
              f"{ratio:>12.3f}{err:>9.2f}")
    print(f"\n平均绝对误差 = {sum(errs) / len(errs):.2f}%")
    print("误差来源:① 只观测了部分时间窗口,而负载在变 —— 用窗口内速率外推整段时间必然失真")
    print("          ② 各事件的 running 窗口不同,跨事件的比值(如未命中率)误差被进一步放大")

    def scaled(name):
        e = next(x for x in rows if x.name == name)
        return scale_float(e.count, e.enabled_us, e.running_us)

    cyc, ins = scaled("CPU_CYCLES"), scaled("INSTRUCTIONS")
    ref, mis = scaled("CACHE_REFERENCES"), scaled("CACHE_MISSES")
    bref, bmis = scaled("BRANCH_INSTRUCTIONS"), scaled("BRANCH_MISSES")
    true_cyc = next(x for x in rows if x.name == "CPU_CYCLES").true_count
    true_ins = next(x for x in rows if x.name == "INSTRUCTIONS").true_count

    print("\n=== 派生指标(缩放后 vs 真值)===")
    print(f"  IPC        缩放 {ins / cyc:.3f}   真值 {true_ins / true_cyc:.3f}")
    print(f"  CPI        缩放 {cyc / ins:.3f}   真值 {true_cyc / true_ins:.3f}")
    print(f"  LLC 未命中率 缩放 {100 * mis / ref:.2f}%")
    print(f"  分支误预测率 缩放 {100 * bmis / bref:.2f}%")


def no_multiplex_case():
    """事件数 <= 槽位:time_enabled == time_running,缩放是恒等变换"""
    groups = build_scenario()[:1]
    groups[0].events.extend([Event("A", "INSTRUCTIONS", 2.4, 2.5)])
    for e in groups[0].events:
        e.running_us = e.enabled_us = TOTAL_US
        e.true_count = sum(e.rate_at(t) * QUANTUM_US for t in range(0, TOTAL_US, QUANTUM_US))
        e.count = e.true_count
    print("\n=== 对照组:2 个事件 <= 4 槽位,无多路复用 ===")
    for e in groups[0].events:
        si = scale_integer(e.count, e.enabled_us, e.running_us)
        print(f"  {e.name:<16} enabled==running=={e.enabled_us} us  "
              f"count={e.count:.1f}  缩放后={si}  恒等={abs(si - e.count) < 1}")


def main():
    groups = build_scenario()
    log = simulate(groups)
    print("前 6 个量子的放置结果(组原子:整组放得下才放):")
    for t, placed, used in log:
        print(f"  t={t:>6} us  放置 {','.join(placed) or '(空)':<10} 占用槽位 {used}/{PMC_SLOTS}")
    print()
    report(groups)
    no_multiplex_case()
    print("\n结论:多路复用下 read(2) 给的原始计数【只是部分时间】的累计,")
    print("      必须用 time_enabled/time_running 缩放;组内比值可信、跨组比值要谨慎。")
    print("      实践建议:事件数控制在槽位以内(或固定 CPU + 关闭其他计数器),比对时看缩放比。")


if __name__ == "__main__":
    main()
