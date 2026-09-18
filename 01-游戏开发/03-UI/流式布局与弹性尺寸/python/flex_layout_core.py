#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CSS Flexbox 布局核心算法 —— 由 flex_layout.py 的自检驱动。

依据 W3C CSS Flexible Box Layout Module Level 1：
  §9.3 Main Size Determination —— Collect flex items into flex lines（换行收集）
  §9.7 Resolving Flexible Lengths —— 弹性长度解析（含冻结循环与 min/max 违约处理）

游戏 UI 里的对应物：Unity UI 的 Horizontal/Vertical Layout Group + LayoutElement
（minWidth/preferredWidth/flexibleWidth）、Unreal UMG 的 Size Box + Horizontal Box、
Cocos Creator 的 Layout 组件。它们的"首选尺寸 → 最小/最大 → 弹性分配"三元组与
flexbox 的 flex-basis / min-max / flex-grow-shrink 一一对应。
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

EPS = 1e-9


class Item:
    """一个 flex item 的主轴尺寸参数。

    flex_base_size  ：flex-basis（内容盒，不含 margin）
    grow / shrink   ：flex-grow / flex-shrink 因子
    min_size/max_size：主轴 min/max（min 默认 0，max 默认 +inf）
    margin          ：主轴方向两侧外边距之和（outer = inner + margin）
    """

    def __init__(self, name: str, base: float, grow: float = 0.0,
                 shrink: float = 1.0, min_size: float = 0.0,
                 max_size: float = math.inf, margin: float = 0.0) -> None:
        self.name = name
        self.flex_base = base
        self.grow = grow
        self.shrink = shrink
        self.min_size = min_size
        self.max_size = max_size
        self.margin = margin
        # §9.7 运行期状态
        self.target = base
        self.frozen = False

    def hypothetical(self) -> float:
        """hypothetical main size = flex base size 经 min/max 钳制。"""
        return clamp(self.flex_base, self.min_size, self.max_size)

    def outer_hypothetical(self) -> float:
        return self.hypothetical() + self.margin

    def outer_base(self) -> float:
        return self.flex_base + self.margin

    def outer_target(self) -> float:
        return self.target + self.margin


def clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


# ------------------------------------------------ §9.3 收集 flex lines

def collect_lines(items: Sequence[Item], container_inner: float,
                  single_line: bool = False) -> List[List[Item]]:
    """把 items 收集进若干 flex line。

    规范：从第一个未收集项开始逐个收集连续项，直到**下一次**收集会导致该行放不下；
    若第一个未收集项本身就放不下，则它单独成行。此步用的是 outer hypothetical main size。
    推论（规范 note）：零尺寸项会被收进上一行末尾，即使上一行已被"正好填满"。
    """
    if single_line:
        return [list(items)]
    lines: List[List[Item]] = []
    cur: List[Item] = []
    cur_sum = 0.0
    for it in items:
        h = it.outer_hypothetical()
        if not cur:
            cur, cur_sum = [it], h
            continue
        if cur_sum + h <= container_inner + EPS:
            cur.append(it)
            cur_sum += h
        else:
            lines.append(cur)
            cur, cur_sum = [it], h
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------- §9.7 Resolving Flexible Lengths

def resolve_flexible_lengths(items: Sequence[Item],
                             container_inner: float) -> List[float]:
    """解析一行内各 item 的 used main size（严格照 §9.7 的 9 步）。"""
    for it in items:
        it.target = it.flex_base
        it.frozen = False

    # 1. 决定用 grow 还是 shrink
    sum_hyp = sum(it.outer_hypothetical() for it in items)
    using_grow = sum_hyp < container_inner - EPS

    # 2. 初始 target = flex base size，全部未冻结

    # 3. Size inflexible items —— 先冻掉"不该参与弹性分配"的项
    for it in items:
        factor = it.grow if using_grow else it.shrink
        hypo = it.hypothetical()
        if factor == 0:
            it.target, it.frozen = hypo, True
        elif using_grow and it.flex_base > hypo:
            it.target, it.frozen = hypo, True
        elif (not using_grow) and it.flex_base < hypo:
            it.target, it.frozen = hypo, True

    def outer_of(it: Item) -> float:
        # 冻结项用 outer target，未冻结项用 outer flex base size
        return it.outer_target() if it.frozen else it.outer_base()

    # 4. initial free space
    initial_free = container_inner - sum(outer_of(it) for it in items)

    while True:
        unfrozen = [it for it in items if not it.frozen]
        if not unfrozen:
            break
        # 5a. remaining free space
        remaining = container_inner - sum(outer_of(it) for it in items)
        # 5b. 未冻结因子之和 < 1 时，只用 initial free space 的相应比例
        sum_factors = sum((it.grow if using_grow else it.shrink) for it in unfrozen)
        if sum_factors < 1:
            scaled = initial_free * sum_factors
            if abs(scaled) < abs(remaining):
                remaining = scaled
        # 5c. 按因子比例分配
        if abs(remaining) > EPS:
            if using_grow:
                total = sum(it.grow for it in unfrozen)
                for it in unfrozen:
                    ratio = it.grow / total if total > EPS else 0.0
                    it.target = it.flex_base + remaining * ratio
            else:
                # 收缩要乘「inner flex base size」—— 大块多让，而非按因子平分
                scaled_shrink = {id(it): it.shrink * it.flex_base for it in unfrozen}
                total = sum(scaled_shrink.values())
                for it in unfrozen:
                    ratio = scaled_shrink[id(it)] / total if total > EPS else 0.0
                    it.target = it.flex_base - abs(remaining) * ratio
        # 5d. Fix min/max violations（content-box 还要 floor 到 0）
        total_violation = 0.0
        min_violations: List[Item] = []
        max_violations: List[Item] = []
        for it in unfrozen:
            clamped = clamp(it.target, max(0.0, it.min_size), it.max_size)
            delta = clamped - it.target
            if abs(delta) > EPS:
                total_violation += delta
                it.target = clamped
                (min_violations if delta > 0 else max_violations).append(it)
        # 5e. Freeze over-flexed items
        if abs(total_violation) <= EPS:
            for it in items:
                it.frozen = True
        elif total_violation > 0:
            for it in min_violations:
                it.frozen = True
        else:
            for it in max_violations:
                it.frozen = True

    return [it.target for it in items]


def layout(items: Sequence[Item], container_inner: float,
           single_line: bool = False) -> List[List[float]]:
    """完整主轴布局：先分行，再逐行解析弹性长度。"""
    result = []
    for line in collect_lines(items, container_inner, single_line):
        result.append(resolve_flexible_lengths(line, container_inner))
    return result
