#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EXPLAIN / EXPLAIN ANALYZE 输出解读：把文本计划解析成树并复算代价、估算误差与循环口径。

口径来源（先联网实读再写）：PostgreSQL 18 官方手册 14.1 "Using EXPLAIN"
（https://www.postgresql.org/docs/current/using-explain.html）。本文件直接复现文档中的
示例数值与文字结论，关键原文见各断言 label。

运行：python3 explain_model.py   （仅标准库）
"""
from __future__ import annotations

import re

# 官方手册给出的默认代价常量（14.1 节明确写了 seq_page_cost=1.0 / cpu_tuple_cost=0.01，
# 其余在 19.7.2 也有；本 demo 只用文档明示的两个，避免引用未读到的数值）
SEQ_PAGE_COST = 1.0
CPU_TUPLE_COST = 0.01

NODE_RE = re.compile(r"^(?P<indent>\s*)(?:->\s*)?(?P<label>[A-Z][^():]*?)\s*\((?P<attrs>.*)\)\s*$")
COST_RE = re.compile(r"cost=([\d.]+)\.\.([\d.]+)\s+rows=(\d+)\s+width=(\d+)")
ACTUAL_RE = re.compile(r"actual time=([\d.]+)\.\.([\d.]+)\s+rows=(\d+)\s+loops=(\d+)")


class Node:
    __slots__ = ("label", "indent", "startup", "total", "est_rows", "width",
                 "a_start", "a_end", "a_rows", "loops", "extras", "children")

    def __init__(self, label: str, indent: int):
        self.label, self.indent = label, indent
        self.startup = self.total = 0.0
        self.est_rows = self.width = 0
        self.a_start = self.a_end = 0.0
        self.a_rows = self.loops = 0
        self.extras: list[str] = []
        self.children: list[Node] = []

    @property
    def has_actual(self) -> bool:
        return self.loops > 0

    def total_ms(self) -> float:
        """文档：actual time 与 rows 都是**每次执行的均值**，'Multiply by the loops value
        to get the total time actually spent in the node.'"""
        return round(self.a_end * self.loops, 4)

    def rows_total(self) -> int:
        """同理，重复发射的行要乘 loops（merge join 重复扫描的情形也走这条）。"""
        return self.a_rows * self.loops


def parse(plan: str) -> Node:
    """把文本计划按缩进解析成树（-&gt; 开头的行都是子节点）。"""
    root: Node | None = None
    stack: list[Node] = []
    for raw in plan.strip("\n").splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith(("Planning time", "Execution time",
                                                       "Buffers:", "Filter:", "Rows Removed")):
            if stack and line.strip():
                stack[-1].extras.append(line.strip())
            continue
        m = NODE_RE.match(line)
        # 真正的计划节点行必然带 cost= 或 actual time=；否则是 Filter/Hash Cond 之类的附加行
        if not m or ("cost=" not in m.group("attrs") and "actual time=" not in m.group("attrs")):
            if stack and line.strip():
                stack[-1].extras.append(line.strip())
            continue
        node = Node(m.group("label").strip(), len(m.group("indent")))
        attrs = m.group("attrs")
        if (c := COST_RE.search(attrs)):
            node.startup, node.total = float(c.group(1)), float(c.group(2))
            node.est_rows, node.width = int(c.group(3)), int(c.group(4))
        if (a := ACTUAL_RE.search(attrs)):
            node.a_start, node.a_end = float(a.group(1)), float(a.group(2))
            node.a_rows, node.loops = int(a.group(3)), int(a.group(4))
        while stack and stack[-1].indent >= node.indent:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            root = node
        stack.append(node)
    assert root is not None, "plan is empty"
    return root


def walk(node: Node, under_limit: bool = False) -> list[tuple[Node, bool]]:
    out = [(node, under_limit)]
    trunc = under_limit or node.label.startswith("Limit")
    for c in node.children:
        out.extend(walk(c, trunc))
    return out


def seq_scan_cost(pages: int, rows: int) -> float:
    """文档原文：'The estimated cost is computed as (disk pages read * seq_page_cost) +
    (rows scanned * cpu_tuple_cost)'，并给出 345 页 / 10000 行 → 445 的算例。"""
    return pages * SEQ_PAGE_COST + rows * CPU_TUPLE_COST


def analysis(plan: str) -> dict:
    root = parse(plan)
    nodes = walk(root)
    misestimates, truncated, loops_note = [], [], []
    for node, under_limit in nodes:
        if not node.has_actual:
            continue
        if node.est_rows > 0:
            ratio = node.rows_total() / node.est_rows
            if ratio > 10 or ratio < 0.1:
                (truncated if under_limit else misestimates).append((node.label, ratio))
        if node.loops > 1:
            loops_note.append((node.label, node.loops, node.total_ms()))
    return {
        "root": root,
        "nodes": [n for n, _ in nodes],
        "misestimates": misestimates,
        "truncated": truncated,
        "loops": loops_note,
        "actual_total_ms": round(root.a_end * max(root.loops, 1), 4),
    }


def main() -> int:
    ok, bad = 0, 0

    def check(label: str, cond: bool, detail: str = "") -> None:
        nonlocal ok, bad
        if cond:
            ok += 1
            print(f"  [PASS] {label}")
        else:
            bad += 1
            print(f"  [FAIL] {label} :: {detail}")

    print("=" * 74)
    print("1) 代价算式复现：345 页 × 1.0 + 10000 行 × 0.01 = 445")
    check("seq_scan_cost(345, 10000) == 445", abs(seq_scan_cost(345, 10000) - 445.0) < 1e-9,
          str(seq_scan_cost(345, 10000)))

    plan_no_where = """
 Seq Scan on tenk1  (cost=0.00..445.00 rows=10000 width=244)
"""
    r = analysis(plan_no_where)
    check("无 WHERE 的计划是 Seq Scan", r["root"].label.startswith("Seq Scan"), r["root"].label)
    check("Seq Scan 的 startup cost 为 0（文档示例 cost=0.00..445.00）", r["root"].startup == 0.0,
          str(r["root"].startup))
    check("顶层 cost 估算 = 复算值",
          abs(r["root"].total - seq_scan_cost(345, 10000)) < 1e-9, str(r["root"].total))
    check("rows=10000 是「节点输出行数」而非扫描行数（文档：not the number of rows processed）",
          r["root"].est_rows == 10000 and r["root"].width == 244)

    print("\n2) EXPLAIN ANALYZE：actual time 与 loops 的口径")
    plan_loops = """
 Nested Loop  (cost=0.29..1424.26 rows=10000 width=8) (actual time=0.030..7.500 rows=10000 loops=1)
   ->  Seq Scan on tenk1  (cost=0.00..445.00 rows=10000 width=4) (actual time=0.015..2.100 rows=10000 loops=1)
   ->  Index Scan using tenk1_unique1 on tenk2  (cost=0.29..0.31 rows=1 width=4) (actual time=0.003..0.003 rows=1 loops=10000)
"""
    r = analysis(plan_loops)
    inner = [n for n in r["nodes"] if n.label.startswith("Index Scan")][0]
    check("内层节点 loops=10000（外层每行执行一次）", inner.loops == 10000, str(inner.loops))
    check("单次 0.003ms × 10000 loops = 30.0ms 才是该节点总耗时",
          abs(inner.total_ms() - 30.0) < 1e-6, str(inner.total_ms()))
    check("loops>1 的节点被登记", r["loops"] and r["loops"][0][1] == 10000, str(r["loops"]))
    check("顶层 actual time 是整条语句的墙钟（不乘 loops，因为 loops=1）",
          r["actual_total_ms"] == 7.5, str(r["actual_total_ms"]))

    print("\n3) Rows Removed by Filter 与误差判定")
    plan_filter = """
 Seq Scan on tenk1  (cost=0.00..445.00 rows=7000 width=4) (actual time=0.015..2.100 rows=7000 loops=1)
   Filter: (ten < 7)
   Rows Removed by Filter: 3000
"""
    r = analysis(plan_filter)
    node = r["nodes"][0]
    check("扫描 10000 行、移除 3000 行、输出 7000 行（文档示例口径）",
          node.est_rows == 7000 and node.a_rows == 7000 and
          any("Rows Removed by Filter: 3000" in e for e in node.extras), str(node.extras))
    check("估算准确时不报 misestimate", not r["misestimates"], str(r["misestimates"]))

    plan_bad = """
 Hash Join  (cost=1.00..900.00 rows=10 width=8) (actual time=0.1..90.0 rows=50000 loops=1)
   Hash Cond: (a.id = b.id)
"""
    r = analysis(plan_bad)
    check("估算 10 行 / 实际 50000 行 → 标记为估算偏差（阈值 10×）",
          r["misestimates"] and r["misestimates"][0][1] == 5000.0, str(r["misestimates"]))

    print("\n4) LIMIT：估算与实际的差距不是估算错误")
    plan_limit = """
 Limit  (cost=0.29..0.33 rows=2 width=244) (actual time=0.030..0.031 rows=2 loops=1)
   ->  Index Scan using tenk1_unique1 on tenk1  (cost=0.29..1669.29 rows=10000 width=244) (actual time=0.030..0.031 rows=2 loops=1)
"""
    r = analysis(plan_limit)
    check("Limit 之下的节点被标为 truncated 而非 misestimate",
          r["truncated"] and not r["misestimates"], f"trunc={r['truncated']} mis={r['misestimates']}")
    check("Index Scan 的 cost/rows 仍按「跑完」估算（rows=10000 ≠ actual 2）",
          r["nodes"][1].est_rows == 10000 and r["nodes"][1].a_rows == 2)
    check("Limit 节点自身估算准确（rows=2 == actual 2），被截断的是其子节点",
          r["root"].est_rows == 2 and r["root"].a_rows == 2, str((r["root"].est_rows, r["root"].a_rows)))

    print("\n5) Merge Join 的重复发射会放大内层实际行数")
    plan_merge = """
 Merge Join  (cost=0.58..2.20 rows=100 width=8) (actual time=0.030..0.040 rows=200 loops=1)
   Merge Cond: (a.k = b.k)
   ->  Index Scan using a_k on a  (cost=0.29..1.00 rows=50 width=4) (actual time=0.01..0.01 rows=50 loops=1)
   ->  Index Scan using b_k on b  (cost=0.29..1.00 rows=2 width=4) (actual time=0.01..0.02 rows=100 loops=1)
"""
    r = analysis(plan_merge)
    inner = r["nodes"][-1]
    check("内层 actual rows(100) 远超其真实行数(2)：文档说明重复发射被计为真实行",
          inner.a_rows == 100 and inner.est_rows == 2, str(inner.a_rows))

    print("\n6) 单页表几乎必然是 Seq Scan；禁用计划类型会被标注 Disabled")
    plan_disabled = """
 Seq Scan on unit  (cost=0.00..21.30 rows=1130 width=44)
   Disabled: true
"""
    r = analysis(plan_disabled)
    check("Disabled: true 被识别为节点附加信息",
          any(e.startswith("Disabled") for e in r["nodes"][0].extras), str(r["nodes"][0].extras))
    check("estimate 与 actual 都缺失时不参与误差统计（纯 EXPLAIN）",
          not r["nodes"][0].has_actual and not r["misestimates"])

    print("\n" + "=" * 74)
    print(f"断言结果：pass={ok} fail={bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
