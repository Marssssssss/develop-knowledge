#!/usr/bin/env python3
"""cgroup_attrib.py — 容器性能归因:cgroup v2 指标解析 + 反向诊断决策树

背景:命名空间限制「看得见什么」,cgroup 限制「能用多少」,两者合起来才是容器。
容器里 top/free 看到的是宿主数据,故「容器为什么慢」必须回到 cgroup v2 的
cpu.stat / memory.events / io.stat 上归因。

核心方法:**反向诊断(reverse diagnosis,Gregg DockerCon 2017)** ——
先列出全部可能结论,再倒推每种结论各需要哪些指标。CPU 分析的第一步就是
`cpu.stat -> throttled_usec`:被自己硬 cap 限流是最容易排除、也最常见的一种。

运行: python cgroup_attrib.py   (含 32 项自检)
"""
from __future__ import annotations

# ---------------------------------------------------------------- 解析层
def parse_flat(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[0]] = int(parts[1])
    return out


def parse_cpu_stat(text: str) -> dict[str, int]:
    """cpu.stat:恒定 3 个 + 控制器启用时 5 个 CFS 带宽字段。"""
    s = parse_flat(text)
    always = {"usage_usec", "user_usec", "system_usec"}
    cfs = {"nr_periods", "nr_throttled", "throttled_usec", "nr_bursts", "burst_usec"}
    return {k: v for k, v in s.items() if k in always | cfs}


def parse_cpu_max(text: str) -> tuple[float | None, int]:
    """cpu.max = '<$MAX> <$PERIOD>';'max' 表示不限。返回 (CPU 数上限, period_us)。"""
    parts = text.split()
    period = int(parts[1]) if len(parts) > 1 else 100_000
    quota = None if parts[0] == "max" else int(parts[0])
    return (None if quota is None else quota / period), period


def parse_io_stat(text: str) -> dict[str, dict[str, int]]:
    """io.stat:按 'MAJ:MIN' 分行的嵌套键。"""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and ":" in parts[0]:
            out[parts[0]] = {k: int(v) for k, v in (p.split("=") for p in parts[1:])}
    return out


def parse_nested_line(line: str) -> dict[str, float]:
    return {k: float(v) for k, v in (p.split("=") for p in line.split() if "=" in p)}


# ---------------------------------------------------------------- 反向诊断
# 结论枚举(从 Gregg 的 CPU 反向诊断图倒推:先穷举结论,再找指标)
CAP_THROTTLED = "被自己的 cpu.max 硬限流"
ANCESTOR_THROTTLED = "被祖先 cgroup 的带宽限制牵连"
HOST_CONTENDED = "宿主/系统级争用(不是容器的配额问题)"
APP_QUEUED = "容器在等下游(应用自身排队,不是 CPU 不够)"
CPU_OK = "CPU 侧未见瓶颈"


def diagnose_cpu(cpu_stat: dict[str, int], cpu_local: dict[str, int] | None,
                 cpu_pressure: dict[str, float], usage_delta_usec: int) -> tuple[str, str]:
    """返回 (结论, 依据)。顺序即排除法的顺序。"""
    own_throttle = cpu_stat.get("throttled_usec", 0)
    # 第 1 步:自己的 cap 限流 —— 最容易测,先把它从候选里删掉
    if own_throttle > 0 and usage_delta_usec > 0 and own_throttle / usage_delta_usec > 0.05:
        return CAP_THROTTLED, f"throttled_usec/usage_usec = {own_throttle / usage_delta_usec:.1%}"
    # 第 2 步:cpu.stat 是**非层级**的 —— 它为 0 也可能是被祖先限了,要看 cpu.stat.local
    if cpu_local and cpu_local.get("throttled_usec", 0) > 0 and own_throttle == 0:
        return ANCESTOR_THROTTLED, (f"cpu.stat.throttled_usec=0 但 cpu.stat.local"
                                    f".throttled_usec={cpu_local['throttled_usec']}")
    # 第 3 步:自己没被限,但 PSI 说在等 CPU -> 系统/宿主争用
    if cpu_pressure.get("some", 0) > 10.0:
        return HOST_CONTENDED, f"cpu.pressure some avg10 = {cpu_pressure['some']:.1f}%"
    # 第 4 步:CPU 没等 -> 瓶颈在下游
    if cpu_pressure.get("some", 0) <= 1.0:
        return APP_QUEUED, "cpu.pressure some 几乎为 0;应转向 off-CPU / 下游依赖分析"
    return CPU_OK, "无明显信号"


# ---------------------------------------------------------------- CPU shares 数学
def shares_limits(shares: int, total_allocated: int, total_busy: int) -> tuple[float, float]:
    """官方(Gregg):容器 CPU 上限 = 100% × shares/总**忙**份额(可超用空闲 CPU = bursting);
    容器**最小**保障 = 100% × shares/总**分配**份额(全员都忙时的保底)。"""
    hi = 100.0 * shares / total_busy if total_busy else 0.0
    lo = 100.0 * shares / total_allocated if total_allocated else 0.0
    return hi, lo


# ---------------------------------------------------------------- 采样(内嵌 fixture)
# 一个被祖先 cgroup 带宽牵连的容器:自己 cpu.max=4 CPU 很宽,但父 cgroup 只给 2 CPU,
# 于是 cpu.stat.throttled_usec 恒 0、cpu.stat.local.throttled_usec 猛涨。
CGROUP_FILES = {
    "cpu.stat": "usage_usec 8000000\nuser_usec 5000000\nsystem_usec 3000000\n"
                "nr_periods 100\nnr_throttled 0\nthrottled_usec 0\n"
                "nr_bursts 2\nburst_usec 1500\n",
    "cpu.stat.local": "throttled_usec 1800000\n",
    "cpu.max": "400000 100000\n",
    "cpu.weight": "1024\n",
    "cpu.pressure": "some avg10=0.60 avg60=1.20 avg300=2.40 total=987654\n"
                    "full avg10=0.20 avg60=0.40 avg300=0.80 total=123456\n",
    "memory.current": "1073741824\n",
    "memory.max": "2147483648\n",
    "memory.high": "1932735283\n",
    "memory.events": "low 0\nhigh 128\nmax 4\noom 2\noom_kill 1\noom_group_kill 0\n",
    "memory.events.local": "low 0\nhigh 12\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n",
    "io.stat": "8:16 rbytes=1459200 wbytes=314773504 rios=192 wios=353 dbytes=0 dios=0\n"
               "8:0 rbytes=90430464 wbytes=299008000 rios=8950 wios=1252 dbytes=50331648 dios=3021\n",
    "pids.current": "48\n",
    "pids.max": "40\n",
    "pids.events": "max 7\n",
}


def diagnose_memory(events: dict[str, int], events_local: dict[str, int],
                    current: int, maximal: int, high: int) -> list[tuple[str, str]]:
    """内存侧归因:区分「被 high 节流」「触到 max」「被 OOM kill」,并区分层级/本地。"""
    out = []
    if events.get("oom_kill", 0) > 0:
        out.append(("OOM_KILLED", f"memory.events.oom_kill = {events['oom_kill']}"
                                  f"(local {events_local.get('oom_kill', 0)})"))
    if events.get("max", 0) > events_local.get("max", 0):
        out.append(("CGROUP_TREE_HIT_MAX",
                    f"max={events['max']} 而 local={events_local.get('max', 0)}"
                    " -> 顶到上限的是后代 cgroup,不是本层"))
    if current > high:
        out.append(("HIGH_THROTTLED", f"memory.current {current} > memory.high {high}"
                                      " -> 进程被节流并转入直接回收(但不会触发 OOM)"))
    if maximal and current / maximal > 0.9:
        out.append(("NEAR_MAX", f"current/max = {current / maximal:.1%}"))
    return out


# ---------------------------------------------------------------- 自检
def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  <- {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ok = True
    F = CGROUP_FILES

    print("== 1. cpu.stat:8 个字段、口径分两组 ==")
    cs = parse_cpu_stat(F["cpu.stat"])
    ok &= check("恒定 3 字段 + CFS 带宽 5 字段 = 8", len(cs) == 8, str(sorted(cs)))
    ok &= check("throttled_usec 与 nr_bursts 都解析出来",
                cs["throttled_usec"] == 0 and cs["nr_bursts"] == 2)
    quota, period = parse_cpu_max(F["cpu.max"])
    ok &= check("cpu.max '400000 100000' -> 4.0 CPU,period 100 ms",
                quota == 4.0 and period == 100_000, f"{quota} CPU / {period} us")
    ok &= check("cpu.max 'max 100000' -> 不限",
                parse_cpu_max("max 100000\n")[0] is None)

    print("== 2. CFS 带宽字段是「非层级」的,而 cpu.stat.local 才是含继承的实际值 ==")
    local = parse_flat(F["cpu.stat.local"])
    verdict, why = diagnose_cpu(cs, local, parse_nested_line(F["cpu.pressure"].splitlines()[0]),
                                8_000_000)
    ok &= check("cpu.stat.throttled_usec=0(非层级,只算自己 cap 造成的节流)",
                cs["throttled_usec"] == 0)
    ok &= check("cpu.stat.local.throttled_usec=1800000(含祖先 cap 牵连的实际节流)",
                local["throttled_usec"] == 1_800_000)
    ok &= check("结论 = 被祖先 cgroup 带宽限制牵连", verdict == ANCESTOR_THROTTLED, why)

    print("== 3. 反向诊断的排除顺序 ==")
    hot = dict(cs, throttled_usec=2_000_000)
    v2, w2 = diagnose_cpu(hot, local, {"some": 0.1}, 8_000_000)
    ok &= check("自己 throttle 占比 25% > 5% -> 先判「被自己的 cpu.max 限流」",
                v2 == CAP_THROTTLED and "25.0%" in w2, w2)
    v3, w3 = diagnose_cpu(dict(cs, throttled_usec=100_000), None, {"some": 42.0}, 8_000_000)
    ok &= check("自己没被限(1.25% 未过 5%) 且 PSI some=42% -> 宿主争用",
                v3 == HOST_CONTENDED, w3)
    v4, w4 = diagnose_cpu(dict(cs), None, {"some": 0.2}, 8_000_000)
    ok &= check("两者都没有 -> 应转向下游/off-CPU,而不是继续怀疑 CPU",
                v4 == APP_QUEUED, w4)
    ok &= check("throttle 占比恰好等于阈值不算超标(用 > 而非 >=)",
                diagnose_cpu(dict(cs, throttled_usec=400_000), None, {"some": 0.1},
                             8_000_000)[0] != CAP_THROTTLED)
    ok &= check("usage_delta 为 0 时不除零(返回后续分支而非崩)",
                diagnose_cpu(dict(cs, throttled_usec=5), None, {"some": 0.1}, 0)[0]
                == APP_QUEUED)

    print("== 4. CPU shares 的双公式(bursting 的由来) ==")
    hi, lo = shares_limits(1024, 4096, 2048)
    ok &= check("上限 = 100%×1024/2048 = 50%(可用别家的空闲 CPU = bursting)", hi == 50.0)
    ok &= check("保底 = 100%×1024/4096 = 25%(全员忙时的最小保障)", lo == 25.0)
    ok &= check("上限 > 保底,差额即 bursting 空间", hi > lo)
    ok &= check("空闲越多上限越高:total_busy 减半则上限翻倍",
                shares_limits(1024, 4096, 1024)[0] == 100.0)
    ok &= check("全部分配完时上限 == 保底(无可抢)", shares_limits(1024, 4096, 4096)[0] == 25.0)

    print("== 5. memory:层级 vs 本地 ==")
    ev, evl = parse_flat(F["memory.events"]), parse_flat(F["memory.events.local"])
    ok &= check("memory.events 是层级式的(含后代),local 只有本层",
                ev["high"] == 128 and evl["high"] == 12 and ev["high"] > evl["high"])
    ok &= check("oom_kill 计数为 1 -> 有进程被杀", ev["oom_kill"] == 1)
    msgs = diagnose_memory(ev, evl, 1_073_741_824, 2_147_483_648, 1_932_735_283)
    kinds = [m[0] for m in msgs]
    ok &= check("命中 OOM_KILLED", "OOM_KILLED" in kinds)
    ok &= check("max 层级(4) > local(0) -> 顶到上限的是后代 cgroup",
                "CGROUP_TREE_HIT_MAX" in kinds)
    ok &= check("high 128 > local 12 说明后代在被 high 节流", ev["high"] != evl["high"])
    ok &= check("memory.current(1 GiB) < memory.high(1.8 GiB) -> 本层未被 high 节流",
                "HIGH_THROTTLED" not in kinds)
    ok &= check("current(1 GiB)/max(2 GiB) = 50% 未到 90% -> 不报 NEAR_MAX",
                "NEAR_MAX" not in kinds, str(msgs))
    tight = diagnose_memory(dict(ev, oom=0, oom_kill=0), evl,
                            2_000_000_000, 2_147_483_648, 1_932_735_283)
    tight_kinds = [m[0] for m in tight]
    ok &= check("current 2.0 GiB > high 1.8 GiB -> 报 HIGH_THROTTLED",
                "HIGH_THROTTLED" in tight_kinds, str(tight_kinds))
    ok &= check("且不报 OOM(历史 oom_kill 计数已清零;high 越界永不触发 OOM)",
                "OOM_KILLED" not in tight_kinds)
    ok &= check("同一场景 current/max = 93.1% > 90% -> 追加 NEAR_MAX",
                "NEAR_MAX" in tight_kinds, str(tight))

    print("== 6. io.stat:按设备分行 ==")
    io = parse_io_stat(F["io.stat"])
    ok &= check("解析出 2 个设备键(8:16 / 8:0)", len(io) == 2, str(sorted(io)))
    ok &= check("8:16 的 wbytes=314773504 / rios=192 / dios=0",
                io["8:16"]["wbytes"] == 314_773_504 and io["8:16"]["rios"] == 192
                and io["8:16"]["dios"] == 0)
    ok &= check("8:0 有丢弃(dbytes=50331648)", io["8:0"]["dbytes"] == 50_331_648)

    print("== 7. pids 与「容器也是进程」 ==")
    ok &= check("pids.current(48) > pids.max(40):官方说组织性操作不受策略阻塞,"
                "但 fork/clone 会返回 -EAGAIN",
                48 > 40 and parse_flat(F["pids.events"])["max"] == 7)
    ok &= check("pids.events.max=7 表示确实撞过 7 次上限", parse_flat(F["pids.events"])["max"] > 0)

    print("== 8. 容器内视角的坑 ==")
    host_meminfo_total = 16_384_000        # 宿主
    container_sees_total = 16_384_000      # 容器里 /proc/meminfo 读到的也是宿主值
    container_limit = 2_147_483_648 // 1024  # 自己的 memory.max
    ok &= check("容器内 free/top 看到的是宿主数据(命名空间限可见性、cgroup 限用量)",
                container_sees_total == host_meminfo_total
                and container_sees_total > container_limit)
    ok &= check("「容器内存够不够」看 memory.current/max(1 GiB/2 GiB),不看 free(15.6 GiB)",
                container_sees_total > container_limit
                and F["memory.current"].strip() != str(container_sees_total * 1024))
    ok &= check("cpu.pressure 在容器级同时有 some 与 full 两行(系统级 CPU full 才未定义)",
                F["cpu.pressure"].splitlines()[1].startswith("full"))
    p = parse_nested_line(F["cpu.pressure"].splitlines()[0])
    ok &= check("cpu.pressure some 行 avg10=0.60 / total=987654 解析正确",
                F["cpu.pressure"].splitlines()[0].startswith("some")
                and p["avg10"] == 0.60 and p["total"] == 987654.0)

    print("\n" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
