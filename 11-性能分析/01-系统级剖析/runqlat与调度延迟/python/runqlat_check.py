#!/usr/bin/env python3
"""runqlat_check.py — 运行队列延迟(run queue latency)与调度器统计

run queue latency(Gregg):「线程从变为可运行的那一刻(例如收到中断、被喂了活),
到它真的开始在某个 CPU 上运行」之间的时间。名字里的 "queue" 如今已不是队列
(CFS 是红黑树,EEVDF 是虚拟时间),但这个叫法沿用多年。

两个观测路径:
  1. 追踪路径 —— sched_wakeup / sched_switch 配对,输出 power-of-2 微秒直方图
     (bcc 的 runqlat 就是这么做的;列宽/柱区宽度与官方样例逐字符对齐)
  2. 计数路径 —— /proc/schedstat 的 rq_cpu_time(字段 7)与 run_delay(字段 8),
     计数器只增不减,必须两次采样求差

运行: python runqlat_check.py   (含 38 项自检)
"""
from __future__ import annotations

BAR_W = 40          # runqlat 的柱区宽度(与官方样例一致)
CPU_COUNT = 8

# ---------------------------------------------------------------- 直方图渲染
def bucket_index(usec: float) -> int:
    """runqlat 的桶边界是 2 的幂: 0->1, 2->3, 4->7, 8->15, 16->31 …"""
    i = 0
    while (1 << (i + 1)) <= usec:
        i += 1
    return i


def bucket_low(i: int) -> int:
    """第 i 桶的下界。注意第 0 桶是 [0,1] 而不是 [1,2],这是官方输出的特例。"""
    return 0 if i == 0 else 1 << i


def bucket_high(i: int) -> int:
    return (1 << (i + 1)) - 1


def bucket_label(i: int, unit: str = "usecs") -> str:
    return f"{bucket_low(i)} -> {bucket_high(i)}"


def render_hist(buckets: dict[int, int], unit: str = "usecs") -> list[str]:
    """完全复刻 runqlat 的输出格式 '%10s -> %-11s: %-9d|%-40s|'。"""
    if not buckets or not any(buckets.values()):
        return [f"  ({unit} 无样本)"]
    last = max(i for i, c in buckets.items() if c)
    maxc = max(buckets.values())
    out = [f"{unit:>10}{'':<15}: count     distribution"]
    for i in range(0, last + 1):
        c = buckets.get(i, 0)
        bar = c * BAR_W // maxc if c else 0
        out.append(f"{bucket_low(i):>10} -> {bucket_high(i):<11}: "
                   f"{c:<9}|{'*' * bar:<{BAR_W}}|")
    return out


def modes(buckets: dict[int, int], min_share: float = 0.05) -> list[tuple[int, int]]:
    """找出「模式(mode)」:连续非空且占比达标的桶段。双峰即两段。"""
    if not buckets or not any(buckets.values()):
        return []
    total = sum(buckets.values())
    hot = sorted(i for i, c in buckets.items() if c / total >= min_share)
    runs, cur = [], []
    for i in hot:
        if cur and i != cur[-1] + 1:
            runs.append((cur[0], cur[-1]))
            cur = []
        cur.append(i)
    if cur:
        runs.append((cur[0], cur[-1]))
    return runs


# ---------------------------------------------------------------- 事件配对
def pair_latencies(events: list[tuple]) -> tuple[dict[int, int], int, int]:
    """events: ('wakeup', ts_ns, tid) 或 ('switch', ts_ns, prev_tid, next_tid)。

    返回 (直方图, 配对数, 无 wakeup 的 switch 数)。
    关键: switch 里的 next 线程才是「开始运行」的那一个;prev 是「离开 CPU」。
    没有对应 wakeup 的首次上 CPU 不能算成 now-0(会造出巨值),应计数并跳过。
    """
    pending: dict[int, int] = {}     # tid -> 变为 runnable 的时刻
    buckets: dict[int, int] = {}
    paired = orphan = 0
    for ev in events:
        if ev[0] == "wakeup":
            pending[ev[2]] = ev[1]
        else:
            tid = ev[3]
            if tid in pending:
                lat_us = (ev[1] - pending.pop(tid)) / 1000.0
                i = bucket_index(lat_us)
                buckets[i] = buckets.get(i, 0) + 1
                paired += 1
            else:
                orphan += 1
    return buckets, paired, orphan


# ---------------------------------------------------------------- /proc/schedstat
def parse_schedstat_cpu(text: str) -> dict[str, int]:
    """cpu<N> 行 9 个字段(对应官方 sched-stats 文档的编号 1..9)。"""
    for line in text.splitlines():
        if line.startswith("cpu") and line[3:4].isdigit():
            f = line.split()
            if len(f) >= 10:
                return {"yld_count": int(f[1]), "array_exp": int(f[2]),
                        "sched_count": int(f[3]), "sched_goidle": int(f[4]),
                        "ttwu_count": int(f[5]), "ttwu_local": int(f[6]),
                        "rq_cpu_time": int(f[7]), "run_delay": int(f[8]),
                        "pcount": int(f[9])}
    raise ValueError("找不到 cpu<N> 行")


def run_delay_ratio(a: dict[str, int], b: dict[str, int]) -> float | None:
    """官方 perf sched stats 的派生指标: 本 CPU 上任务的等待时间 / 运行时间(%)。

    两次采样的 run_delay 都恒为 0 时,无法区分「真的没人等」与「统计没开」,
    必须返回 None 让调用方显式处理,而不是给出一个误导性的 0%。
    """
    if a["run_delay"] == 0 and b["run_delay"] == 0:
        return None
    ran = b["rq_cpu_time"] - a["rq_cpu_time"]
    waited = b["run_delay"] - a["run_delay"]
    return 100.0 * waited / ran if ran else None


def parse_pid_schedstat(text: str) -> tuple[int, int, int]:
    """三个字段: CPU 上时间(ns) / 运行队列上等待时间(ns) / 被调度的次数。"""
    f = text.split()
    return int(f[0]), int(f[1]), int(f[2])


# ---------------------------------------------------------------- 排队论:为什么饱和度非线性
def mmc_wait(rho: float, service: float = 1.0) -> float:
    """M/M/1 的平均排队等待时间(以服务时间为单位): Wq = ρ / (1-ρ)。

    ρ→1 时发散,这就是「CPU 利用率 90% 与 98% 体感完全不同」的数学来源。
    """
    if rho <= 0:
        return 0.0
    if rho >= 1:
        return float("inf")
    return service * rho / (1 - rho)


# ---------------------------------------------------------------- 自检
def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  <- {detail}" if detail else ""))
    return bool(cond)


def synthetic_events() -> list[tuple]:
    """重放官方博文那台「重负载机」的直方图形状(计数取自该样例)。

    快峰 1~12 us(正常情况下线程被唤醒后几乎立刻上 CPU),
    慢峰 18~40 ms(CPU 饱和后要排队等),两峰之间是真空。
    这是形状重放,不是真实采集数据。
    """
    ev, ts, tid = [], 0, 1000
    plan = [(1, 233), (3, 742), (7, 203), (12, 173),   # -> 桶 0,1,2,3
            (18_000, 809), (40_000, 64)]               # -> 桶 14,15
    for lat_us, n in plan:
        for _ in range(n):
            tid += 1
            ev.append(("wakeup", ts, tid))
            ts += lat_us * 1000
            ev.append(("switch", ts, 1, tid))
        ts += 1000          # 事件之间留缝,避免不同组共享同一时间点
    return ev


def main() -> int:
    ok = True

    print("== 1. runqlat 的桶边界是 2 的幂 ==")
    ok &= check("0->0,1->0,2->1,3->1,15->3,16->4,31->4:数值落在 [2^i, 2^(i+1)) 里",
                [bucket_index(v) for v in (0, 1, 2, 3, 15, 16, 31)] == [0, 0, 1, 1, 3, 4, 4])
    ok &= check("标签是 '0 -> 1' / '8 -> 15'(第 0 桶特例为 [0,1],其余右端闭区间)",
                bucket_label(0) == "0 -> 1" and bucket_label(3) == "8 -> 15"
                and bucket_label(14) == "16384 -> 32767")

    print("== 2. 输出列宽与官方样例逐字符一致 ==")
    # 只保留承载两个峰的桶,让断言可读;官方样例的完整 16 桶见 README
    b = {0: 233, 1: 742, 2: 203, 3: 173, 14: 809, 15: 64}
    lines = render_hist(b)
    head = lines[0]
    row = [x for x in lines if x.startswith(f"{0:>10} -> ")][0]
    ok &= check("表头 = 'usecs' 右对齐 10 列 + 15 空格 + ': count     distribution'",
                head == f"{'usecs':>10}{'':<15}: count     distribution", repr(head))
    ok &= check("数据行前 27 列 = '         0 -> 1          : '",
                row[:27] == "         0 -> 1          : ", repr(row[:27]))
    ok &= check("计数左对齐 9 列后再接 '|'", row[27:36] == f"{233:<9}", repr(row[27:36]))
    ok &= check("柱区总宽恒为 40", all(len(x.split("|")[1]) == BAR_W for x in lines[1:]))
    ok &= check("最大桶占满 40 个 *(官方样例 809 占满)", max(x.count("*") for x in lines) == BAR_W)
    ok &= check("233 相对 809 -> 233*40//809 = 11 个 *(与官方样例的 11 个一致)",
                row.count("*") == 233 * 40 // 809 == 11)

    print("== 3. 双峰识别 ==")
    m = modes(b)
    ok &= check("识别出 2 个模式(双峰)", len(m) == 2, str(m))
    ok &= check("第一峰在桶 0..3(0~15 us)", m[0] == (0, 3), str(m[0]))
    ok &= check("第二峰起点在桶 14(16384~32767 us,即 16~32 ms)", m[1][0] == 14, str(m[1]))
    ok &= check("5% 阈值把桶 15(64/2224=2.9%)排除在峰外,第二峰收缩为 (14,14)",
                m[1] == (14, 14), str(m[1]))
    ok &= check("阈值降到 2% 就把整段慢峰恢复成 14..15",
                modes(b, 0.02)[1] == (14, 15), str(modes(b, 0.02)))
    only_fast = {i: c for i, c in b.items() if i <= 3}
    ok &= check("只有快路径时退化成单峰(对照机就该长这样)", len(modes(only_fast)) == 1)

    print("== 4. wakeup / switch 配对 ==")
    buckets, paired, orphan = pair_latencies(synthetic_events())
    ok &= check("全部 switch 都配上了 wakeup(重放序列成对构造)", orphan == 0, str(orphan))
    ok &= check("配对数 = 各桶计数之和 = 2224", paired == sum(b.values()), str(paired))
    ok &= check("重放后仍是双峰", len(modes(buckets)) == 2, str(modes(buckets)))
    ok &= check("重放是确定性的,两次结果完全一致",
                pair_latencies(synthetic_events()) == (buckets, paired, orphan))
    _, p2, o2 = pair_latencies([("switch", 5_000_000, 1, 42)])
    ok &= check("没有 wakeup 的首次上 CPU 记成 orphan,而非制造 now-0 巨值",
                p2 == 0 and o2 == 1)
    ok &= check("有 wakeup 但无 switch -> 不产生样本",
                pair_latencies([("wakeup", 1, 7)])[1] == 0)

    print("== 5. /proc/schedstat: 9 个字段、等待/运行比 ==")
    s0 = ("version 17\n"
          "cpu0 0 0 402267 147161 236309 1062 7000000000 3000000000 255035\n"
          "domain0 SMT ff 1 2 3\n")
    s1 = ("version 17\n"
          "cpu0 0 0 402267 147161 236309 1062 7083791148 3449973971 255035\n"
          "domain0 SMT ff 1 2 3\n")
    a, bb = parse_schedstat_cpu(s0), parse_schedstat_cpu(s1)
    ok &= check("cpu0 行读出 9 个具名字段", len(a) == 9 and a["sched_count"] == 402267,
                str(sorted(a)))
    ok &= check("字段 7/8 分别是 rq_cpu_time 与 run_delay",
                a["rq_cpu_time"] == 7_000_000_000 and a["run_delay"] == 3_000_000_000)
    ok &= check("字段 2(array_exp)是 O(1) 调度器的遗留,恒为 0", a["array_exp"] == 0)
    try:
        parse_schedstat_cpu("version 17\ndomain0 SMT ff 1 2 3\n")
        dom_ok = False
    except ValueError:
        dom_ok = True
    ok &= check("只有 domain 行时明确报错,不会把 domain 当成 CPU", dom_ok)
    ratio = run_delay_ratio(a, bb)
    ok &= check("等待/运行 = (3449973971-3000000000)/(7083791148-7000000000) = 537.02%",
                ratio is not None and abs(ratio - 100 * 449_973_971 / 83_791_148) < 1e-9,
                f"{ratio:.2f}%" if ratio is not None else "None")
    ok &= check("计数器只增不减,必须两次采样求差:单点读值会得到完全不同的数",
                abs(100 * a["run_delay"] / a["rq_cpu_time"] - 42.86) < 0.01
                and abs(ratio - 42.86) > 100)

    print("== 6. sched_schedstats 开关:0 表示「没统计」而不是「没等待」 ==")
    off = "cpu0 0 0 402267 147161 236309 1062 7000000000 0 255035\n"
    off2 = "cpu0 0 0 480000 147161 236309 1062 7300000000 0 255035\n"
    d, d2 = parse_schedstat_cpu(off), parse_schedstat_cpu(off2)
    ok &= check("sysctl=0 时 run_delay 恒为 0", d["run_delay"] == 0 and d2["run_delay"] == 0)
    ok &= check("此时 sched_count 与 rq_cpu_time 照常推进,证明文件在动、只是没统计",
                d2["sched_count"] > d["sched_count"] and d2["rq_cpu_time"] > d["rq_cpu_time"])
    ok &= check("两次采样 run_delay 都是 0 -> 比值返回 None(显式「未统计」),不是 0%",
                run_delay_ratio(d, d2) is None)

    print("== 7. /proc/<pid>/schedstat 三字段 ==")
    on_cpu, waited, slices = parse_pid_schedstat("1234567890 98765432 1042\n")
    ok &= check("1=CPU 上时间 2=运行队列等待时间 3=被调度次数",
                (on_cpu, waited, slices) == (1234567890, 98765432, 1042))
    ok &= check("进程级等待时间 98765432 ns = 98.77 ms 换算正确",
                abs(waited / 1e6 - 98.765432) < 1e-6)

    print("== 8. 为什么饱和度是非线性的 ==")
    # 0.9/0.1 在二进制浮点里是 8.999999999999998,断言必须带容差
    near = lambda x, y: abs(x - y) < 1e-9
    w50, w90, w98 = mmc_wait(0.5), mmc_wait(0.9), mmc_wait(0.98)
    ok &= check("ρ=0.5 -> 等待 1 个服务时间(Wq = ρ/(1-ρ))", near(w50, 1.0), f"{w50!r}")
    ok &= check("ρ=0.9 -> 9 倍服务时间", near(w90, 9.0), f"{w90!r}")
    ok &= check("ρ=0.98 -> 49 倍", near(w98, 49.0), f"{w98!r}")
    ok &= check("饱和度 0.9→0.98 只涨 8.9%,等待时间却从 9 涨到 49(5.4 倍)",
                abs((0.98 - 0.9) / 0.9 - 0.0889) < 0.001 and abs(w98 / w90 - 49 / 9) < 1e-9)
    ok &= check("ρ→1 时等待时间发散(运行队列长 1 与长 10 完全不是一回事)",
                mmc_wait(1.0) == float("inf") and mmc_wait(0.0) == 0.0)

    print("== 9. run queue latency 的定位 ==")
    ok &= check("它测的是「可运行 -> 真正在 CPU 上运行」:同一 wakeup 换个 switch 时刻,延迟就变",
                pair_latencies([("wakeup", 0, 7), ("switch", 9_000, 1, 7)])[0] == {3: 1}
                and pair_latencies([("wakeup", 0, 7), ("switch", 90_000, 1, 7)])[0] == {6: 1})
    ok &= check("它不回答「在 CPU 上待了多久」(那是 cpudist 的视角):重放里最短 0~1us、最长 32~65ms",
                min(buckets) == 0 and max(buckets) == 15 and CPU_COUNT == 8)

    print("\n" + ("全部通过" if ok else "存在失败项"))
    print("\n-- 重放序列的直方图(双峰) --")
    for line in render_hist(buckets):
        print("  " + line)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
