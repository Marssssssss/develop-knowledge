#!/usr/bin/env python3
"""use_metrics.py — USE 检查清单的采集层(命名计数器)与 14 个指标计算。

从 use_check.py 拆出,只是为了让单文件落到 300 行以内;
三态判定口径、清单组装与自检在 use_check.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------- 采集层
# 一次采样 = 一组命名计数器。字段名不绑定 /proc 的列序(列序随内核版本变动:
# 内核 iostats 文档 v5.3 时 diskstats 仍是 11 个字段,新版又追加了 discard/flush),
# 换成真机时只需把「解析 /proc/xxx」替换成同一个 dict 的填充逻辑。
Stats = dict


def sample_a() -> Stats:
    """t0 采样。刻意构造:CPU 忙 15%、内存吃紧、网卡有丢包、磁盘在排队。"""
    return {
        "stat": {"user": 100, "nice": 0, "system": 50, "idle": 800, "iowait": 100,
                 "irq": 5, "softirq": 10, "steal": 35, "guest": 0, "guest_nice": 0},
        "loadavg": {"load1": 4.0, "load5": 3.5, "load15": 3.0, "running": 6, "total": 500},
        "meminfo": {"MemTotal": 16_384_000, "MemAvailable": 2_048_000,
                    "SwapTotal": 4_194_304, "SwapFree": 4_100_000},
        "vmstat": {"pswpin": 0, "pswpout": 0, "pgscan_direct": 100, "pgscan_kswapd": 200},
        "net": {"eth0": {"rx_bytes": 5_000_000_000, "tx_bytes": 1_000_000_000,
                         "rx_drop": 0, "tx_drop": 0, "rx_errs": 0, "tx_errs": 0,
                         "rx_fifo": 0, "tx_fifo": 0}},
        "dev": {"sda": {"busy_ms": 10_000, "weighted_ms": 12_000, "reads": 100_000,
                        "writes": 50_000, "ioerr_cnt": 0}},
        "df": {"sda1": {"size_kb": 100_000_000, "used_kb": 91_000_000}},
    }


def sample_b() -> Stats:
    """t1 采样(距 t0 恰好 10 s)。"""
    return {
        "stat": {"user": 3700, "nice": 0, "system": 2200, "idle": 13900, "iowait": 900,
                 "irq": 50, "softirq": 240, "steal": 35, "guest": 0, "guest_nice": 0},
        "loadavg": {"load1": 9.5, "load5": 6.5, "load15": 4.0, "running": 12, "total": 500},
        "meminfo": {"MemTotal": 16_384_000, "MemAvailable": 1_228_800,
                    "SwapTotal": 4_194_304, "SwapFree": 3_400_000},
        "vmstat": {"pswpin": 4096, "pswpout": 8192, "pgscan_direct": 900,
                   "pgscan_kswapd": 300},
        "net": {"eth0": {"rx_bytes": 5_000_000_000 + 94_000_000_000,
                         "tx_bytes": 1_000_000_000 + 1_000_000_000,
                         "rx_drop": 37, "tx_drop": 0, "rx_errs": 0, "tx_errs": 0,
                         "rx_fifo": 12, "tx_fifo": 0}},
        "dev": {"sda": {"busy_ms": 10_000 + 9_400, "weighted_ms": 12_000 + 34_000,
                        "reads": 100_000 + 40_000, "writes": 50_000 + 22_000,
                        "ioerr_cnt": 0}},
        "df": {"sda1": {"size_kb": 100_000_000, "used_kb": 99_200_000}},
    }


CPU_COUNT = 8
DEV_MAX_IOPS = 300.0          # 磁盘标称能力(官方:标注每条总线的最大带宽,未测就可能识别瓶颈)
NIC_MAX_BPS = 12_500_000_000  # 100 GbE ≈ 12.5 GB/s
INTERVAL_S = 10.0


# ---------------------------------------------------------------- 指标计算
def cpu_utilization(s0: Stats, s1: Stats) -> float:
    """官方:system-wide 'us'+'sy'+'st',即**除 %idle 与 %iowait 外全部字段求和**。"""
    a, b = s0["stat"], s1["stat"]
    busy = sum(b[k] - a[k] for k in ("user", "nice", "system", "irq", "softirq", "steal"))
    total = sum(b[k] - a[k] for k in b)
    return 100.0 * busy / total if total else 0.0


def cpu_saturation(s: Stats) -> float:
    """官方:runq-size > CPU 数才叫饱和;这里直接返回排队长度供判定。"""
    return float(s["loadavg"]["running"] - CPU_COUNT)


def mem_utilization(s: Stats) -> float:
    """官方:Memory capacity utilization = 已用/总量;本 demo 用 MemAvailable 作未用口径。"""
    m = s["meminfo"]
    return 100.0 * (m["MemTotal"] - m["MemAvailable"]) / m["MemTotal"]


def mem_saturation(s0: Stats, s1: Stats) -> float:
    """官方:anon paging / swapping —— 即 si+so 的增量。"""
    a, b = s0["vmstat"], s1["vmstat"]
    return float((b["pswpin"] - a["pswpin"]) + (b["pswpout"] - a["pswpout"]))


def nic_utilization(s0: Stats, s1: Stats, iface: str = "eth0") -> float:
    a, b = s0["net"][iface], s1["net"][iface]
    bps = ((b["rx_bytes"] - a["rx_bytes"]) + (b["tx_bytes"] - a["tx_bytes"])) / INTERVAL_S
    return 100.0 * bps / NIC_MAX_BPS


def nic_drops(s0: Stats, s1: Stats, iface: str = "eth0") -> float:
    a, b = s0["net"][iface], s1["net"][iface]
    return float((b["rx_drop"] - a["rx_drop"]) + (b["tx_drop"] - a["tx_drop"])
                 + (b["rx_fifo"] - a["rx_fifo"]) + (b["tx_fifo"] - a["tx_fifo"]))


def nic_errors(s0: Stats, s1: Stats, iface: str = "eth0") -> float:
    a, b = s0["net"][iface], s1["net"][iface]
    return float((b["rx_errs"] - a["rx_errs"]) + (b["tx_errs"] - a["tx_errs"]))


def dev_utilization(s0: Stats, s1: Stats, dev: str = "sda") -> float:
    """%util = 设备忙时间 / 墙钟时间(iostat 的 %util 口径)。"""
    a, b = s0["dev"][dev], s1["dev"][dev]
    return 100.0 * (b["busy_ms"] - a["busy_ms"]) / (INTERVAL_S * 1000.0)


def dev_avgqu(s0: Stats, s1: Stats, dev: str = "sda") -> float:
    """avgqu-sz = 加权 I/O 时间 / 墙钟时间;官方判据是 > 1 即说明有排队。"""
    a, b = s0["dev"][dev], s1["dev"][dev]
    return (b["weighted_ms"] - a["weighted_ms"]) / (INTERVAL_S * 1000.0)


def dev_iops(s0: Stats, s1: Stats, dev: str = "sda") -> float:
    a, b = s0["dev"][dev], s1["dev"][dev]
    return ((b["reads"] - a["reads"]) + (b["writes"] - a["writes"])) / INTERVAL_S


def storage_capacity_utilization(s: Stats, fs: str = "sda1") -> float:
    d = s["df"][fs]
    return 100.0 * d["used_kb"] / d["size_kb"]


