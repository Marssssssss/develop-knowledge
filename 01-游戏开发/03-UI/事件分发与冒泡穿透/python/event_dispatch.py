#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 事件分发：冒泡 / 捕获 / 穿透 —— 自检入口。

核心算法在同目录 `event_dispatch_core.py`（WHATWG DOM §2.9 的等价实现）。
运行：python3 event_dispatch.py
"""
from __future__ import annotations

from typing import Callable, List

from event_dispatch_core import (
    AT_TARGET, BUBBLING_PHASE, CAPTURING_PHASE, Event, Node,
    dispatch, dispatch_with_passthrough, event_path, hit_test,
)

# ---------------------------------------------------------------- 自检

_CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if not cond:
        raise AssertionError("FAIL: %s %s" % (label, detail))
    print("  ok  %-54s %s" % (label, detail))


def build_tree() -> Node:
    root = Node("root")
    panel = root.add(Node("panel"))
    panel.add(Node("button"))
    return root


def rec(log: List[str], tag: str) -> Callable:
    def cb(ev: Event, lg: List[str]) -> None:
        phase = {CAPTURING_PHASE: "capture", AT_TARGET: "at-target",
                 BUBBLING_PHASE: "bubble"}.get(ev.eventPhase, "?")
        lg.append("%s@%s(%s)" % (tag, ev.currentTarget.name, phase))
    return cb


def main() -> None:
    print("[1] event path = target → 祖先链")
    root = build_tree()
    btn = root.children[0].children[0]
    check("path = [button, panel, root]",
          [n.name for n in event_path(btn)] == ["button", "panel", "root"],
          str([n.name for n in event_path(btn)]))

    print("[2] 捕获逆序、冒泡正序，target 上两阶段都跑")
    log: List[str] = []
    for name, node in (("root", root), ("panel", root.children[0]), ("button", btn)):
        node.add_listener("click", rec(log, "C-" + name), capture=True)
        node.add_listener("click", rec(log, "B-" + name), capture=False)
    dispatch(Event("click"), btn, log)
    check("捕获阶段：root → panel → button",
          log[0].startswith("C-root") and log[1].startswith("C-panel")
          and log[2].startswith("C-button"), log[0] + " " + log[1])
    check("冒泡阶段：button → panel → root",
          log[3].startswith("B-button") and log[4].startswith("B-panel")
          and log[5].startswith("B-root"), " ".join(x.split("@")[0] for x in log[3:]))
    check("target 上 capture 与 bubble 监听都会执行",
          sum(1 for x in log if x.startswith(("C-button", "B-button"))) == 2, "")
    check("eventPhase：target 上为 AT_TARGET、祖先上为 CAPTURING/BUBBLING",
          "C-button@button(at-target)" in log and "C-root@root(capture)" in log
          and "B-root@root(bubble)" in log, "")

    print("[3] bubbles=false：只有捕获阶段 + target")
    log2: List[str] = []
    root2 = build_tree()
    btn2 = root2.children[0].children[0]
    for name, node in (("root", root2), ("panel", root2.children[0]), ("button", btn2)):
        node.add_listener("click", rec(log2, "C-" + name), capture=True)
        node.add_listener("click", rec(log2, "B-" + name), capture=False)
    dispatch(Event("click", bubbles=False), btn2, log2)
    check("冒泡阶段只跑 target 一项（target 上 eventPhase 仍为 AT_TARGET）",
          [x for x in log2 if x.startswith("B-")] == ["B-button@button(at-target)"],
          str([x for x in log2 if x.startswith("B-")]))
    check("捕获阶段三项全跑（叶节点那项 eventPhase 记为 AT_TARGET）",
          sum(1 for x in log2 if x.startswith("C-")) == 3, "")

    print("[4] stopPropagation 在捕获阶段：连 target 都收不到")
    log3: List[str] = []
    root3 = build_tree()
    panel3 = root3.children[0]
    btn3 = panel3.children[0]

    def stopper(ev: Event, lg: List[str]) -> None:
        lg.append("STOP@%s" % ev.currentTarget.name)
        ev.stopPropagation()

    panel3.add_listener("click", stopper, capture=True)
    btn3.add_listener("click", rec(log3, "btn"), capture=False)
    dispatch(Event("click"), btn3, log3)
    check("panel 捕获阶段 STOP 后 button 的监听完全不执行",
          log3 == ["STOP@panel"], str(log3))

    print("[5] stopImmediatePropagation：同节点剩余监听 + 上层全部中断")
    log4: List[str] = []
    root4 = build_tree()
    panel4 = root4.children[0]
    btn4 = panel4.children[0]
    btn4.add_listener("click", rec(log4, "btn1"))
    btn4.add_listener("click", lambda ev, lg: (lg.append("btn2-SIP"),
                                               ev.stopImmediatePropagation()))
    btn4.add_listener("click", rec(log4, "btn3"))
    panel4.add_listener("click", rec(log4, "panel"))
    dispatch(Event("click"), btn4, log4)
    check("btn3 与 panel 均未执行", log4.count("btn3@button(at-target)") == 0
          and not any(x.startswith("panel@") for x in log4), str(log4))
    check("btn1 正常执行", any(x.startswith("btn1") for x in log4), "")

    print("[6] 派发过程中新增/移除监听")
    log5: List[str] = []
    root5 = build_tree()
    panel5 = root5.children[0]
    btn5 = panel5.children[0]
    btn5.add_listener("click", rec(log5, "btn"))
    victim = panel5.add_listener("click", rec(log5, "panel-victim"))
    btn5.add_listener("click", lambda ev, lg: victim.__setattr__("removed", True))
    panel5.add_listener("click", rec(log5, "panel-late"))
    dispatch(Event("click"), btn5, log5)
    check("已 removed 的监听不会被调用",
          not any(x.startswith("panel-victim") for x in log5), str(log5))
    check("同一节点上其它监听仍执行", any(x.startswith("panel-late") for x in log5), "")
    log6: List[str] = []
    root6 = build_tree()
    panel6 = root6.children[0]
    btn6 = panel6.children[0]
    btn6.add_listener("click", lambda ev, lg: root6.add_listener(
        "click", rec(lg, "root-added-in-flight")))
    dispatch(Event("click"), btn6, log6)
    check("尚未到达的祖先上新加的监听会生效（克隆只发生在 invoke 当刻）",
          any(x.startswith("root-added-in-flight") for x in log6), str(log6))

    print("[7] once 监听只触发一次")
    log7: List[str] = []
    b = Node("b")
    b.add_listener("click", rec(log7, "once"), once=True)
    dispatch(Event("click"), b, log7)
    dispatch(Event("click"), b, log7)
    check("第二次派发不再执行", log7.count("once@b(at-target)") == 1, str(log7))

    print("[8] 游戏侧：命中测试 + 穿透")
    bg = Node("背景", rect=(0, 0, 400, 300), depth=0)
    modal = Node("半透明面板", rect=(0, 0, 400, 300), depth=1)
    btn = Node("按钮", rect=(10, 10, 60, 30), depth=2)
    panel_root = Node("Canvas")
    for n in (bg, modal, btn):
        panel_root.add(n)
    nodes = [bg, modal, btn]
    check("点 (20,20) 命中 depth 最大的「按钮」",
          hit_test(nodes, 20, 20) is btn, str(hit_test(nodes, 20, 20)))
    check("点 (200,200) 命中「半透明面板」（按钮之外）",
          hit_test(nodes, 200, 200) is modal, str(hit_test(nodes, 200, 200)))
    check("点 (399,299) 仍命中面板；点 (500,10) 未命中任何元素",
          hit_test(nodes, 399, 299) is modal and hit_test(nodes, 500, 10) is None, "")

    log8 = dispatch_with_passthrough(nodes, 200, 200)   # 面板不消费
    check("无人消费 → 事件穿透到「背景」",
          log8.count("hit:背景") == 1 and log8[0] == "hit:半透明面板", str(log8))
    modal.add_listener("click", lambda ev, lg: (lg.append("modal-consume"),
                                                setattr(ev, "consumed", True)))
    log9 = dispatch_with_passthrough(nodes, 200, 200)
    check("面板消费后不再穿透（模态遮罩的经典做法）",
          "hit:背景" not in log9 and "modal-consume" in log9, str(log9))

    print("\n全部 %d 项断言通过" % _CHECKS)


if __name__ == "__main__":
    main()
