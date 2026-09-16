#!/usr/bin/env python3
"""mini_bpftrace_hist.py — 探针说明符展开、2 的幂与 lhist 分桶、柱状图渲染、聚合器。

从 mini_bpftrace.py 拆出,只是为了让单文件落到 300 行以内:
词法/语法在 mini_bpftrace_parse.py,执行引擎在 mini_bpftrace_engine.py,
自检与 SRC 程序在 mini_bpftrace.py。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 探针说明符
# 官方 language.md 的 "Short Name" 列。注意 U/fr/rt 等别名大小写敏感。
ALIASES = {"t": "tracepoint", "k": "kprobe", "kr": "kretprobe", "u": "uprobe",
           "ur": "uretprobe", "p": "profile", "i": "interval", "s": "software",
           "h": "hardware", "w": "watchpoint", "it": "iter", "rt": "rawtracepoint",
           "f": "fentry", "fr": "fexit"}
# begin/end 是内置事件而非 provider;官方文档写小写,cheat sheet/man page 写大写,两者都收。
BUILTIN_EVENTS = {"begin", "end", "BEGIN", "END"}


def expand_probe(spec: str) -> str:
    """短名探针展开为全名: k:f -> kprobe:f ; BEGIN -> begin ; begin 原样。"""
    spec = spec.strip()
    if spec in BUILTIN_EVENTS:
        return spec.lower()
    head, sep, rest = spec.partition(":")
    return spec if not sep else ALIASES.get(head, head) + ":" + rest


def probe_matches(pattern: str, actual: str) -> bool:
    """探针名匹配,支持 * 与 ? 通配符(官方: -l 的搜索词支持 glob)。"""
    rx = "^" + re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".") + "$"
    return re.match(rx, actual) is not None


# ---------------------------------------------------------------- 直方图分桶
def hist_index(v: int) -> int:
    """hist() 的桶号(每 2 的幂一个桶): 0/1 -> 0 ; 2..3 -> 1 ; 4..7 -> 2 ; 8..15 -> 3 …"""
    return 0 if v < 2 else v.bit_length() - 1


def fmt_bucket(n: int) -> str:
    """桶边界的刻度写法: >=1024 起用 k/M/G 后缀(官方 tutorial 里出现 [2k, 4k) / [512k, 1M))。"""
    for shift, suf in ((30, "G"), (20, "M"), (10, "k")):
        if n >= (1 << shift):
            return f"{n >> shift}{suf}"
    return str(n)


def hist_label(i: int) -> str:
    """官方 tutorial 输出里首桶写作 [0, 1] —— 右括号是方括号,因为它同时收纳 0 和 1。"""
    return "[0, 1]" if i == 0 else f"[{fmt_bucket(1 << i)}, {fmt_bucket(1 << (i + 1))})"


def lhist_m(lo: int, hi: int, step: int) -> int:
    return (hi - lo) // step


def lhist_index(v: int, lo: int, hi: int, step: int) -> int:
    """(-inf, lo] -> -1 ; [lo, hi) -> 0..M-1 ; [hi, +inf) -> M, M = (hi-lo)/step"""
    if v <= lo:
        return -1
    if v >= hi:
        return lhist_m(lo, hi, step)
    return (v - lo) // step


def lhist_label(k: int, lo: int, hi: int, step: int) -> str:
    if k == -1:
        return f"(...,{lo}]"
    if k == lhist_m(lo, hi, step):
        return f"[{hi},...)"
    return f"[{lo + k * step}, {lo + (k + 1) * step})"


BAR_WIDTH = 52   # 柱区固定宽度,与官方 tutorial / man page 样例输出一致


def span(buckets: dict[int, int]):
    """只渲染到最后一个非空桶(上方空桶不打印),中间空桶保留为 0 行。"""
    if not buckets or not any(buckets.values()):
        return []
    last = max(i for i, c in buckets.items() if c)
    return [(i, buckets.get(i, 0)) for i in range(min(buckets), last + 1)]


def render_rows(rows) -> list[str]:
    """列宽:标签左对齐 15 列 + 计数右对齐 9 列 + ' |' + 柱区(52) + '|'"""
    rows = list(rows)
    if not rows:
        return []
    maxc = max(c for _, c in rows) or 1
    out = []
    for label, c in rows:
        bar = max(1, c * BAR_WIDTH // maxc) if c else 0
        out.append(f"{label:<15}{c:>9} |{('@' * bar):<{BAR_WIDTH}}|")
    return out


class Aggregator:
    """一个 map 键对应的聚合状态。hist/lhist 用稀疏 dict,其余用标量。"""

    def __init__(self, name: str, args: tuple):
        self.name, self.args = name, args
        self.n = 0            # count() / 参与 avg、stats 的次数
        self.total = 0        # sum() / avg() / stats()
        self.val = args[0] if name in ("min", "max") else None
        self.buckets: dict[int, int] = {}
        if name == "hist":
            if len(args) > 1 and args[1] != 0:
                raise ValueError("本 demo 只实现 hist(x) 的 k=0(每个 2 的幂区间一个桶)")
            self.buckets = {0: 0}          # 桶 0 恒存在,与 bpftrace 一样从 [0, 1] 开始打印
        elif name == "lhist":
            _v, lo, hi, step = args
            if step <= 0 or hi <= lo:
                raise ValueError("lhist: 需要 step > 0 且 min < max")
            self.lo, self.hi, self.step = lo, hi, step
            self.buckets = {k: 0 for k in range(-1, lhist_m(lo, hi, step) + 1)}

    def update(self, v=None) -> None:
        n = self.name
        if n == "count":
            self.n += 1
        elif n in ("sum", "avg", "stats"):
            self.n += 1
            self.total += v
        elif n == "min":
            self.val = min(self.val, v)
        elif n == "max":
            self.val = max(self.val, v)
        elif n == "hist":
            i = hist_index(v)
            self.buckets[i] = self.buckets.get(i, 0) + 1
        elif n == "lhist":
            self.n += 1
            self.buckets[lhist_index(v, self.lo, self.hi, self.step)] += 1
        else:
            raise AssertionError(n)

    def render(self) -> list[str]:
        n = self.name
        if n in ("count", "sum"):
            return [str(self.n if n == "count" else self.total)]
        if n in ("min", "max"):
            return [str(self.val)]
        if n == "avg":
            return [str(self.total // self.n) if self.n else "0"]
        if n == "stats":
            avg = (self.total // self.n) if self.n else 0
            return [f"count {self.n}, average {avg}, total {self.total}"]
        if n == "hist":
            return render_rows((hist_label(i), c) for i, c in span(self.buckets))
        if n == "lhist":
            return render_rows((lhist_label(k, self.lo, self.hi, self.step), c)
                               for k, c in span(self.buckets))
        raise AssertionError(n)


