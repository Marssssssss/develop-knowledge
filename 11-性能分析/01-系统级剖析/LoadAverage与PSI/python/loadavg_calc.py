#!/usr/bin/env python3
"""loadavg_calc.py — 复刻 Linux 负载平均值的计算(kernel/sched/loadavg.c 的模型)

两个反直觉事实(来自 Brendan Gregg《Linux Load Averages: Solving the Mystery》):
  1) Linux 的 loadavg 不是"CPU 负载",而是"系统负载":1993 年的一个补丁把
     TASK_UNINTERRUPTIBLE(ps/top 里的 D 状态,通常是磁盘 I/O)也算进了需求,
     因为把慢盘换成快盘理应让负载【下降】。
  2) 那三个数【不是】1/5/15 分钟的算术平均,而是以 5 秒为采样周期的
     指数衰减移动和:EXP_1=1884 / EXP_5=2014 / EXP_15=2037(FIXED_1 = 2048)。
     从空闲突然起一个满负荷线程,所谓"1 分钟平均"在 60 秒时只到约 0.62,
     因为 1 - e^(-60/60) = 0.6321。

权威依据:linux/include/linux/sched/loadavg.h 的 EXP_* 定义与
kernel/sched/loadavg.c 的 calc_load();数值与行为对照
https://www.brendangregg.com/blog/2017-08-08/linux-load-averages.html

用法: python3 loadavg_calc.py
"""

import math

FIXED_1 = 1 << 11          # 2048,定点基数
EXP_1, EXP_5, EXP_15 = 1884, 2014, 2037   # = FIXED_1 * e^(-5s/T),T = 1/5/15 min
LOAD_FREQ = 5              # 采样周期:5 秒


def calc_load(load: int, exp: int, active: int) -> int:
    """kernel/sched/loadavg.c 的 calc_load():load 与 active 都是定点数"""
    newload = load * exp + active * (FIXED_1 - exp)
    if active >= load:
        newload += FIXED_1 - 1        # 向上取整,避免长期低估
    return newload // FIXED_1


class LoadAvg:
    """对应内核里的 avenrun[];内部保存定点数,读出时除以 FIXED_1"""

    def __init__(self):
        self.fixed = [0, 0, 0]

    def update(self, active: int):
        """active = nr_running + nr_uninterruptible(D 状态)"""
        a = active * FIXED_1
        exps = (EXP_1, EXP_5, EXP_15)
        self.fixed = [calc_load(self.fixed[i], exps[i], a) for i in range(3)]
        return self.read()

    def read(self):
        return [f / FIXED_1 for f in self.fixed]


class NaiveAvg:
    """对照:同样以 5 秒采样,但做【窗口内算术平均】"""

    def __init__(self):
        self.samples = []

    def update(self, active: int):
        self.samples.append(active)

    def window_mean(self, seconds: int):
        n = max(1, seconds // LOAD_FREQ)
        win = self.samples[-n:]
        return sum(win) / len(win)


def run_scenario(title: str, schedule, duration_s: int):
    """schedule: 函数 t(秒) -> (nr_running, nr_uninterruptible)"""
    print(f"\n=== {title} ===")
    print(f"{'t(s)':>5}{'running':>9}{'D状态':>7}{'active':>8}"
          f"{'load1':>8}{'load5':>8}{'load15':>8}{'朴素1min':>10}")
    la, naive = LoadAvg(), NaiveAvg()
    checkpoints = {}
    for step in range(0, duration_s + 1, LOAD_FREQ):
        running, uninterruptible = schedule(step)
        active = running + uninterruptible
        la.update(active)
        naive.update(active)
        if step % 30 == 0 or step in (60, 300, 900):
            l1, l5, l15 = la.read()
            print(f"{step:>5}{running:>9}{uninterruptible:>7}{active:>8}"
                  f"{l1:>8.2f}{l5:>8.2f}{l15:>8.2f}{naive.window_mean(60):>10.2f}")
        checkpoints[step] = la.read()
    return checkpoints


def case_pure_cpu():
    """空闲 5 分钟后起一个 100% CPU 线程:看"1 分钟平均"如何慢慢爬"""
    def sched(t):
        return (0, 0) if t < 300 else (1, 0)
    ck = run_scenario("场景 A:空闲 300 s 后起 1 个 CPU 密集线程", sched, 420)
    l1_at_60 = ck[360][0]          # 负载开始后第 60 秒(含起点共 13 个 5 s 采样)
    print(f"  负载起点后 60 s(13 个采样)load1 = {l1_at_60:.3f};"
          f"若恰好 12 个采样,理论上限 1 - e^(-60/60) = {1 - math.exp(-1):.4f}")
    print("  -> 所以\"1 分钟平均\"远不是 1 分钟内的算术平均:它拖着一截历史")
    print("     (对照同一时刻的\"朴素 1 min 窗口平均\"= 1.00,差别就在这里)")


def case_d_state():
    """D 状态计入:磁盘 I/O 阻塞的任务会把 loadavg 推高,而 CPU 其实很闲"""
    def sched(t):
        if t < 120:
            return (0, 0)
        if t < 300:
            return (0, 2)          # 2 个任务阻塞在磁盘 I/O 上,CPU 空闲
        return (0, 0)
    ck = run_scenario("场景 B:CPU 空闲,但 2 个任务长期处于 D 状态(磁盘 I/O)", sched, 480)
    peak = max(v[0] for v in ck.values())
    print(f"  load1 峰值 = {peak:.2f},而同一时刻 nr_running = 0")
    print("  -> 这就是 1993 年那个补丁的意图:盘慢就该算进负载;")
    print("     但今天内核里近 400 条路径会置 TASK_UNINTERRUPTIBLE(含部分锁),")
    print("     所以 loadavg 偏高时,要看它究竟来自 CPU、磁盘还是锁。")


def case_commensurate_check():
    """验证定点运算与浮点 EMA 一致:α = 1 - EXP/FIXED_1"""
    print("\n=== 定点常数与浮点衰减因子的对应 ===")
    for name, exp, T in (("EXP_1", EXP_1, 60), ("EXP_5", EXP_5, 300), ("EXP_15", EXP_15, 900)):
        alpha = 1 - exp / FIXED_1
        print(f"  {name:>6} = {exp}  α = {alpha:.6f}  "
              f"理论 1 - e^(-5/{T}) = {1 - math.exp(-5 / T):.6f}  "
              f"半衰期 ≈ {T * math.log(2):.1f} s")


def read_proc_loadavg_explained():
    print("\n=== /proc/loadavg 五个字段 ===")
    print("  25.72 23.19 23.35 42/3411 43603")
    print("  ① 1 分钟  ② 5 分钟  ③ 15 分钟  "
          "④ running/total(当前可运行线程数/总线程数)  ⑤ 最近分配的 PID")
    print("  uptime / top 只显示前三列;完整信息要读 /proc/loadavg")
    print("\n=== 为什么 loadavg 会误导 ===")
    print("  · 它混合了 CPU / 磁盘 / 部分锁的需求,【不能】除以 CPU 数来判断饱和")
    print("  · 至少是 1 分钟量级的长周期平均,会掩盖变化")
    print("  · 它只说\"需求变多了\",不说\"是哪个资源\"")
    print("  · 适合的用法:与自身历史做【相对比较】(如 1min 远低于 15min => 问题可能已过去)")
    print("  · 更好的饱和度指标:运行队列延迟(bcc runqlat / /proc/PID/schedstat)、")
    print("    vmstat 的 r 列(队列长度);利用率用 mpstat -P ALL 1 / pidstat 1")


def main():
    case_commensurate_check()
    case_pure_cpu()
    case_d_state()
    read_proc_loadavg_explained()


if __name__ == "__main__":
    main()
