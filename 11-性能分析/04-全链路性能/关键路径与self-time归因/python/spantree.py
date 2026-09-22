#!/usr/bin/env python3
"""span 树的 self time、关键路径与跨进程配对归因。

数据模型来自实读的 OpenTelemetry Trace API 规范：

- 同一条 trace 的所有 span 共享 ``trace_id``，``parent_id`` 为空的是**根 span**；
  ``parent_id`` 指向同 trace 内另一个 span 的 ``span_id`` 即构成父子关系；
- ``SpanKind`` 把"出站调用"（``CLIENT``/``PRODUCER``）与"处理入站请求"（``SERVER``/``CONSUMER``）
  区分开，并约定 **``CLIENT`` span 的上下文传播出去后，通常成为远端 ``SERVER`` span 的父**。
  这一条是"跨进程一跳在两侧各有一个 span"的依据，也是差值归因能成立的前提；
- ``INTERNAL`` 是默认值，表示进程内操作。

本模块给出的三个度量里，只有第一个是"规范里有"的，后两个是本 demo 的定义，
README 里显式标注了口径：

1. ``self_time = duration - Σ child.duration`` —— 直接的差值归因；
2. ``parallel_wait = duration - max(child.duration)`` —— 子 span 并发时的等待口径；
3. ``critical_path`` —— 以 self time 为点权的根到叶最长路径。
"""

from __future__ import annotations

CLIENT, SERVER, INTERNAL, PRODUCER, CONSUMER = "CLIENT", "SERVER", "INTERNAL", "PRODUCER", "CONSUMER"

# CLIENT 传播出去后通常成为远端 SERVER 的父；这一对才是"同一跳的两侧"
PAIRS = {(CLIENT, SERVER), (PRODUCER, CONSUMER)}


class Span:
    def __init__(self, span_id, parent_id, name, kind, start, end):
        self.span_id, self.parent_id = span_id, parent_id
        self.name, self.kind = name, kind
        self.start, self.end = start, end

    @property
    def duration(self) -> float:
        return self.end - self.start


def build(spans: list[Span]) -> dict:
    """返回 ``{roots, children, by_id, orphans}``。

    ``orphans`` 是 ``parent_id`` 非空但父 span 不在集合里的 span——它们的父很可能
    落在**另一个 collector 实例**或压根没被上报，此时它们被提升为伪根。
    """
    by_id = {s.span_id: s for s in spans}
    children: dict[str | None, list[Span]] = {}
    orphans: list[Span] = []
    for s in spans:
        if s.parent_id is None or s.parent_id == "":
            children.setdefault(None, []).append(s)
        elif s.parent_id in by_id:
            children.setdefault(s.parent_id, []).append(s)
        else:
            orphans.append(s)
            children.setdefault(None, []).append(s)   # 提升为伪根
    return {"roots": children.get(None, []), "children": children, "by_id": by_id, "orphans": orphans}


def kids(tree: dict, span_id: str) -> list[Span]:
    return tree["children"].get(span_id, [])


def self_time(tree: dict, span: Span) -> float:
    """``duration - Σ child.duration``。子 span **并发**时这个值是负的——这是特性不是 bug。"""
    return span.duration - sum(c.duration for c in kids(tree, span.span_id))


def parallel_wait(tree: dict, span: Span) -> float:
    """``duration - max(child.duration)``：并发子 span 下才是"自己额外等了多久"。"""
    cs = kids(tree, span.span_id)
    if not cs:
        return span.duration
    return span.duration - max(c.duration for c in cs)


def critical_path(tree: dict, span: Span) -> tuple[float, list[str]]:
    """以 **self time 为点权**的根到叶最长路径，返回 ``(路径 self time 之和, span_id 链)``。

    两个要点：

    - 点权用 self time 而不是 duration，否则"父 span 时长"会被整条路径反复计入；
    - 结果**不等于**根 span 的 duration（除非整棵树是单链），
      中间的缺口就是"并行分支里没走完的那部分时间"。
    """
    own = self_time(tree, span)
    best: tuple[float, list[str]] = (0.0, [])
    for c in kids(tree, span.span_id):
        cand = critical_path(tree, c)
        if cand[0] > best[0]:
            best = cand
    return own + best[0], [span.span_id] + best[1]


def pair_gaps(tree: dict) -> list[dict]:
    """找出所有 (CLIENT, SERVER) / (PRODUCER, CONSUMER) 父子对，算两侧时长差。

    差值 = 网络往返 + 排队 + 序列化，**在任一侧的 profiler 里都看不到**。
    """
    out = []
    for parent in tree["by_id"].values():
        for c in kids(tree, parent.span_id):
            if (parent.kind, c.kind) in PAIRS:
                out.append({
                    "client": parent.span_id, "server": c.span_id,
                    "client_ms": parent.duration, "server_ms": c.duration,
                    "gap_ms": parent.duration - c.duration,
                })
    return out


def anomalies(tree: dict) -> list[str]:
    """结构性异常：多根、孤儿 span、负 self time。"""
    out = []
    if len(tree["roots"]) > 1:
        out.append(f"multi_root:{len(tree['roots'])}")
    if tree["orphans"]:
        out.append(f"orphan:{sorted(s.span_id for s in tree['orphans'])}")
    for s in tree["by_id"].values():
        if self_time(tree, s) < -1e-9:
            out.append(f"negative_self_time:{s.span_id}")
    return out
