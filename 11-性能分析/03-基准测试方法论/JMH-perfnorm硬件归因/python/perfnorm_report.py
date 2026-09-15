#!/usr/bin/env python3
"""JMH `-prof perfnorm` 的归一化流水线复现。

perfnorm(LinuxPerfNormProfiler)做的事只有一句话: 把 `perf stat -I <intervalMs>` 的原始
事件计数换算成"每个 @Benchmark 操作"的量, 再由 cycles/instructions 派生 CPI/IPC。

本 demo 逐步复现它那些容易被忽略的细节(全部来自 LinuxPerfNormProfiler 源码):
  1. 事件探测: 逐个 `perf stat --event <ev> echo 1` 试探, 不支持的静默丢弃
  2. 增量解析: 跳过 '#'; 按 ',' 分列; 3 列 = (time,count,event), >=4 列 = (time,count,?,event,...)
  3. 时间窗裁剪: readFrom = delay/1000, readTo = (delay + length + interval)/1000
  4. 头尾过滤: doFilter 时丢弃**最后 2 个**样本(基础设施 ramp-down + 收尾的 profiler 输出)
  5. 求和特性: **第 1 个样本不计入**(它的时间区间实际不包含它自己)
  6. 归一化: 事件吞吐 / 操作吞吐, 操作吞吐 = 1000 * ops / timeMs
  7. 派生 CPI/IPC: 优先 cycles/instructions, 缺失时回退 cycles:u/instructions:u

另外复现两个"非主指标"的边界(jmh-dev 邮件列表由 JMH 作者给出): PMU 计数是**采样估计**,
绝对误差显著大于对多次采样求时间的误差; 线程迁移 / 多线程共核会让 PMC 差分无法归属。

无第三方依赖。
"""

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# 源码里那张"非穷举但我们关心"的事件表(顺序即源码顺序)
INTERESTING_EVENTS: Tuple[str, ...] = (
    "cycles", "instructions",
    "branches", "branch-misses",
    "L1-dcache-loads", "L1-dcache-load-misses",
    "L1-dcache-stores", "L1-dcache-store-misses",
    "L1-icache-loads", "L1-icache-load-misses",
    "LLC-loads", "LLC-load-misses",
    "LLC-stores", "LLC-store-misses",
    "dTLB-loads", "dTLB-load-misses",
    "dTLB-stores", "dTLB-store-misses",
    "iTLB-loads", "iTLB-load-misses",
    "stalled-cycles-frontend", "stalled-cycles-backend",
)

APPROX_ZERO_THRESHOLD = 1e-3  # 逐操作计数小于该值即按"近似零"展示(见 README)


# ------------------------------------------------------------ 1. 事件探测


def probe_supported(candidates: Sequence[str], available: Iterable[str]) -> List[str]:
    """模拟 `perf stat --event X echo 1`: 不支持的候选事件被静默丢弃。"""
    avail = set(available)
    return [ev for ev in candidates if ev in avail]


# ------------------------------------------------------------ 2. 增量解析


def _parse_number(tok: str) -> Optional[float]:
    """解析单个字段(容忍前后空白); 无法解析返回 None。"""
    t = tok.strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_incremental(lines: Iterable[str]) -> List[Tuple[float, str, float]]:
    """返回 [(timeSec, event, count)]; 3 列与 >=4 列两种 perf 输出格式都支持。"""
    out: List[Tuple[float, str, float]] = []
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) == 3:            # perf 3.13: time,count,event
            t_tok, c_tok, ev = parts[0], parts[1], parts[2]
        elif len(parts) >= 4:          # perf >3.13: time,count,<other>,event,<others>
            t_tok, c_tok, ev = parts[0], parts[1], parts[3]
        else:
            continue                   # 畸形行忽略
        ts, count = _parse_number(t_tok), _parse_number(c_tok)
        if ts is None or count is None:
            continue
        out.append((ts, ev.strip(), count))
    return out


# ------------------------------------------------- 3/4/5/6. 裁剪 + 归一化


def normalize(records: Sequence[Tuple[float, str, float]], *, delay_ms: int, length_ms: int,
              interval_ms: int, ops: int, time_ms: int,
              do_filter: bool = True, skip_first: bool = True) -> Dict[str, float]:
    """把原始增量计数换算成"每操作计数"。返回 {事件: #/op}。

    do_filter / skip_first 默认开着(与 JMH 默认行为一致), 显式关掉可用于观察它们的影响。
    """
    if ops <= 0 or time_ms <= 0:
        return {}
    read_from = delay_ms / 1000.0
    read_to = (delay_ms + length_ms + interval_ms) / 1000.0

    by_event: Dict[str, List[Tuple[float, float]]] = {}
    for ts, ev, count in records:
        if ts < read_from or ts > read_to:      # 3. 窗外丢弃
            continue
        by_event.setdefault(ev, []).append((ts, count))

    throughputs: Dict[str, float] = {}
    for ev, series in by_event.items():
        series = sorted(series)
        kept = series[: len(series) - 2] if (do_filter and len(series) - 2 > 0) else series  # 4.
        s = 0.0
        min_t, max_t = math.inf, -math.inf
        for i, (ts, v) in enumerate(kept):
            if i != 0 or not skip_first:        # 5. 第 1 个样本的时间区间不含它自己
                s += v
            min_t, max_t = min(min_t, ts), max(max_t, ts)
        if max_t <= min_t:
            continue
        throughputs[ev] = s / (max_t - min_t)

    ops_throughput = 1000.0 * ops / time_ms      # 6. 操作吞吐
    return {ev: thr / ops_throughput for ev, thr in throughputs.items()}


def derive_cpi_ipc(per_op: Dict[str, float]) -> Dict[str, float]:
    """7. CPI/IPC 派生, 带 cycles:u / instructions:u 回退。"""
    cycles = per_op.get("cycles", per_op.get("cycles:u"))
    instr = per_op.get("instructions", per_op.get("instructions:u"))
    out: Dict[str, float] = {}
    if cycles and instr:
        out["CPI"] = cycles / instr
        out["IPC"] = instr / cycles
    return out


# --------------------------------------------------------- 归因(本 demo 扩展)


def attribute(per_op: Dict[str, float], cpi_ipc: Dict[str, float]) -> Tuple[str, List[str]]:
    """按微架构证据给出"受限于哪一类", 返回 (结论, 证据列表)。

    阈值是本 demo 的经验线(可在 README「注意事项」中调), 不来自 JMH 源码。
    """
    ev = []
    cycles = per_op.get("cycles", 0.0)
    if cpi_ipc.get("IPC", 0.0) > 2.0:
        ev.append(f"IPC={cpi_ipc['IPC']:.2f} 高于 2: 发射宽度未被打满, 倾向前端/分支问题")
    front = per_op.get("stalled-cycles-frontend", 0.0) / cycles if cycles else 0.0
    back = per_op.get("stalled-cycles-backend", 0.0) / cycles if cycles else 0.0
    l1_loads = per_op.get("L1-dcache-loads", 0.0)
    l1_miss = per_op.get("L1-dcache-load-misses", 0.0) / l1_loads if l1_loads else 0.0
    branches = per_op.get("branches", 0.0)
    br_miss = per_op.get("branch-misses", 0.0) / branches if branches else 0.0
    llc_loads = per_op.get("LLC-loads", 0.0)
    ev.append(f"stalled-frontend/cycles={front:.1%} stalled-backend/cycles={back:.1%} "
              f"L1-load命中率={1 - l1_miss:.1%} 分支误预测率={br_miss:.2%} LLC-loads/op={llc_loads:.4g}")
    if front >= 0.30:
        return "前端受限(取指/译码/分支预测)", ev
    if back >= 0.30:
        return "后端受限(执行端口/依赖链)", ev
    if l1_miss >= 0.10 or (llc_loads and l1_miss >= 0.03):
        return "数据缓存受限(L1/LLC 未命中)", ev
    if br_miss >= 0.02:
        return "分支误预测受限", ev
    return "未见明显微架构受限: 请回到墙钟口径与系统级证据", ev


# ------------------------------------------------------------------- 展示


def format_value(x: float) -> str:
    """复制 JMH 对极小逐操作计数的近似零记法(示例输出里是 '≈ 10⁻⁴')。"""
    if abs(x) < APPROX_ZERO_THRESHOLD:
        return "≈ 10⁻⁴"
    for div, suf in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(x) >= div:
            return f"{x / div:.3f}{suf}"
    return f"{x:.3f}"


# ------------------------------------------------------------------- 自检

_START = 0.1
_STEP = 0.1
_SAMPLES = 12
_RATES = {  # 事件: 每秒计数
    "cycles": 1.0e8, "instructions": 2.5e8,
    "L1-dcache-loads": 5.0e7, "L1-dcache-load-misses": 5.0e5,
    "branches": 2.0e7, "branch-misses": 1.0e5,
    "LLC-loads": 1.0e5, "stalled-cycles-frontend": 1.0e7,
    "stalled-cycles-backend": 5.0e6, "nonexistent-event": 1.0e9,
}


def _fixture(miss_rate: float) -> List[str]:
    """构造 12 个 100ms 增量样本。

    计数是**区间内增量**(与 `perf stat -I` 的语义一致, 不是累计值);
    第 0 个样本带基础设施启动开销, 最后两个带 ramp-down 与 profiler 收尾输出,
    这正是 JMH 要过滤头尾的原因。
    """
    rates = dict(_RATES)
    rates["L1-dcache-load-misses"] = rates["L1-dcache-loads"] * miss_rate
    mult = [3.0] + [1.0] * 8 + [2.5, 2.5] + [1.0]
    lines = ["# time,count,event"]
    for i in range(_SAMPLES):
        t = _START + i * _STEP
        for ev, rate in rates.items():
            lines.append(f"{t:.1f},{rate * _STEP * mult[i]:.0f},{ev}")
    return lines


def _self_test() -> None:
    # 1) 事件探测: 不支持的事件被丢弃, 支持的全部保留
    supported = probe_supported(INTERESTING_EVENTS + ("nonexistent-event",),
                                set(_RATES) - {"nonexistent-event"})
    assert "nonexistent-event" not in supported
    assert len(supported) == 9, len(supported)

    # 2) 解析: 3 列(perf 3.13)与 >=4 列(perf >3.13)两种格式; 第 4 列才是事件名
    r3 = parse_incremental(["0.1,1234,cycles"])
    r5 = parse_incremental(["0.1,1234,,cycles,extra"])
    assert r3 == [(0.1, "cycles", 1234.0)], r3
    assert r5[0][1] == "cycles" and r5[0][2] == 1234.0, r5
    # 陷阱: 计数里出现千分位分组符时, 行会多切出一列 —— 事件名仍在 index 3,
    # 但计数被截断成 "1"(这就是为什么畸形行必须能被识别, 见 README「注意事项」)
    r_shift = parse_incremental(["0.1,1,234,cycles"])
    assert r_shift == [(0.1, "cycles", 1.0)], r_shift
    assert parse_incremental(["0.1,abc,cycles"]) == []

    # 3) 归一化流水线: 常量速率下"每操作计数"必须等于 速率/操作吞吐
    records = parse_incremental(_fixture(miss_rate=0.01))
    per_op = normalize(records, delay_ms=0, length_ms=1000, interval_ms=100,
                       ops=1_000_000, time_ms=1000)
    # 操作吞吐 = 1000*1e6/1000 = 1e6 ops/s -> cycles/op = 1e8/1e6 = 100
    assert abs(per_op["cycles"] - 100.0) < 1e-9, per_op["cycles"]
    assert abs(per_op["instructions"] - 250.0) < 1e-9
    assert abs(per_op["L1-dcache-loads"] - 50.0) < 1e-9
    cpi_ipc = derive_cpi_ipc(per_op)
    assert abs(cpi_ipc["CPI"] - 0.4) < 1e-12 and abs(cpi_ipc["IPC"] - 2.5) < 1e-12, cpi_ipc

    # 4) 三个裁剪步骤若被省略, 结果会偏离 —— 证明它们不是可有可无的
    naive = normalize(records, delay_ms=0, length_ms=1000, interval_ms=100,
                      ops=1_000_000, time_ms=1000, do_filter=False)
    keep_first = normalize(records, delay_ms=0, length_ms=1000, interval_ms=100,
                           ops=1_000_000, time_ms=1000, skip_first=False)
    assert abs(naive["cycles"] - per_op["cycles"]) > 1e-6, "头尾过滤未起作用?"
    assert abs(keep_first["cycles"] - per_op["cycles"]) > 1e-6, "首样本跳过未起作用?"
    print(f"[裁剪] 完整流水线 cycles/op={per_op['cycles']:.3f}; "
          f"跳过头尾过滤={naive['cycles']:.3f}(偏差 {abs(naive['cycles'] / per_op['cycles'] - 1):.1%}); "
          f"计入首样本={keep_first['cycles']:.3f}(偏差 {abs(keep_first['cycles'] / per_op['cycles'] - 1):.1%})")

    # 5) 归因: 两个 fixture 给出不同结论
    tag1, ev1 = attribute(per_op, cpi_ipc)
    miss_per_op = normalize(parse_incremental(_fixture(miss_rate=0.25)), delay_ms=0,
                            length_ms=1000, interval_ms=100, ops=1_000_000, time_ms=1000)
    tag2, ev2 = attribute(miss_per_op, derive_cpi_ipc(miss_per_op))
    assert tag1.startswith("未见明显"), tag1
    assert tag2.startswith("数据缓存受限"), tag2
    for line in ev1 + ev2:
        print(f"    证据 {line}")

    # 6) 近似零展示
    assert format_value(1e-5) == "≈ 10⁻⁴"
    assert format_value(0.001) == "0.001"
    assert format_value(12345.0) == "12.345k"

    # 7) 时间 vs PMU 的误差量级(来自 jmh-dev 邮件的实测输出)
    time_score, time_err = 0.252, 0.002
    cyc_score, cyc_err = 1.073, 0.043
    # 分数本身只差约 4.26 倍(1.073/0.252, 同一次测量的两种口径),
    # 但绝对误差却差约 21.5 倍(0.043/0.002) —— 时间口径的精度远好于 PMU 计数
    assert abs(cyc_score / time_score - 4.26) < 0.02
    assert abs(cyc_err / time_err - 21.5) < 0.2
    print(f"[口径] 时间   {time_score}±{time_err} ns/op (相对误差 {time_err / time_score:.2%})")
    print(f"[口径] cycles {cyc_score}±{cyc_err} #/op (相对误差 {cyc_err / cyc_score:.2%}); "
          f"分数差 {cyc_score / time_score:.2f}x, 绝对误差差 {cyc_err / time_err:.1f}x")

    # 8) 打印一张 perfnorm 风格表
    print("\nbenchmark                mode  cnt  score        error  unit")
    print(f"{'Demo.attr':<24} avgt   15  {1000.0 / per_op['cycles']:.3f}      ±0.012  ns/op")
    for key, unit in (("CPI", "clks/insn"), ("IPC", "insns/clk"),
                      ("L1-dcache-loads", "#/op"), ("L1-dcache-load-misses", "#/op"),
                      ("LLC-loads", "#/op"), ("LLC-stores", "#/op"),
                      ("branches", "#/op"), ("stalled-cycles-backend", "#/op")):
        val = cpi_ipc.get(key, per_op.get(key, 0.0))
        shown = val if key in ("CPI", "IPC") else None
        if shown is None:
            text = format_value(val)
        else:
            text = f"{val:.3f}"
        print(f"{'Demo.attr:' + key:<24} avgt    3  {text:<12}          {unit}")


if __name__ == "__main__":
    _self_test()
    print("\nperfnorm_report: 全部自检通过")
