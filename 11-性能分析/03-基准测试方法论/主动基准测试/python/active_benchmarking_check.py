#!/usr/bin/env python3
"""主动基准测试(Active Benchmarking): 把"运行中收集到的多口径证据"变成"陷阱判定"。

Gregg 的定义只有两句:
  1. 让基准尽可能长时间跑在稳态(例如数小时);
  2. **在它还在跑的时候**用其它工具分析整个系统, 找出真正的限流因子。

与之相对的是被动基准测试: 跑完只看结果数字。"Data is not Information" ——
只看结果永远说不清"为什么是 X 而不是 2X"。他的原文还给出了一个判据:
> 能否讲清运行过程中用了哪些工具、并解释限流因子是什么?

本 demo 把 Gregg 的 7 条 problem checklist 变成可计算判定(输入是从 pidstat /
vmstat / iostat / perf 等工具收集到的多口径证据):
  1. 被其它系统事件(含邻居)扰动         -> 进程外 CPU 占用高
  2. 被软件资源控制限流                  -> 利用率被钉在 cgroup 配额上
  3. 被客户端-服务端之间的网络限流        -> 吞吐贴链路上限且服务端不忙
  4. 基准软件自身单线程                  -> 单线程跑满而机器有空闲核
  5. 测的是磁盘 I/O 而不是文件系统 I/O    -> 文件系统读字节 >> 磁盘读字节
  6. 热降频(频率/吞吐同趋势下滑)
  7. 统计口径不可信                      -> CoV 过高, 唯一正确结论是"结果不可信"

最后一条来自 Gregg 的原文: 数据本来就错时, 统计分析唯一有价值的产出就是
"判定数据不可信(如 CoV 过高)", 否则统计只会让错数据显得可信。

无第三方依赖。
"""

from __future__ import annotations

import itertools
import statistics
from typing import Callable, Dict, List, Tuple

# Gregg 的 problem checklist 原文条目(用于说明判定覆盖面)
GREGG_CHECKLIST = [
    "Perturbed by other system events, including neighbors.",
    "Throttled by software imposed resource controls.",
    "Throttled by the network between the benchmark client and the server.",
    "Limited by the benchmark software being single threaded.",
    "Testing different client or server software versions, when doing comparative benchmarking.",
    "Testing disk I/O instead of file system I/O.",
    "Applying an unrealistic workload.",
]


def _trend(series: List[float]) -> float:
    """用最小二乘斜率 / 均值 表示相对漂移率(负值 = 随时间下降)。"""
    n = len(series)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx, my = statistics.fmean(xs), statistics.fmean(series)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, series))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den else 0.0
    return slope / my if my else 0.0


def _cov(series: List[float]) -> float:
    return statistics.stdev(series) / abs(statistics.fmean(series)) if len(series) > 1 else 0.0


# ------------------------------------------------------------------ 判定器


def check_perturbed(ev: Dict) -> Tuple[bool, str]:
    """进程外 CPU 占用高 -> 被其它事件/邻居扰动。"""
    util = ev.get("proc_cpu_util", 0.0)
    other = ev.get("other_cpu_util", 0.0)
    fired = other >= 0.30
    return fired, (f"benchmark 进程占用 {util:.2f} 核, 其它进程占用整机算力的 "
                   f"{other:.0%}(阈值 30%)")


def check_resource_control(ev: Dict) -> Tuple[bool, str]:
    """利用率被钉在 cgroup 配额上 -> 被软件资源控制限流。"""
    util = ev.get("proc_cpu_util", 0.0)
    quota = ev.get("cgroup_quota_cores")
    if quota is None:
        return False, "无 cgroup 配额信息"
    fired = util >= quota * 0.95
    return fired, f"利用率 {util:.2f} 核 vs 配额 {quota:.2f} 核(≥95% 即判定被钉住)"


def check_network_limit(ev: Dict) -> Tuple[bool, str]:
    """吞吐贴链路上限且服务端不忙 -> 网络限流。"""
    client = ev.get("client_throughput", 0.0)
    server = ev.get("server_throughput", 0.0)
    link = ev.get("link_capacity", float("inf"))
    util = ev.get("server_cpu_util", 0.0)
    fired = client >= link * 0.95 and util < 0.50
    return fired, (f"客户端吞吐 {client:.1f} 单位 vs 链路上限 {link:.1f}"
                   f"(≥95%), 服务端 CPU 仅 {util:.0%}(<50%)")


def check_single_thread(ev: Dict) -> Tuple[bool, str]:
    """单线程跑满而机器有空闲核 -> 基准软件本身单线程受限。"""
    util = ev.get("proc_cpu_util", 0.0)
    cores = ev.get("machine_cores", 1)
    threads = ev.get("benchmark_threads", 1)
    fired = threads == 1 and cores > 1 and util >= 0.95
    return fired, (f"基准线程数 {threads}, 机器 {cores} 核, 进程占用 {util:.2f} 核 "
                   f"-> 未利用其余 {cores - 1} 核")


def check_wrong_target(ev: Dict) -> Tuple[bool, str]:
    """文件系统读字节远大于磁盘读字节 -> 测的是 page cache 而非磁盘 I/O。"""
    fs = ev.get("fs_bytes_read", 0.0)
    disk = ev.get("disk_bytes_read", 0.0)
    if fs <= 0:
        return False, "无文件系统读计数"
    ratio = (fs - disk) / fs
    fired = ratio >= 0.50
    return fired, (f"文件系统读 {fs:.3g} B, 磁盘读 {disk:.3g} B -> {ratio:.0%} 由 "
                   f"page cache 满足(阈值 50%)")


def check_thermal(ev: Dict) -> Tuple[bool, str]:
    """频率与吞吐同趋势下滑 -> 热降频。"""
    freq = ev.get("freq_series", [])
    tps = ev.get("throughput_series", [])
    if len(freq) < 3 or len(tps) < 3:
        return False, "缺少频率/吞吐时间序列"
    f_tr, t_tr = _trend(freq), _trend(tps)
    fired = f_tr < -0.02 and t_tr < -0.02
    return fired, f"频率漂移 {f_tr:+.2%}/样本, 吞吐漂移 {t_tr:+.2%}/样本(同为负即判定)"


def check_untrustworthy(ev: Dict, limit: float = 0.02) -> Tuple[bool, str]:
    """同码重跑的 CoV 过高 -> 统计结论不可信(此时唯一正确的产出是判定不可信)。"""
    reps = ev.get("repeat_results", [])
    if len(reps) < 3:
        return False, "缺少重复测量"
    cv = _cov(reps)
    return cv > limit, f"重复测量 CoV={cv:.2%}(阈值 {limit:.0%})"


CHECKS: List[Tuple[str, Callable[[Dict], Tuple[bool, str]]]] = [
    ("被其它系统事件/邻居扰动", check_perturbed),
    ("被软件资源控制(cgroup)限流", check_resource_control),
    ("被客户端-服务端网络限流", check_network_limit),
    ("基准软件单线程受限", check_single_thread),
    ("测的是磁盘 I/O 而非文件系统 I/O", check_wrong_target),
    ("热降频", check_thermal),
]


def analyze(ev: Dict, cov_limit: float = 0.02) -> Tuple[List[dict], bool, str]:
    """返回 (命中列表, 是否可信, "限流因子"结论)。"""
    hits = []
    for name, fn in CHECKS:
        fired, evidence = fn(ev)
        hits.append({"name": name, "fired": fired, "evidence": evidence})
    fired_names = [h["name"] for h in hits if h["fired"]]
    untrusted, cov_evidence = check_untrustworthy(ev, cov_limit)
    if untrusted:
        verdict = f"结果不可信: {cov_evidence} —— 先降噪, 不要下性能结论"
    elif fired_names:
        verdict = "限流因子: " + " + ".join(fired_names)
    else:
        verdict = "未见明显陷阱: 结果可用于解释'为什么是 X'"
    return hits, not untrusted, verdict


# ------------------------------------------------------------------- 自检


def _clean() -> Dict:
    return {
        "proc_cpu_util": 3.8, "other_cpu_util": 0.05, "machine_cores": 8,
        "benchmark_threads": 4, "server_cpu_util": 0.72, "server_throughput": 900.0,
        "client_throughput": 900.0, "link_capacity": 10000.0,
        "fs_bytes_read": 1e9, "disk_bytes_read": 0.9e9,
        "freq_series": [3.4, 3.39, 3.41, 3.40], "throughput_series": [900, 901, 899, 900],
        "repeat_results": [900, 902, 899, 901, 900],
    }


def _with(ev: Dict, **kw) -> Dict:
    out = dict(ev)
    out.update(kw)
    return out


def _self_test() -> None:
    clean = _clean()
    hits, trusted, verdict = analyze(clean)
    assert trusted and not any(h["fired"] for h in hits), hits
    print(f"[干净] {verdict}")

    cases = [
        ("扰动", _with(clean, other_cpu_util=0.55), "被其它系统事件/邻居扰动"),
        ("配额", _with(clean, proc_cpu_util=2.0, cgroup_quota_cores=2.0), "被软件资源控制(cgroup)限流"),
        ("网络", _with(clean, client_throughput=9990.0, server_throughput=9990.0,
                       server_cpu_util=0.20, link_capacity=10000.0), "被客户端-服务端网络限流"),
        ("单线程", _with(clean, benchmark_threads=1, proc_cpu_util=0.99, machine_cores=8),
         "基准软件单线程受限"),
        ("测错目标", _with(clean, fs_bytes_read=1e9, disk_bytes_read=1e6),
         "测的是磁盘 I/O 而非文件系统 I/O"),
        ("热降频", _with(clean, freq_series=[3.6, 3.4, 3.2, 2.9],
                         throughput_series=[980, 930, 880, 830]), "热降频"),
    ]
    for tag, ev, expected in cases:
        hits, trusted, verdict = analyze(ev)
        fired = [h["name"] for h in hits if h["fired"]]
        assert expected in fired, (tag, fired)
        ev_line = next(h["evidence"] for h in hits if h["name"] == expected)
        print(f"[{tag}] 命中 {len(fired)} 项: {expected}\n    证据 {ev_line}")
        assert trusted

    # CoV 门禁: 抖动过大时唯一正确的结论是"结果不可信"
    jumpy = _with(clean, repeat_results=[900, 1040, 810, 990, 860])
    hits, trusted, verdict = analyze(jumpy)
    assert not trusted and "不可信" in verdict, verdict
    print(f"[抖动] {verdict}")

    # 多陷阱叠加: 限流因子要全部列出
    multi = _with(clean, other_cpu_util=0.6, benchmark_threads=1, proc_cpu_util=0.99)
    _, _, verdict_multi = analyze(multi)
    assert verdict_multi.count("+") >= 1 and "扰动" in verdict_multi and "单线程" in verdict_multi
    print(f"[叠加] {verdict_multi}")

    # checklist 覆盖面自检: 7 条原文条目里 6 条可计算, 第 7 条(不现实负载)无法从数据判定
    assert len(GREGG_CHECKLIST) == 7 and len(CHECKS) == 6
    print(f"[清单] 原文 7 条, 可程序化判定 {len(CHECKS)} 条; "
          f"剩余 1 条需人工判断: {GREGG_CHECKLIST[6]}")

    # 趋势与 CoV 辅助函数
    assert _trend([1, 2, 3, 4]) > 0.3 and _trend([4, 3, 2, 1]) < -0.3
    assert 0.0 < _cov([1.0, 1.1, 0.9, 1.0]) < 0.1
    print("[工具] 趋势/CoV 辅助函数自检通过")

    # 演示"同一批结果, 统计显著但不可信"的组合情形
    sig = _with(clean, repeat_results=[1000, 1000, 1000, 1000, 1000],
                proc_cpu_util=0.98, benchmark_threads=1)
    _, trusted_sig, verdict_sig = analyze(sig)
    assert trusted_sig  # CoV=0 -> 统计上"很可信"
    assert "单线程" in verdict_sig  # 但机制上仍是错的: 单线程跑满只是没利用机器
    print(f"[组合] 极低 CoV 但机制有问题 -> {verdict_sig}")
    print("     -> 统计可信 != 结论正确: 这正是被动基准测试的陷阱")


if __name__ == "__main__":
    _self_test()
    print("\nactive_benchmarking_check: 全部自检通过")
