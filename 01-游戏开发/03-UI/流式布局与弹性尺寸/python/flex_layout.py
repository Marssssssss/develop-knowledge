#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSS Flexbox 主轴布局原理 demo —— 自检入口。

把 W3C CSS Flexbox Level 1 §9.3 / §9.7 的每条规则转成断言。
运行：python3 flex_layout.py
"""
from __future__ import annotations

from flex_layout_core import Item, collect_lines, resolve_flexible_lengths, layout

_CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if not cond:
        raise AssertionError("FAIL: %s %s" % (label, detail))
    print("  ok  %-56s %s" % (label, detail))


def near(a: float, b: float) -> bool:
    return abs(a - b) < 1e-6


def all_near(got, want) -> bool:
    return len(got) == len(want) and all(near(g, w) for g, w in zip(got, want))


def main() -> None:
    print("[1] §9.7 基础 grow：容器 600，三项 base 100、grow 1")
    r = resolve_flexible_lengths(
        [Item("a", 100, grow=1), Item("b", 100, grow=1), Item("c", 100, grow=1)], 600)
    check("剩余 300 均分 → 200/200/200", all_near(r, [200, 200, 200]),
          "[%s]" % ", ".join("%.1f" % v for v in r))

    print("[2] §9.7 按 grow 比例：容器 600，grow = 1/2/1")
    r = resolve_flexible_lengths(
        [Item("a", 100, grow=1), Item("b", 100, grow=2), Item("c", 100, grow=1)], 600)
    check("比例 .25/.5/.25 → 175/250/175", all_near(r, [175, 250, 175]),
          "[%s]" % ", ".join("%.1f" % v for v in r))

    print("[3] §9.7 因子之和 < 1：容器 600，两项 grow 各 0.25")
    r = resolve_flexible_lengths(
        [Item("a", 100, grow=0.25), Item("b", 100, grow=0.25)], 600)
    check("只分配 initial free space × 0.5 = 200 → 200/200",
          all_near(r, [200, 200]), "[%s]" % ", ".join("%.1f" % v for v in r))
    check("容器仍有 200 未被占用（grow<1 不强行填满）",
          near(600 - sum(r), 200), "剩余 %.1f" % (600 - sum(r)))

    print("[4] §9.7 基础 shrink：容器 200，三项 base 100、shrink 1")
    r = resolve_flexible_lengths(
        [Item("a", 100, shrink=1), Item("b", 100, shrink=1), Item("c", 100, shrink=1)], 200)
    check("亏空 100 均摊 → 66.67 × 3", all_near(r, [200 / 3, 200 / 3, 200 / 3]),
          "[%s]" % ", ".join("%.2f" % v for v in r))

    print("[5] §9.7 scaled flex shrink：收缩按 shrink × inner base 加权")
    r = resolve_flexible_lengths(
        [Item("small", 100, shrink=1), Item("big", 300, shrink=1)], 200)
    check("scaled = 100 / 300 → 亏空 200 按 .25/.75 摊 → 50/150",
          all_near(r, [50, 150]), "[%s]" % ", ".join("%.1f" % v for v in r))
    check("不是各让一半（那是 100/0 才对）", not near(r[0], 0), "小项仍保留 50")

    print("[6] §9.7 冻结循环 + min 违约：容器 100，A(base200,min150) B(base200)")
    a = Item("A", 200, shrink=1, min_size=150)
    b = Item("B", 200, shrink=1)
    r = resolve_flexible_lengths([a, b], 100)
    check("第一轮各让 150 → A 触 min 150 被冻结", near(r[0], 150), "A=%.1f" % r[0])
    check("亏空转由 B 承担 → B 被压到 0", near(r[1], 0), "B=%.1f" % r[1])
    check("min 约束下总宽 150 > 容器 100（溢出是规范允许的结果）",
          near(sum(r), 150), "sum=%.1f" % sum(r))

    print("[7] §9.7 flex 因子为 0 的项先被冻结")
    r = resolve_flexible_lengths(
        [Item("fixed", 100, grow=0), Item("flex", 100, grow=1)], 500)
    check("grow=0 的项保持 base 100", near(r[0], 100), "fixed=%.1f" % r[0])
    check("grow=1 的项吃掉全部剩余 → 400", near(r[1], 400), "flex=%.1f" % r[1])

    print("[8] §9.7 max 违约：容器 600，A(base100,grow1,max150) B(base100,grow1)")
    r = resolve_flexible_lengths(
        [Item("A", 100, grow=1, max_size=150), Item("B", 100, grow=1)], 600)
    check("A 触 max 150 冻结（减少 → max violation）", near(r[0], 150), "A=%.1f" % r[0])
    check("剩余全给 B → 450", near(r[1], 450), "B=%.1f" % r[1])

    print("[9] §9.3 换行收集：容器 300，四项 outer hypothetical 各 120")
    items = [Item("i%d" % i, 120, grow=1) for i in range(4)]
    lines = collect_lines(items, 300)
    check("每两项一行 → 2 行", len(lines) == 2, "行数=%d" % len(lines))
    check("每行两项（240 ≤ 300，第三项会让行宽 360 > 300）",
          all(len(l) == 2 for l in lines), "[%s]" % ", ".join(str(len(l)) for l in lines))

    print("[10] §9.3 零尺寸项被收进上一行末尾")
    items = [Item("x", 300, grow=0), Item("zero", 0, grow=0)]
    lines = collect_lines(items, 300)
    check("首项正好填满 300，零尺寸项仍进同一行", len(lines) == 1 and len(lines[0]) == 2,
          "行数=%d 首行=%d" % (len(lines), len(lines[0])))

    print("[11] §9.3 首项就放不下 → 单独成行")
    lines = collect_lines([Item("huge", 500, grow=0), Item("s", 10, grow=0)], 300)
    check("500 > 300 单独一行，10 另起一行",
          len(lines) == 2 and len(lines[0]) == 1 and len(lines[1]) == 1, "行数=%d" % len(lines))

    print("[12] 端到端：容器 300，5 项 base 100 grow 1 → 先分行再分配")
    grid = layout([Item("i%d" % i, 100, grow=1) for i in range(5)], 300)
    check("3 项 + 2 项两行", len(grid) == 2 and len(grid[0]) == 3 and len(grid[1]) == 2,
          "[%s]" % ", ".join(str(len(l)) for l in grid))
    check("第一行 3 项各 100（正好填满，无剩余可分配）",
          all_near(grid[0], [100, 100, 100]), "[%s]" % ", ".join("%.1f" % v for v in grid[0]))
    check("第二行 2 项各 150（剩余 100 均分）",
          all_near(grid[1], [150, 150]), "[%s]" % ", ".join("%.1f" % v for v in grid[1]))

    print("\n全部 %d 项断言通过" % _CHECKS)


if __name__ == "__main__":
    main()
