"""Godot 4 Animation 轨道求值：_find 二分 + _interpolate 循环模式的最小转写。

依据（实读原文，godotengine/godot@master）：
- scene/resources/animation.cpp（224493 B）
  - `Animation::_find(keys, p_time, p_backward, p_limit)`
  - `Animation::_interpolate(keys, p_time, p_interp, p_loop_wrap, p_ok, p_backward)`
  - `Animation::track_find_key(p_track, p_time, p_find_mode, p_limit, p_backward)`
- core/math/math_defs.h：`CMP_EPSILON = 0.00001`
- core/math/math_funcs.h：`pingpong(v, len) = abs(fract((v-len)/(len*2))*len*2 - len)`

只转写读到的部分：NEAREST / LINEAR / LINEAR_ANGLE 与三种 LoopMode 的下标选择；
CUBIC / CUBIC_ANGLE 需要 pre/post 的 pre_t/to_t/post_t 记账且 Vector3::
cubic_interpolate_in_time 的曲线公式不在本 demo 取材范围内，故未实现（README 注明）。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

CMP_EPSILON = 0.00001


def is_equal_approx(a: float, b: float) -> bool:
    """Math::is_equal_approx：绝对值差小于 CMP_EPSILON。"""
    return abs(a - b) < CMP_EPSILON


def is_zero_approx(v: float) -> bool:
    return abs(v) < CMP_EPSILON


def posmod(x: float, m: float) -> float:
    """Math::posmod：结果恒在 [0, m)。"""
    return x - math.floor(x / m) * m


def fract(x: float) -> float:
    return x - math.floor(x)


def pingpong(value: float, length: float) -> float:
    if length == 0.0:
        return 0.0
    return abs(fract((value - length) / (length * 2.0)) * length * 2.0 - length)


def pingpong_index(i: int, length: int) -> int:
    """源码写法：round(pingpong(i + 0.5, len) - 0.5)，把下标折成三角波。"""
    return int(round(pingpong(i + 0.5, float(length)) - 0.5))


class Key:
    def __init__(self, time: float, value, transition: float = 1.0):
        self.time = float(time)
        self.value = value
        self.transition = float(transition)


# ---------------------------------------------------------------- _find


def find(keys: Sequence[Key], time: float, backward: bool = False,
         limit: bool = False, length: float = 0.0) -> int:
    """Animation::_find 的直译。空轨道返回 -2。"""
    n = len(keys)
    if n == 0:
        return -2
    low, high, middle = 0, n - 1, 0
    while low <= high:
        middle = (low + high) // 2
        if is_equal_approx(time, keys[middle].time):
            return middle
        elif time < keys[middle].time:
            high = middle - 1
        else:
            low = middle + 1
    if not backward:
        if keys[middle].time > time:
            middle -= 1
    else:
        if keys[middle].time < time:
            middle += 1
    if limit and -1 < middle < n:
        diff = length - keys[middle].time
        if ((math.copysign(1.0, keys[middle].time) < 0 and not is_zero_approx(keys[middle].time))
                or (math.copysign(1.0, diff) < 0 and not is_zero_approx(diff))):
            return -1
    return middle


class Track:
    """一条 Animation 轨道。"""

    LOOP_NONE, LOOP_LINEAR, LOOP_PINGPONG = 0, 1, 2
    NEAREST, LINEAR, LINEAR_ANGLE = 0, 1, 2

    def __init__(self, keys: Sequence[Key], length: float, loop_mode: int = LOOP_NONE,
                 interpolation: int = LINEAR, loop_wrap: bool = True,
                 update_discrete: bool = False):
        self.keys: List[Key] = list(keys)
        self.length = float(length)
        self.loop_mode = loop_mode
        self.interpolation = interpolation
        self.loop_wrap = loop_wrap
        self.update_discrete = update_discrete

    # ------------------------------------------------------------ 关键帧查找
    def find_key(self, time: float, find_mode: str = "NEAREST",
                 limit: bool = False, backward: bool = False) -> int:
        """track_find_key：NEAREST 不做命中判定，APPROX 用 is_equal_approx，EXACT 用 ==。"""
        k = find(self.keys, time, backward, limit, self.length)
        if k < 0 or k >= len(self.keys):
            return -1
        t = self.keys[k].time
        if find_mode == "APPROX" and not is_equal_approx(t, time):
            return -1
        if find_mode == "EXACT" and t != time:
            return -1
        return k

    # ------------------------------------------------------------ 插值
    def interpolate(self, time: float, backward: bool = False) -> Optional[float]:
        """Animation::_interpolate 的直译（标量版本）。"""
        keys = self.keys
        # len = _find(keys, length) + 1：超过 length 的关键帧被丢弃
        n = find(keys, self.length) + 1
        if n <= 0:
            return None
        if n == 1:
            return keys[0].value
        idx = find(keys, time, backward)
        if idx == -2:
            return None
        maxi = n - 1
        is_start_edge = (idx >= n) if backward else (idx == -1)
        is_end_edge = (idx == 0) if backward else (idx >= maxi)

        delta = 0.0
        from_ = 0.0
        nxt = 0
        interp = self.NEAREST if self.update_discrete else self.interpolation

        if not self.loop_wrap or self.loop_mode == self.LOOP_NONE:
            if is_start_edge:
                idx = maxi if backward else 0
            nxt = min(max(idx + (-1 if backward else 1), 0), maxi)
        elif self.loop_mode == self.LOOP_LINEAR:
            if is_start_edge:
                idx = 0 if backward else maxi
            nxt = int(posmod(idx + (-1 if backward else 1), n))
            if is_start_edge:
                if not backward:
                    endtime = self.length - keys[idx].time
                    if endtime < 0:
                        endtime = 0.0
                    delta = endtime + keys[nxt].time
                    from_ = endtime + time
                else:
                    endtime = keys[idx].time
                    if endtime > self.length:
                        endtime = self.length
                    delta = endtime + self.length - keys[nxt].time
                    from_ = endtime + self.length - time
            elif is_end_edge:
                if not backward:
                    delta = (self.length - keys[idx].time) + keys[nxt].time
                    from_ = time - keys[idx].time
                else:
                    delta = keys[idx].time + (self.length - keys[nxt].time)
                    from_ = (self.length - time) - (self.length - keys[idx].time)
        else:  # LOOP_PINGPONG
            if is_start_edge:
                idx = n if backward else -1
            nxt = pingpong_index(idx + (-1 if backward else 1), n)
            idx = pingpong_index(idx, n)
            if is_start_edge:
                if not backward:
                    endtime = keys[idx].time
                    if endtime < 0:
                        endtime = 0.0
                    delta = endtime + keys[nxt].time
                    from_ = endtime + time
                else:
                    endtime = self.length - keys[idx].time
                    if endtime > self.length:
                        endtime = self.length
                    delta = endtime + self.length - keys[nxt].time
                    from_ = endtime + self.length - time
            elif is_end_edge:
                if not backward:
                    delta = self.length * 2.0 - keys[idx].time - keys[nxt].time
                    from_ = time - keys[idx].time
                else:
                    delta = keys[idx].time + keys[nxt].time
                    from_ = (self.length - time) - (self.length - keys[idx].time)

        if not is_start_edge and not is_end_edge:
            if not backward:
                delta = keys[nxt].time - keys[idx].time
                from_ = time - keys[idx].time
            else:
                delta = (self.length - keys[nxt].time) - (self.length - keys[idx].time)
                from_ = (self.length - time) - (self.length - keys[idx].time)

        c = 0.0 if is_zero_approx(delta) else from_ / delta

        # transition：0 表示不做插值，直接取 idx 上的值
        tr = keys[idx].transition
        if tr == 0.0:
            return keys[idx].value

        if interp == self.NEAREST:
            return keys[idx].value
        if interp == self.LINEAR:
            return self._lerp(keys[idx].value, keys[nxt].value, c)
        if interp == self.LINEAR_ANGLE:
            return self._lerp_angle(keys[idx].value, keys[nxt].value, c)
        return keys[idx].value

    @staticmethod
    def _lerp(a: float, b: float, c: float) -> float:
        return (1.0 - c) * a + c * b

    @staticmethod
    def _lerp_angle(a: float, b: float, c: float) -> float:
        """_interpolate_angle：fposmod(lerp_angle(a, b, c), TAU)。"""
        d = b - a
        d = d - math.tau * math.floor((d + math.pi) / math.tau)  # 折到 [-π, π)
        return posmod(a + d * c, math.tau)


def track_len(keys: Sequence[Key], length: float) -> int:
    """源码：`int len = _find(p_keys, length) + 1;`，即超过 length 的关键帧被截断。"""
    return find(keys, length) + 1
