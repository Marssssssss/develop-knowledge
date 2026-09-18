#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 事件分发核心算法（由 event_dispatch.py 的自检驱动）。

依据 WHATWG DOM Standard §2.9「Dispatching events」实读实现：
  · event path 由 target 沿 get the parent 一路收集到根
  · 捕获阶段：path 逆序遍历，非 target 的项 eventPhase = CAPTURING_PHASE
  · 冒泡阶段：path 正序遍历，bubbles 为 false 时**非 target 项全部跳过**
  · invoke：先克隆 currentTarget 的 listener list（本节点触发后新加的监听不会被调用，
    但**未到达的节点上新加的监听会生效**；removed 标记仍然有效）
  · inner invoke 的阶段过滤：capturing 只跑 capture=true，bubbling 只跑 capture=false
  · stop propagation flag 一旦置位，后续所有 invoke 直接 return（**两个阶段都停**）
  · stop immediate propagation flag 还会中断当前节点上剩余的监听

游戏侧对应物：UGUI EventSystem（GraphicRaycaster 取最上层命中 → ExecuteEvents 沿
parent 冒泡）、UMG 的 OnMouseButtonDown + "Is Focusable/Visibility HitTestVisible"、
Cocos 的触摸事件冒泡与 swallowTouches。本 demo 末尾用同一套 dispatch 演示「穿透」。

运行：python3 event_dispatch.py
"""
from __future__ import annotations

from typing import Callable, List, Optional

CAPTURING_PHASE, AT_TARGET, BUBBLING_PHASE = 1, 2, 3


class Listener:
    __slots__ = ("type", "callback", "capture", "once", "removed")

    def __init__(self, type_: str, callback: Callable, capture: bool, once: bool) -> None:
        self.type = type_
        self.callback = callback
        self.capture = capture
        self.once = once
        self.removed = False


class Node:
    """一个 UI 节点：有父指针（用于构建 event path）、有监听列表。"""

    def __init__(self, name: str, rect=None, depth: int = 0) -> None:
        self.name = name
        self.parent: Optional[Node] = None
        self.children: List[Node] = []
        self.listeners: List[Listener] = []
        self.rect = rect          # (x, y, w, h)，仅命中测试用
        self.depth = depth        # 越大越靠上（先被命中）

    def add(self, child: "Node") -> "Node":
        child.parent = self
        self.children.append(child)
        return child

    def add_listener(self, type_: str, callback: Callable,
                     capture: bool = False, once: bool = False) -> Listener:
        lsn = Listener(type_, callback, capture, once)
        self.listeners.append(lsn)
        return lsn

    def remove_listener(self, lsn: Listener) -> None:
        lsn.removed = True


class Event:
    def __init__(self, type_: str, bubbles: bool = True) -> None:
        self.type = type_
        self.bubbles = bubbles
        self.target: Optional[Node] = None
        self.currentTarget: Optional[Node] = None
        self.eventPhase = 0
        self.path: List[Node] = []
        self.defaultPrevented = False
        self.consumed = False     # 游戏侧惯用：标记"已被处理，不再向下穿透"
        self._stop_propagation = False
        self._stop_immediate = False

    def stopPropagation(self) -> None:
        self._stop_propagation = True

    def stopImmediatePropagation(self) -> None:
        self._stop_propagation = True
        self._stop_immediate = True


def event_path(target: Node) -> List[Node]:
    """§2.9：从 target 沿 parent 一路收集到根（target 在头，root 在尾）。"""
    path: List[Node] = []
    cur: Optional[Node] = target
    while cur is not None:
        path.append(cur)
        cur = cur.parent
    return path


def invoke(node: Node, event: Event, phase: str, log: List[str]) -> None:
    """§2.9 invoke + inner invoke 的等价实现。"""
    if event._stop_propagation:          # 一旦置位，本阶段与后续阶段全部停止
        return
    event.currentTarget = node
    listeners = list(node.listeners)     # 克隆：本节点触发后再加的不会被本节点调用
    for lsn in listeners:
        if lsn.removed or lsn.type != event.type:
            continue
        if phase == "capturing" and not lsn.capture:
            continue
        if phase == "bubbling" and lsn.capture:
            continue
        if lsn.once:
            lsn.removed = True           # once：调用前先移除
        lsn.callback(event, log)
        if event._stop_immediate:
            break


def dispatch(event: Event, target: Node, log: Optional[List[str]] = None) -> List[str]:
    """完整派发：捕获（逆序）→ AT_TARGET → 冒泡（正序）。

    log 传入则由调用方持有（自检依赖观察同一份列表），不传则内部新建。
    """
    if log is None:
        log = []
    path = event_path(target)
    event.path = path
    event.target = target

    for item in reversed(path):          # 捕获阶段：根 → 叶
        event.eventPhase = AT_TARGET if item is target else CAPTURING_PHASE
        invoke(item, event, "capturing", log)
    for item in path:                    # 冒泡阶段：叶 → 根
        if item is not target and not event.bubbles:
            continue                     # bubbles=false：非 target 项全部跳过
        event.eventPhase = AT_TARGET if item is target else BUBBLING_PHASE
        invoke(item, event, "bubbling", log)

    event.eventPhase = 0
    event.currentTarget = None
    return log


# ------------------------------------------------------- 命中测试与穿透

def hit_test(nodes: List[Node], px: float, py: float) -> Optional[Node]:
    """按 depth 从大到小找第一个包含该点的节点（等同 UGUI GraphicRaycaster）。"""
    for n in sorted(nodes, key=lambda v: -v.depth):
        if n.rect is None:
            continue
        x, y, w, h = n.rect
        if x <= px < x + w and y <= py < y + h:
            return n
    return None


def dispatch_with_passthrough(nodes: List[Node], px: float, py: float,
                              type_: str = "click") -> List[str]:
    """命中 → 派发 → 无人 consume 就穿透到下一层，直到被消费或没有下一层。"""
    log: List[str] = []
    remaining = sorted(nodes, key=lambda v: -v.depth)
    while remaining:
        hit = None
        for n in remaining:
            if n.rect is None:
                continue
            x, y, w, h = n.rect
            if x <= px < x + w and y <= py < y + h:
                hit = n
                break
        if hit is None:
            break
        ev = Event(type_)
        log.append("hit:%s" % hit.name)
        log += dispatch(ev, hit)
        if ev.consumed or ev.defaultPrevented:
            break
        remaining = remaining[remaining.index(hit) + 1:]   # 继续向下穿透
    return log
