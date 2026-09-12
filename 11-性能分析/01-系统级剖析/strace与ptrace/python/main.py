#!/usr/bin/env python3
"""strace 与 ptrace demo:Python 版 —— ptrace 开销模型 + strace -c 输出解析。

两个部分:
  1. 开销模型:被 ptrace 追踪的进程,每个系统调用要经历
     2 次 ptrace-stop + 2 次上下文切换 + tracer 处理,
     T_total ≈ N × (2×T_stop + 2×T_ctxsw + T_handler)。
     用 Gregg 的 dd 实测(3.53s -> 218.9s, 1.5GB/s -> 23.9MB/s)反推
     每 syscall 的追踪税,再外推到不同 syscall 频率的进程。

  2. strace -c 汇总解析:表格解析 calls/errors/time 列,输出占比。
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass

# Gregg 实测基线(brendangregg.com/perf.html,dd 重系统调用负载)
DD_BASELINE = {"time_s": 3.53, "throughput": 1.5e9, "traced_time_s": 218.9,
               "traced_throughput": 23.9e6, "perf_stat_time_s": 9.14}

# strace -c 示例输出(字段格式来自 strace -c 的标准表头)
BUILTIN_STRACE_C = """\
% time     seconds  usecs/call     calls    errors syscall
------ ----------- ----------- --------- --------- ----------------
 42.31    0.004348           4      1000           read
 31.09    0.003194           8       400         6 write
 18.20    0.001870           3       600        12 openat
  5.44    0.000559           1       500           close
  1.96    0.000201           2       100           fstat
  1.00    0.000103           1       103        38 stat
------ ----------- ----------- --------- --------- ----------------
100.00    0.010275           4      2703        56 total
"""


@dataclass
class SyscallRow:
    name: str
    calls: int
    errors: int
    seconds: float
    pct: float


def parse_strace_c(text: str) -> list[SyscallRow]:
    rows: list[SyscallRow] = []
    for line in text.splitlines():
        m = re.match(
            r"\s*([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)", line)
        if not m:
            continue
        pct, secs, _us_call, calls, errors, name = m.groups()
        if name == "total":
            continue
        rows.append(SyscallRow(name, int(calls), int(errors),
                               float(secs), float(pct)))
    return rows


def overhead_model() -> None:
    print("== ptrace 开销模型 ==")
    t = DD_BASELINE
    slowdown = t["traced_time_s"] / t["time_s"]
    tput_ratio = t["traced_throughput"] / t["throughput"]
    print(f"dd 基线(Gregg 实测): {t['time_s']}s -> strace 下 {t['traced_time_s']}s")
    print(f"  时间减速比 = {slowdown:.1f}x,吞吐只剩 {100*tput_ratio:.1f}%")
    print(f"  对照 perf stat: {t['perf_stat_time_s']}s(仅 ~{t['perf_stat_time_s']/t['time_s']:.1f}x)\n")

    # 反推:整个窗口内被追踪进程慢了 (218.9-3.53)s;把慢出来的时间
    # 归到"每字节一读一写"模型的 syscall 数上,估算每 syscall 追踪税
    bytes_ = 5.2e9
    extra_s = t["traced_time_s"] - t["time_s"]
    # dd 每轮 read+write 一次,块大小按 128KiB 估
    block = 128 * 1024
    n_syscalls = int(bytes_ / block) * 2
    tax_us = extra_s / n_syscalls * 1e6
    print(f"模型假设: dd 以 {block//1024}KiB 块读写, {n_syscalls:,} 次 syscall 吸收了")
    print(f"         {extra_s:.0f}s 额外时间 -> 每次系统调用的 ptrace 税 ≈ {tax_us:.1f} us")
    print("         (2 次 ptrace-stop + 2 次上下文切换 + waitpid 往返)\n")

    print("外推(同样的税作用到不同 syscall 频率的进程):")
    for label, freq in [("计算密集(10^2/s)", 1e2), ("普通服务(10^4/s)", 1e4),
                        ("I/O 密集(10^6/s)", 1e6), ("代理/转发(10^7/s)", 1e7)]:
        per_s = freq * tax_us / 1e6
        print(f"  {label:<22} 追踪税 {per_s:8.3f} cpu-s/s "
              f"{'(≈全速被吃光)' if per_s > 0.5 else ''}")
    print("\n结论: 系统调用越密,strace 越接近把进程拖停;")
    print("      生产环境用 perf trace / perf stat -e 'syscalls:sys_enter_*' 计数替代。\n")


def strace_c_summary() -> None:
    print("== strace -c 汇总解析 ==")
    rows = parse_strace_c(BUILTIN_STRACE_C)
    total_calls = sum(r.calls for r in rows)
    total_errors = sum(r.errors for r in rows)
    for r in sorted(rows, key=lambda r: -r.calls):
        err_rate = 100.0 * r.errors / r.calls if r.calls else 0.0
        print(f"  {r.name:<10} calls={r.calls:<6} ({100*r.calls/total_calls:5.1f}%)  "
              f"errors={r.errors:<4} ({err_rate:4.1f}%)  time={r.seconds*1000:6.1f}ms")
    print(f"  {'TOTAL':<10} calls={total_calls:<6} errors={total_errors}")
    print("\n读法: 高 errors 占比(如上面的 stat/openat)往往指向缺文件/权限问题,")
    print("      先看错误率再看耗时 —— strace 的强项是'调了什么',不是'多快'。\n")


def main() -> int:
    overhead_model()
    if len(sys.argv) > 1:
        rows = parse_strace_c(open(sys.argv[1], encoding="utf-8").read())
        for r in rows:
            print(r)
    else:
        strace_c_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
