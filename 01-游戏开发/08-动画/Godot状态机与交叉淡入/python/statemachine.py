"""Godot 4 AnimationNodeStateMachine 的推进与交叉淡入最小转写。

依据（实读原文，godotengine/godot@master）：
- scene/animation/animation_node_state_machine.cpp（75810 B）
  - `AnimationNodeStateMachineTransition::set_xfade_time`（ERR_FAIL_COND(p_xfade < 0)）
  - `AnimationNodeStateMachinePlayback::_find_next`（priority_best 从 1e20 起、<= 判定、
    path 非空时只认 path[0] 的那条过渡）
  - `AnimationNodeStateMachinePlayback::_check_advance_condition`
    （advance_mode != ADVANCE_MODE_AUTO 时恒 false）
  - `AnimationNodeStateMachinePlayback::_transition_to_next_recursive`
    （transition_path 环路检测、xfade 置位、SWITCH_MODE_SYNC 的 seek）
  - `AnimationNodeStateMachinePlayback::_process`
    （fade_blend = MIN(1, fading_pos/fading_time)、curve 采样、CMP_EPSILON 下限）
- core/math/math_defs.h：CMP_EPSILON = 0.00001
"""

from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Sequence, Tuple

CMP_EPSILON = 0.00001

IMMEDIATE, SYNC, AT_END = 0, 1, 2
ADVANCE_DISABLED, ADVANCE_ENABLED, ADVANCE_AUTO = 0, 1, 2


def is_zero_approx(v: float) -> bool:
    return abs(v) < CMP_EPSILON


class Transition:
    """一条状态机过渡。"""

    def __init__(self, frm: str, to: str, xfade_time: float = 0.0,
                 xfade_curve: Optional[Callable[[float], float]] = None,
                 switch_mode: int = IMMEDIATE, priority: int = 0,
                 advance_mode: int = ADVANCE_AUTO, advance_condition: str = "",
                 is_reset: bool = False, break_loop_at_end: bool = False):
        if xfade_time < 0:
            raise ValueError("xfade_time 必须 >= 0（set_xfade_time 有 ERR_FAIL_COND）")
        self.frm = frm
        self.to = to
        self.xfade_time = float(xfade_time)
        self.xfade_curve = xfade_curve
        self.switch_mode = switch_mode
        self.priority = int(priority)
        self.advance_mode = advance_mode
        self.advance_condition = advance_condition
        self.is_reset = is_reset
        self.break_loop_at_end = break_loop_at_end


class StateMachine:
    def __init__(self, transitions: Sequence[Transition]):
        self.transitions: List[Transition] = list(transitions)
        self.conditions: Dict[str, bool] = {}

    def set_condition(self, name: str, value: bool) -> None:
        # 源码里条件参数名是 "conditions/<name>"
        self.conditions["conditions/" + name] = bool(value)


class Playback:
    """状态机播放器的最小模型。"""

    def __init__(self, sm: StateMachine, start: str):
        self.sm = sm
        self.current = start
        self.fading_from: Optional[str] = None
        self.fading_time = 0.0
        self.fading_pos = 0.0
        self.current_curve: Optional[Callable[[float], float]] = None
        self.path: List[str] = []
        self.position = 0.0          # 当前状态的播放位置（SWITCH_MODE_SYNC 用它对表）
        self.teleported = False
        self.loop_aborted = False

    # ---------------------------------------------------------------- 推进判据
    def _check_advance_condition(self, tr: Transition) -> bool:
        """源码：advance_mode != ADVANCE_MODE_AUTO 时直接返回 false。"""
        if tr.advance_mode != ADVANCE_AUTO:
            return False
        if tr.advance_condition:
            if not self.sm.conditions.get("conditions/" + tr.advance_condition, False):
                return False
        return True

    def _find_next(self) -> Optional[Transition]:
        if self.path:
            # 走 travel 路径时只认「当前 → path[0]」这条过渡，不看条件也不比优先级
            for tr in self.sm.transitions:
                if tr.advance_mode == ADVANCE_DISABLED:
                    continue
                if tr.frm == self.current and tr.to == self.path[0]:
                    return tr
            return None
        auto_advance_to = -1
        priority_best = 1e20
        for i, tr in enumerate(self.sm.transitions):
            if tr.advance_mode == ADVANCE_DISABLED:
                continue
            if tr.frm == self.current and self._check_advance_condition(tr):
                # <= 让「下标更大者」在优先级相同时胜出
                if tr.priority <= priority_best:
                    priority_best = tr.priority
                    auto_advance_to = i
        if auto_advance_to == -1:
            return None
        return self.sm.transitions[auto_advance_to]

    # ---------------------------------------------------------------- 过渡推进
    def _transition_to_next_recursive(self) -> None:
        transition_path = [self.current]
        while True:
            nxt = self._find_next()
            if nxt is None:
                break
            if nxt.to in transition_path:
                # 源码：WARN_PRINT_ONCE_ED 后 break，防止单帧死循环
                self.loop_aborted = True
                break
            transition_path.append(nxt.to)
            if nxt.xfade_time:
                self.fading_from = self.current
                self.fading_time = nxt.xfade_time
                self.fading_pos = 0.0
            else:
                self.fading_from = None
                self.fading_time = 0.0
                self.fading_pos = 0.0
            if self.path:
                self.path.pop(0)
            prev_position = self.position
            self.current = nxt.to
            self.current_curve = nxt.xfade_curve
            if nxt.switch_mode == SYNC:
                self.position = prev_position   # pi.time = current_nti.position
            else:
                self.position = 0.0 if nxt.is_reset else self.position
            if self.fading_time:
                break   # 有淡入时必须先处理淡入

    # ---------------------------------------------------------------- 每帧
    def process(self, delta: float, seek: bool = False) -> Dict[str, float]:
        self._transition_to_next_recursive()
        fade_blend = 1.0
        if self.fading_time and self.fading_from:
            if not seek:
                self.fading_pos += abs(delta)   # 注意是 abs
            fade_blend = min(1.0, self.fading_pos / self.fading_time)
        if self.current_curve is not None:
            fade_blend = self.current_curve(fade_blend)
        if is_zero_approx(fade_blend):
            fade_blend = CMP_EPSILON
        weights = {self.current: fade_blend}
        if self.fading_from:
            inv = 1.0 - fade_blend
            if is_zero_approx(inv):
                inv = CMP_EPSILON
            weights[self.fading_from] = weights.get(self.fading_from, 0.0) + inv
        return weights

    # ---------------------------------------------------------------- 外部请求
    def travel(self, target: str) -> None:
        """travel：能找到路径就沿路径走，否则 teleport（清掉淡入）。"""
        route = self._make_travel_path(target)
        if route:
            self.path = route
        else:
            self._set_current(target)
            self.fading_from = None
            self.fading_time = 0.0
            self.fading_pos = 0.0
            self.teleported = True

    def _make_travel_path(self, target: str) -> List[str]:
        if target == self.current:
            return []
        # 源码用 _make_travel_path 递归找；这里用最短路等价实现
        seen = {self.current}
        frontier: List[Tuple[str, List[str]]] = [(self.current, [])]
        while frontier:
            node, acc = frontier.pop(0)
            for tr in self.sm.transitions:
                if tr.advance_mode == ADVANCE_DISABLED or tr.frm != node:
                    continue
                if tr.to in seen:
                    continue
                if tr.to == target:
                    return acc + [tr.to]
                seen.add(tr.to)
                frontier.append((tr.to, acc + [tr.to]))
        return []

    def _set_current(self, node: str) -> None:
        self.current = node
        self.position = 0.0

    def start(self, node: str) -> None:
        """start = 直接 teleport 到目标并清掉淡入。"""
        self._set_current(node)
        self.fading_from = None
        self.fading_time = 0.0
        self.fading_pos = 0.0
        self.path = []
        self.teleported = True
