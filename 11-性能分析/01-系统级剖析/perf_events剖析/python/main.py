#!/usr/bin/env python3
"""perf_events 剖析 demo:Python 版 —— perf stat 输出解析 + 指标计算。

对 `perf stat` 的四段式输出行(value unit event "# comment")做解析,
计算 IPC / SCPI / miss 率,并把事件名按 hardware / software / tracepoint /
raw 分类(对照 Gregg perf 页面的事件类型全景)。

用法:
    python3 main.py                  # 解析内置示例(perf stat gzip 输出)
    perf stat ... | python3 main.py  # 解析标准输入
    python3 main.py perf_stat.txt
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass

# 内置示例:Brendan Gregg 页面的 perf stat gzip 实测输出
BUILTIN_STAT = """\
 Performance counter stats for 'gzip file1':

       5,649,595,479 cycles                    #    2.942 GHz
       1,808,339,931 stalled-cycles-frontend   #   32.01% frontend cycles idle
       1,171,884,577 stalled-cycles-backend    #   20.74% backend cycles idle
       8,625,207,199 instructions              #    1.53  insns per cycle
                                                 #   0.21  stalled cycles per insn
       1,488,797,176 branches                  #  775.351 M/sec
          53,395,139 branch-misses             #    3.59% of all branches
        96.434812554 seconds time elapsed
"""

HARDWARE_EVENTS = {
    "cycles", "instructions", "branches", "branch-misses",
    "cache-references", "cache-misses", "bus-cycles", "ref-cycles",
    "stalled-cycles-frontend", "stalled-cycles-backend",
}
SOFTWARE_EVENTS = {
    "context-switches", "cpu-clock", "task-clock", "page-faults",
    "cpu-migrations", "minor-faults", "major-faults",
    "alignment-faults", "emulation-faults", "bpf-output", "dummy",
}
# 硬件缓存事件族(L1-*/LLC-*/dTLB-*)与原始 PMC(rXXXXXX)单独判别

LINE_RE = re.compile(
    r"^\s*(?P<value>[\d,\.]+)\s+"
    r"(?:(?P<unit>msec|usec|sec)\s+)?"
    r"(?P<event>[\w\-:]+)\s*"
    r"(?:#\s*(?P<comment>.*))?$"
)


@dataclass
class Counter:
    value: float
    event: str
    comment: str = ""

    @property
    def kind(self) -> str:
        e = self.event
        if e in HARDWARE_EVENTS:
            return "hardware"
        if e in SOFTWARE_EVENTS or e.endswith("-clock"):
            return "software"
        if ":" in e:  # sched:sys_enter_read 之类
            return "tracepoint"
        if re.fullmatch(r"r[0-9a-fA-F]+", e):
            return "raw-pmc"  # r80a2 = RESOURCE_STALLS.OTHER
        if re.match(r"(L1-|LLC-|dTLB-|iTLB-|node-)", e):
            return "hw-cache"
        return "unknown"


def parse_stat(text: str) -> list[Counter]:
    out: list[Counter] = []
    for line in text.splitlines():
        m = LINE_RE.match(line)
        if not m or m.group("event") in ("seconds", "time"):
            # 纯注释行(只有 # 开头)没有 value,跳过
            continue
        val = float(m.group("value").replace(",", ""))
        out.append(Counter(val, m.group("event"), (m.group("comment") or "").strip()))
    return out


def derive(counters: list[Counter]) -> None:
    by = {c.event: c.value for c in counters}
    cyc, ins = by.get("cycles"), by.get("instructions")
    print("---- 派生指标 ----")
    if cyc and ins:
        print(f"IPC   = instructions / cycles        = {ins / cyc:.2f}")
        be = by.get("stalled-cycles-backend", 0.0)
        fe = by.get("stalled-cycles-frontend", 0.0)
        stalled = be + fe
        if stalled:
            print(f"SCPI  = stalled / instructions      = {stalled / ins:.2f}"
                  "   (停滞=延迟,Gregg 希望它像 IPC 一样被普及)")
        print(f"frontend idle = {100 * fe / cyc:.2f}%  (取指/预测/译码喂不上)")
        print(f"backend  idle = {100 * be / cyc:.2f}%  (乱序引擎停滞;可并行 3-4 uops 是 IPC>1 的原因)")
    br, brm = by.get("branches"), by.get("branch-misses")
    if br and brm:
        print(f"branch miss rate = {100 * brm / br:.2f}%")
    print()


def classify(counters: list[Counter]) -> None:
    print("---- 事件分类(事件类型全景) ----")
    for c in counters:
        print(f"  [{c.kind:>10}]  {c.value:>16,.0f}  {c.event}")
    print()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] != "-":
        text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    else:
        text = sys.stdin.read() if not sys.stdin.isatty() else BUILTIN_STAT
    if not text.strip():
        text = BUILTIN_STAT

    counters = parse_stat(text)
    if not counters:
        print("ERROR: no perf stat counters parsed", file=sys.stderr)
        return 2
    print(f"[perf-parse] {len(counters)} counters\n")
    classify(counters)
    derive(counters)
    print("提示: 高 IPC 警惕自旋循环(高指令率低有效工作);虚拟机无 PMU 时用 cpu-clock。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
