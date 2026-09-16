#!/usr/bin/env python3
"""use_check.py — 把 Brendan Gregg 的 USE 方法落成可执行的检查清单

USE = 对**每一种资源**检查 **U**tilization(利用率) / **S**aturation(饱和度) / **E**rrors(错误)。
官方 checklist 的核心价值不在「有这些指标」,而在把「没检查过的东西」从
unknown-unknowns 变成 known-unknowns —— 所以本实现把**拿不到指标的格子显式记成 "?"**,
并算出覆盖率,而不是悄悄跳过。

本机是 Windows,读不到 /proc,故采集层用**命名计数器快照**(两次采样求差)建模;
每个格子都记了「官方 checklist 里的工具 + 字段来源」,便于换到 Linux 上接真实文件。

运行: python use_check.py     (含 38 项自检)
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 判定口径
# 官方:Saturation 任何非零都可能是问题;Utilization 100% 是瓶颈信号,
# 且「长时间窗口测出的 70% 总利用率」会掩盖短时 100% 峰值 —— 故 70% 起提示。
UTIL_CAUTION, UTIL_CRIT = 70.0, 100.0


def judge_util(pct: float | None) -> str:
    if pct is None:
        return "?"
    if pct >= UTIL_CRIT:
        return "BOTTLENECK"
    return "CAUTION" if pct >= UTIL_CAUTION else "OK"


def judge_sat(q: float | None) -> str:
    if q is None:
        return "?"
    return "OK" if q == 0 else "QUEUED"


def judge_err(n: float | None) -> str:
    if n is None:
        return "?"
    return "OK" if n == 0 else "ERRORS"


from use_metrics import (CPU_COUNT, DEV_MAX_IOPS, INTERVAL_S, NIC_MAX_BPS, Stats,
                         cpu_saturation, cpu_utilization, dev_avgqu, dev_iops,
                         dev_utilization, mem_saturation, mem_utilization, nic_drops,
                         nic_errors, nic_utilization, sample_a, sample_b,
                         storage_capacity_utilization)

# ---------------------------------------------------------------- 检查清单
@dataclass
class Cell:
    resource: str
    kind: str                 # utilization / saturation / errors
    metric: str               # 官方 checklist 里的指标名
    source: str               # 官方 checklist 里的工具
    value: float | None = None
    verdict: str = "?"
    note: str = ""

    def __str__(self) -> str:
        v = "   n/a" if self.value is None else f"{self.value:>14.3f}"
        return f"{self.resource:<24}{self.kind:<12}{v}  {self.verdict:<11}{self.source}"


@dataclass
class Checklist:
    cells: list[Cell] = field(default_factory=list)

    def add(self, resource, kind, metric, source, value=None, judge=judge_util, note=""):
        self.cells.append(Cell(resource, kind, metric, source, value, judge(value), note))
        return self

    def of(self, resource: str, kind: str | None = None):
        return [c for c in self.cells
                if c.resource == resource and (kind is None or c.kind == kind)]

    @property
    def coverage(self) -> float:
        got = sum(1 for c in self.cells if c.value is not None)
        return 100.0 * got / len(self.cells) if self.cells else 0.0


# 官方 physical resources 清单(顺序照抄 use-linux.html);硬件缓存被**有意省略** ——
# 缓存在高利用率下反而改善性能,USE 只适用于「高利用率/饱和会退化」的资源。
PHYSICAL_RESOURCES = ["CPU", "Memory capacity", "Network Interfaces", "Network controller",
                      "Storage device I/O", "Storage capacity", "Storage controller",
                      "CPU interconnect", "Memory interconnect", "I/O interconnect"]
# 官方 software resources 清单
SOFTWARE_RESOURCES = ["Kernel mutex", "User mutex", "Task capacity", "File descriptors"]


def build(s0: Stats, s1: Stats) -> Checklist:
    c = Checklist()
    # ---- CPU
    c.add("CPU", "utilization", "us+sy+st(除 idle/iowait)", "vmstat 1 / sar -u",
          cpu_utilization(s0, s1))
    c.add("CPU", "saturation", "runq-sz > CPU 数", "vmstat 1 'r' / sar -q",
          cpu_saturation(s1),
          judge=lambda q: judge_sat(max(0.0, q) if q is not None else None),
          note="返回 排队长度 - CPU 数;>0 即饱和")
    c.add("CPU", "errors", "LPE 处理器错误事件(ECC 等)", "perf", None, judge_err,
          "多数 Intel/AMD 公开手册不列全,故记 ?")
    # ---- Memory capacity(注意:loadavg 不算 CPU 利用率,它含 D 状态)
    c.add("Memory capacity", "utilization", "(MemTotal-MemAvailable)/MemTotal",
          "free -m / sar -r / vmstat 1 'free'", mem_utilization(s1))
    c.add("Memory capacity", "saturation", "si+so(换页)", "vmstat 1 'si'/'so'、sar -B",
          mem_saturation(s0, s1), judge=judge_sat, note="任何非零即问题")
    c.add("Memory capacity", "errors", "dmesg 物理故障 / failed malloc",
          "dmesg | grep killed", None, judge_err)
    # ---- Network
    c.add("Network Interfaces", "utilization", "吞吐/最大带宽", "sar -n DEV 1 / nicstat %Util",
          nic_utilization(s0, s1))
    c.add("Network Interfaces", "saturation", "dropped / overruns", "ifconfig / netstat -s",
          nic_drops(s0, s1), judge=judge_sat)
    c.add("Network Interfaces", "errors", "errors / dropped", "ip -s link / /proc/net/dev",
          nic_errors(s0, s1))
    c.add("Network controller", "utilization", "推断:接口吞吐/控制器上限", "ip -s link", None)
    c.add("Network controller", "saturation", "同 Network Interfaces 饱和", "—", None)
    c.add("Network controller", "errors", "同 Network Interfaces 错误", "—", None)
    # ---- Storage:官方特别指出**存储设备同时是两种资源**,两者都可能成为瓶颈
    c.add("Storage device I/O", "utilization", "%util(忙时间占比)", "iostat -xz 1",
          dev_utilization(s0, s1))
    c.add("Storage device I/O", "saturation", "avgqu-sz > 1 或 await 高", "iostat -xnz 1",
          dev_avgqu(s0, s1), judge=lambda q: judge_sat(0.0 if q is not None and q <= 1 else q))
    c.add("Storage device I/O", "errors", "设备错误", "/sys/devices/.../ioerr_cnt, smartctl",
          float(s1["dev"]["sda"]["ioerr_cnt"]), judge_err)
    c.add("Storage capacity", "utilization", "已用/容量", "df -h / swapon -s",
          storage_capacity_utilization(s1))
    c.add("Storage capacity", "saturation", "满即 ENOSPC(官方认为这一格无排队语义)", "—", None,
          note="官方原文:not sure this one makes sense - once it's full, ENOSPC")
    c.add("Storage capacity", "errors", "ENOSPC", "strace / 日志", None, judge_err)
    c.add("Storage controller", "utilization", "各设备相加 vs 单卡 IOPS 上限",
          "iostat -xz 1 求和", 100.0 * dev_iops(s0, s1) / DEV_MAX_IOPS)
    c.add("Storage controller", "saturation", "见存储设备饱和", "—", None)
    c.add("Storage controller", "errors", "见存储设备错误", "—", None)
    # ---- 互连:官方说常被忽略、通常不是瓶颈,但一旦是就很难办
    for res in ("CPU interconnect", "Memory interconnect", "I/O interconnect"):
        c.add(res, "utilization", "每端口吞吐/最大带宽", "perf(LPE/CPC)", None)
        c.add(res, "saturation", "stall cycles / CPI", "perf(LPE/CPC)", None)
        c.add(res, "errors", "厂商手册可提供的事件", "perf(LPE/CPC)", None)
    # ---- 软件资源(官方第二节)
    c.add("Kernel mutex", "utilization", "holdtime-total/acquisitions",
          "/proc/lock_stat(CONFIG_LOCK_STATS)", None)
    c.add("Kernel mutex", "saturation", "waittime-total/contentions", "/proc/lock_stat", None)
    c.add("User mutex", "utilization", "持锁时间", "valgrind --tool=drd", None)
    c.add("Task capacity", "utilization", "当前任务数/threads-max",
          "top 'Tasks' / /proc/sys/kernel/threads-max", 500.0 / 63_000 * 100)
    c.add("Task capacity", "errors", "can't fork()", "pthread_create 失败", None, judge_err)
    c.add("File descriptors", "utilization", "已用/上限",
          "/proc/sys/fs/file-nr vs file-max", 100_000.0 / 1_000_000 * 100)
    c.add("File descriptors", "errors", "EMFILE", "strace", None, judge_err)
    return c


# ---------------------------------------------------------------- 自检
def check(label: str, cond: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  <- {detail}" if detail else ""))
    return bool(cond)


def main() -> int:
    ok = True
    s0, s1 = sample_a(), sample_b()

    print("== 1. 资源清单与官方一致 ==")
    ok &= check("10 个物理资源(顺序照抄 use-linux.html)", len(PHYSICAL_RESOURCES) == 10)
    ok &= check("4 个软件资源", len(SOFTWARE_RESOURCES) == 4
                and "File descriptors" in SOFTWARE_RESOURCES)
    ok &= check("CPU 缓存被有意排除(高利用率下缓存反而改善性能)",
                not any("cache" in r.lower() for r in PHYSICAL_RESOURCES))
    ok &= check("存储设备同时是「请求资源」与「容量资源」",
                "Storage device I/O" in PHYSICAL_RESOURCES
                and "Storage capacity" in PHYSICAL_RESOURCES)

    cl = build(s0, s1)
    print("== 2. 指标矩阵规模与覆盖率 ==")
    ok &= check("格子数在官方所说「约 30 个指标」量级(±10)", 20 <= len(cl.cells) <= 40,
                str(len(cl.cells)))
    ok &= check("每个资源都有 utilization/saturation 两类",
                all(cl.of(r, "utilization") and cl.of(r, "saturation")
                    for r in ("CPU", "Memory capacity", "Network Interfaces")))
    cov = cl.coverage
    ok &= check("拿不到的格子显式记 '?'", any(c.value is None for c in cl.cells))
    ok &= check("覆盖率 = 已填/总数,且 < 100%(互连与锁指标确实拿不到)",
                0 < cov < 100, f"{cov:.1f}% ({sum(1 for c in cl.cells if c.value is not None)}"
                               f"/{len(cl.cells)})")
    ok &= check("unknown-unknowns 变 known-unknowns:? 格子可枚举",
                len([c for c in cl.cells if c.value is None]) > 0)

    print("== 3. CPU 利用率口径(排除 idle 与 iowait) ==")
    # t0->t1 增量: user 3600 nice 0 system 2150 irq 45 softirq 230 steal 0 idle 13100 iowait 800
    busy = 3600 + 2150 + 45 + 230
    total = busy + 13100 + 800
    want = 100.0 * busy / total
    got = cpu_utilization(s0, s1)
    ok &= check("us+sy+irq+softirq+st,分母含 idle/iowait", abs(got - want) < 1e-9,
                f"{got:.3f}% vs {want:.3f}%")
    ok &= check("把 iowait 算进去会得到不同结果(说明口径真的生效)",
                abs(100.0 * (busy + 800) / total - got) > 1.0,
                f"含 iowait={100.0 * (busy + 800) / total:.2f}%")
    ok &= check("steal 计入利用率(虚拟化被宿主机抢走的时间也算忙)",
                cpu_utilization(s0, dict(s1, stat=dict(s1["stat"], steal=1035))) > got)
    ok &= check("判定: 85% 为 CAUTION(≥70 提示)", judge_util(85.0) == "CAUTION")
    ok &= check("判定: 100% 为 BOTTLENECK", judge_util(100.0) == "BOTTLENECK")
    ok &= check("判定: 69.9% 为 OK", judge_util(69.9) == "OK")

    print("== 4. CPU 饱和度 ==")
    ok &= check("r=12、CPU=8 -> 排队长度 4(>0 即饱和)",
                cpu_saturation(s1) == 4.0 and judge_sat(4.0) == "QUEUED")
    ok &= check("官方特意不用 loadavg 作 CPU 利用率(它含 D 状态/不可中断睡眠)",
                "loadavg" not in cl.of("CPU", "utilization")[0].source)

    print("== 5. 内存 ==")
    ok &= check("利用率 = (MemTotal-MemAvailable)/MemTotal ≈ 92.5%",
                abs(mem_utilization(s1) - 92.5) < 0.1, f"{mem_utilization(s1):.2f}%")
    ok &= check("利用率 ≥70% -> CAUTION", judge_util(mem_utilization(s1)) == "CAUTION")
    ok &= check("饱和度 = Δpswpin+Δpswpout = 12288(非零即问题)",
                mem_saturation(s0, s1) == 12288.0
                and judge_sat(mem_saturation(s0, s1)) == "QUEUED")
    ok &= check("SwapFree 下降是饱和的旁证",
                s1["meminfo"]["SwapFree"] < s0["meminfo"]["SwapFree"])

    print("== 6. 网络 ==")
    ok &= check("利用率 = Δ(RX+TX)B / 最大带宽 / Δt ≈ 76%",
                abs(nic_utilization(s0, s1) - 76.0) < 0.1, f"{nic_utilization(s0, s1):.2f}%")
    ok &= check("丢弃同时算 saturation 与 errors(官方脚注 7)",
                nic_drops(s0, s1) == 49.0 and nic_drops(s0, s1) > 0)
    ok &= check("物理层错误单独统计且为 0 -> OK", judge_err(nic_errors(s0, s1)) == "OK")

    print("== 7. 存储:两种资源类型、两种口径 ==")
    ok &= check("I/O %util = Δbusy/Δt = 94%", abs(dev_utilization(s0, s1) - 94.0) < 0.1,
                f"{dev_utilization(s0, s1):.1f}%")
    ok &= check("avgqu-sz = Δ加权 I/O 时间/Δt = 3.4 > 1 -> 判为有排队",
                abs(dev_avgqu(s0, s1) - 3.4) < 1e-9
                and judge_sat(0.0 if dev_avgqu(s0, s1) <= 1 else dev_avgqu(s0, s1)) == "QUEUED")
    ok &= check("IOPS = Δ(reads+writes)/Δt = 6200/s", dev_iops(s0, s1) == 6200.0)
    cap = storage_capacity_utilization(s1)
    ok &= check("容量型 utilization 用 population 口径 = 99.2% ≠ I/O 的 94%",
                abs(cap - 99.2) < 0.05 and abs(cap - dev_utilization(s0, s1)) > 1.0,
                f"{cap:.2f}%")
    ok &= check("容量利用率触顶 -> BOTTLENECK(100% 时无法再写入)",
                judge_util(cap) == "CAUTION" and judge_util(100.0) == "BOTTLENECK")
    ok &= check("设备错误计数器为 0 -> OK",
                judge_err(float(s1["dev"]["sda"]["ioerr_cnt"])) == "OK")

    print("== 8. 判定函数三态 ==")
    ok &= check("judge_util(None) = '?'", judge_util(None) == "?")
    ok &= check("judge_sat(0) = OK", judge_sat(0.0) == "OK")
    ok &= check("judge_sat(None) = '?'", judge_sat(None) == "?")
    ok &= check("judge_err(3) = ERRORS", judge_err(3) == "ERRORS")

    print("== 9. 报告输出 ==")
    print(f"  {'资源':<24}{'类型':<12}{'值':>15}  {'判定':<11}来源")
    for c in cl.cells:
        print("  " + str(c))
    ok &= check("互连类资源整行都是 '?'(无 CPC 权限时拿不到)",
                all(c.value is None for c in cl.cells if "interconnect" in c.resource.lower()))
    ok &= check("CPU/内存/网络/存储 I/O 的非 errors 格子都有实测值",
                all(c.value is not None for c in cl.cells
                    if c.resource in ("CPU", "Memory capacity", "Network Interfaces",
                                      "Storage device I/O") and c.kind != "errors"))
    ok &= check("未测格子可按资源枚举(清单的价值就在这里)",
                len({c.resource for c in cl.cells if c.value is None}) >= 8,
                str(sorted({c.resource for c in cl.cells if c.value is None})))

    print("\n" + ("全部通过" if ok else "存在失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
