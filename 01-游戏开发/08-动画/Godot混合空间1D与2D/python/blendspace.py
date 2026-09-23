"""Godot 4 BlendSpace1D / BlendSpace2D 的权重计算最小转写。

依据（实读原文，godotengine/godot@master）：
- scene/animation/animation_blend_space_1d.cpp（24614 B）：`AnimationNodeBlendSpace1D::_process`
- scene/animation/animation_blend_space_2d.cpp（35094 B）：`AnimationNodeBlendSpace2D::_process`
  与 `AnimationNodeBlendSpace2D::_blend_triangle`
- core/math/math_defs.h：CMP_EPSILON = 0.00001

覆盖：插值/离散两种 blend_mode 的权重、closest 的平局取向、sync 的目标长度、
2D 的重心坐标（barycentric）与「点在三角形外」的投影回退。
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

CMP_EPSILON = 0.00001

INTERPOLATED, DISCRETE, DISCRETE_CARRY = 0, 1, 2
SYNC_NONE, SYNC_CYCLIC_MUTABLE, SYNC_FIXED = 0, 1, 2


class BlendSpace1D:
    """一维混合空间。positions 允许乱序（源码是线性扫描，不要求有序）。"""

    def __init__(self, positions: Sequence[float], lengths: Optional[Sequence[float]] = None,
                 blend_mode: int = INTERPOLATED, sync_mode: int = SYNC_NONE,
                 cyclic_length: float = 0.0):
        self.positions = [float(p) for p in positions]
        self.lengths = [float(x) for x in lengths] if lengths else [0.0] * len(positions)
        self.blend_mode = blend_mode
        self.sync_mode = sync_mode
        self.cyclic_length = float(cyclic_length)

    def process(self, blend_pos: float) -> dict:
        n = len(self.positions)
        weights = [0.0] * n
        new_closest = -1
        if n == 0:
            return {"weights": weights, "closest": -1, "delta_scale": None}
        if n == 1:
            weights[0] = 1.0
            return {"weights": weights, "closest": 0, "delta_scale": self._delta_scale(weights)}

        if self.blend_mode == INTERPOLATED:
            point_lower, pos_lower = -1, 0.0
            point_higher, pos_higher = -1, 0.0
            for i, pos in enumerate(self.positions):
                if pos <= blend_pos:
                    if point_lower == -1 or pos > pos_lower:
                        point_lower, pos_lower = i, pos
                elif point_higher == -1 or pos < pos_higher:
                    point_higher, pos_higher = i, pos
            if point_lower == -1 and point_higher != -1:
                weights[point_higher] = 1.0
            elif point_higher == -1:
                weights[point_lower] = 1.0
            else:
                d = pos_higher - pos_lower
                p = (blend_pos - pos_lower) / d
                weights[point_lower] = 1.0 - p
                weights[point_higher] = p
            # closest：>= 让「下标更大者」在权重相等时胜出
            max_weight, new_closest = 0.0, -1
            for i in range(n):
                if weights[i] >= max_weight:
                    max_weight, new_closest = weights[i], i
        else:
            # DISCRETE：< 让「下标更小者」在距离相等时胜出
            best = 1e20
            for i in range(n):
                d = abs(self.positions[i] - blend_pos)
                if d < best:
                    best, new_closest = d, i
            weights[new_closest] = 1.0
        return {"weights": weights, "closest": new_closest,
                "delta_scale": self._delta_scale(weights)}

    def _delta_scale(self, weights: Sequence[float]) -> Optional[float]:
        """sync 的时间缩放：返回 inv_target_length（None 表示不同步）。"""
        if self.sync_mode == SYNC_NONE:
            return None
        if self.sync_mode == SYNC_CYCLIC_MUTABLE:
            target, total = 0.0, 0.0
            for i, w in enumerate(weights):
                if w > 0.0 and self.lengths[i] > CMP_EPSILON:
                    target += w * self.lengths[i]
                    total += w
            if total > CMP_EPSILON:
                target /= total
            return (1.0 / target) if target > CMP_EPSILON else 0.0
        return (1.0 / self.cyclic_length) if self.cyclic_length > CMP_EPSILON else 0.0


def _closest_point_on_segment(p: Tuple[float, float], a: Tuple[float, float],
                              b: Tuple[float, float]) -> Tuple[float, float]:
    """线段上的最近点（Geometry2D::get_closest_point_to_segment 的标准做法）。"""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return a
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / denom
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return (ax + dx * t, ay + dy * t)


def _dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


class BlendSpace2D:
    """二维混合空间。triangles 为顶点下标三元组（可来自 Delaunay2D::triangulate）。"""

    def __init__(self, positions: Sequence[Tuple[float, float]],
                 triangles: Sequence[Tuple[int, int, int]] = (),
                 lengths: Optional[Sequence[float]] = None,
                 blend_mode: int = INTERPOLATED):
        self.positions = [(float(x), float(y)) for x, y in positions]
        self.triangles = [tuple(t) for t in triangles]
        self.lengths = [float(x) for x in lengths] if lengths else [0.0] * len(positions)
        self.blend_mode = blend_mode

    @staticmethod
    def blend_triangle(pos: Tuple[float, float],
                       pts: Sequence[Tuple[float, float]]) -> List[float]:
        """_blend_triangle：先判顶点精确相等，再用点积求重心坐标。"""
        w = [0.0, 0.0, 0.0]
        for i in range(3):
            if abs(pos[0] - pts[i][0]) < 1e-6 and abs(pos[1] - pts[i][1]) < 1e-6:
                w[i] = 1.0
                return w
        v0 = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        v1 = (pts[2][0] - pts[0][0], pts[2][1] - pts[0][1])
        v2 = (pos[0] - pts[0][0], pos[1] - pts[0][1])
        d00 = v0[0] * v0[0] + v0[1] * v0[1]
        d01 = v0[0] * v1[0] + v0[1] * v1[1]
        d11 = v1[0] * v1[0] + v1[1] * v1[1]
        d20 = v2[0] * v0[0] + v2[1] * v0[1]
        d21 = v2[0] * v1[0] + v2[1] * v1[1]
        denom = d00 * d11 - d01 * d01
        if denom == 0.0:
            return [1.0, 0.0, 0.0]
        vv = (d11 * d20 - d01 * d21) / denom
        ww = (d00 * d21 - d01 * d20) / denom
        return [1.0 - vv - ww, vv, ww]

    def process(self, blend_pos: Tuple[float, float]) -> dict:
        n = len(self.positions)
        weights = [0.0] * n
        if n == 0 or (self.blend_mode == INTERPOLATED and not self.triangles):
            return {"weights": weights, "closest": -1, "triangle": -1}
        if self.blend_mode != INTERPOLATED:
            best, new_closest = 1e20, -1
            for i in range(n):
                p = self.positions[i]
                d = (p[0] - blend_pos[0]) ** 2 + (p[1] - blend_pos[1]) ** 2
                if d < best:  # 严格小于 → 平局时下标小者胜
                    best, new_closest = d, i
            weights[new_closest] = 1.0
            return {"weights": weights, "closest": new_closest, "triangle": -1}

        tri_idx, tri_w = -1, [0.0, 0.0, 0.0]
        for i, tri in enumerate(self.triangles):
            pts = [self.positions[tri[j]] for j in range(3)]
            if _point_in_triangle(blend_pos, pts):
                tri_idx, tri_w = i, self.blend_triangle(blend_pos, pts)
                break
        if tri_idx == -1:
            # 回退：找所有三角形边上的最近点，用「边内插值」给权重
            best = None
            for i, tri in enumerate(self.triangles):
                pts = [self.positions[tri[j]] for j in range(3)]
                for j in range(3):
                    a, b = pts[j], pts[(j + 1) % 3]
                    c2 = _closest_point_on_segment(blend_pos, a, b)
                    if best is None or _dist(c2, blend_pos) < best[0]:
                        d = _dist(a, b)
                        if d == 0.0:
                            w = [0.0, 0.0, 0.0]
                            w[j] = 1.0
                        else:
                            cc = _dist(a, c2) / d
                            w = [0.0, 0.0, 0.0]
                            w[j] = 1.0 - cc
                            w[(j + 1) % 3] = cc
                        best = (_dist(c2, blend_pos), i, w)
            tri_idx, tri_w = best[1], best[2]

        max_w, new_closest = 0.0, -1
        for j in range(3):
            pi = self.triangles[tri_idx][j]
            weights[pi] = tri_w[j]
            if tri_w[j] >= max_w:  # >= → 平局时下标大者胜
                max_w, new_closest = tri_w[j], pi
        return {"weights": weights, "closest": new_closest, "triangle": tri_idx}


def _sign(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _point_in_triangle(p: Tuple[float, float],
                       t: Sequence[Tuple[float, float]]) -> bool:
    d1, d2, d3 = _sign(p, t[0], t[1]), _sign(p, t[1], t[2]), _sign(p, t[2], t[0])
    neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (neg and pos)
