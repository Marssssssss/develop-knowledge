#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MSDF（Multi-channel Signed Distance Field）核心算法。

依据 Viktor Chlumský 硕士论文《Shape Decomposition for Multi-channel Distance
Fields》（Czech Technical University in Prague, 2015）实读实现：

  · §1.1.3  单通道 SDF 的"平滑"假设在有尖角的形状上失效 —— 依赖插值的重建
             只能近似"变化率大致恒定"的地方，尖角会被重建为圆角或缺口
  · §1.1.4  把形状分解成若干**平滑**子形状，各建一张距离场塞进 RGB 不同通道
  · §2.5    伪距离（pseudo-distance）：沿边所在直线量取的有符号距离，
             用叉积定符号 —— 它把"到最近点"的非线性脊线抹平
  · §3.2.2  median-of-three 模型：‖A‖ := median(a1, a2, a3)（式 3.13），
             论文用程序穷举后得到**最小维度 n = 3**；三个维度可互换（median 与顺序无关）
  · §3.2    Figure 3.7 给出一个角点四个象限的编码：
             凸角 (1,1,0) (1,0,0) (0,1,0) (0,0,0) → median 恰有 1 个象限为内
             凹角 (1,1,1) (1,0,1) (1,1,0) (1,0,0) → median 恰有 3 个象限为内
  · §4.3.1  颜色按**边**分配，每条边共四个颜色（内外各两个，对应前半段/后半段）
  · §5.1.4  论文给出的完整 GLSL：median = max(min(a,b), min(max(a,b),c))，
             d = median(s.r,s.g,s.b) − 0.5，w = clamp(d/fwidth(d)+0.5, 0, 1)

本文件只放算法；断言见 msdf.py。
"""
from __future__ import annotations

import math
from typing import Callable, List, Sequence, Tuple

Vec3 = Tuple[float, float, float]


# ------------------------------------------------- §5.1.4 median-of-three

def median3(a: float, b: float, c: float) -> float:
    """论文 §5.1.4 给的 GLSL 写法（只用 min/max）：max(min(a,b), min(max(a,b),c))。"""
    return max(min(a, b), min(max(a, b), c))


def median_ref(a: float, b: float, c: float) -> float:
    """参考实现：排序取中间值，用于校验上面那个 min/max 形式。"""
    return sorted((a, b, c))[1]


# ---------------------------------------------------- §3.2.2 角的象限编码

# Figure 3.7：凸角 / 凹角四个象限的三通道二进制向量（顺序：Q1 Q2 Q3 Q4）
CONVEX_QUADRANTS: List[Vec3] = [(1, 1, 0), (1, 0, 0), (0, 1, 0), (0, 0, 0)]
CONCAVE_QUADRANTS: List[Vec3] = [(1, 1, 1), (1, 0, 1), (1, 1, 0), (1, 0, 0)]


def quadrant_inside(vectors: Sequence[Vec3]) -> List[int]:
    """按 median-of-three 判定四个象限的内/外。"""
    return [1 if median3(*v) >= 1 else 0 for v in vectors]


# ------------------------------------------------ §3.2.2 n=3 的可执行复现

def smooth_patterns() -> List[int]:
    """一个"平滑通道"在四个象限上可能取到的 0/1 模式（位掩码）。

    论文要求每个通道都是**某个平滑形状的**距离场，所以该通道在角点处的
    内外边界要么不经过这个角（全 0 / 全 1），要么是一条直线 —— 直线把 4 个
    象限分成相邻的两两一组。这是本 demo 对 §3.2「平滑」约束的形式化。
    """
    out = [0b0000, 0b1111]
    for i in range(4):                       # 相邻两象限为 1（半平面）
        out.append(((1 << i) | (1 << ((i + 1) % 4))))
    return sorted(set(out))


def majority(bits: Sequence[int]) -> int:
    return 1 if sum(bits) * 2 > len(bits) else 0


def can_express(target: int, n: int, patterns: Sequence[int]) -> bool:
    """n 个通道（每个取自 patterns）的多数表决能否恰好表达 target 这个象限模式。"""
    from itertools import product
    for combo in product(patterns, repeat=n):
        ok = True
        for q in range(4):
            bits = [(p >> q) & 1 for p in combo]
            if majority(bits) != ((target >> q) & 1):
                ok = False
                break
        if ok:
            return True
    return False


def min_dimension_for_corner(target: int, max_n: int = 4) -> int:
    """能表达该角所需的最小通道数（论文结论：凸/凹两种角都要能表达 → n=3）。"""
    pats = smooth_patterns()
    for n in range(1, max_n + 1):
        if can_express(target, n, pats):
            return n
    return -1


# --------------------------------------- §2.5 伪距离 vs 真实距离

def pseudo_distance(px: float, py: float, ax: float, ay: float,
                    bx: float, by: float) -> float:
    """点 P 到边 AB 所在**直线**的有符号伪距离（叉积定符号）。

    与"到线段最近点"的真实距离不同：只要投影落在边内两者相等，
    投影落到延长线上时伪距离仍按直线量取 —— 因此场里没有脊线、可精确插值。
    """
    ex, ey = bx - ax, by - ay
    length = math.hypot(ex, ey)
    if length == 0:
        return math.hypot(px - ax, py - ay)
    return (ex * (py - ay) - ey * (px - ax)) / length


def true_distance_to_segment(px: float, py: float, ax: float, ay: float,
                             bx: float, by: float) -> float:
    """点到线段的最短距离（无符号）。"""
    ex, ey = bx - ax, by - ay
    denom = ex * ex + ey * ey
    if denom == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * ex + (py - ay) * ey) / denom))
    return math.hypot(px - (ax + t * ex), py - (ay + t * ey))


# --------------------------------- 被测形状：一个凸直角（象限 {x>cx, y>cy}）

# 角点刻意**不对齐网格**（真实字形里尖角几乎永远不会落在纹素中心），
# 这正是单通道 SDF 暴露插值误差、而 MSDF 仍然精确的场景。
CORNER_X, CORNER_Y = 0.37, -0.21


def corner_sdf(x: float, y: float) -> float:
    """该形状的真实有符号距离（内部为正）。

    dx>0,dy>0 → min(dx,dy)；dx<0,dy>0 → dx；dx>0,dy<0 → dy；dx<0,dy<0 → −√(dx²+dy²)
    最后一支是非线性的 —— 这就是单通道 SDF 在角点附近必然出错的根源。
    """
    dx, dy = x - CORNER_X, y - CORNER_Y
    if dx > 0 and dy > 0:
        return min(dx, dy)
    if dy > 0:
        return dx
    if dx > 0:
        return dy
    return -math.hypot(dx, dy)


def corner_msdf(x: float, y: float, floor_value: float = -8.0) -> Vec3:
    """该形状的 MSDF：两通道各是一条直线（半平面），第三通道恒为外。

    R = y−cy（边 y=cy 的伪距离，上方为内）、G = x−cx、B = 常数（恒外）。
    median(R, G, B) 恰好等价于 min(dx, dy) —— 尖角由两条直线相交得到。
    """
    return (y - CORNER_Y, x - CORNER_X, floor_value)


def inside_truth(x: float, y: float) -> bool:
    return x > CORNER_X and y > CORNER_Y


def inside_from_sdf(d: float) -> bool:
    return d > 0


def inside_from_msdf(s: Vec3) -> bool:
    return median3(*s) > 0


# ------------------------------------------------------------ 采样与重建

class Field:
    """把连续场离散采样到 N×N 网格，供双线性重建使用。"""

    def __init__(self, n: int, lo: float, hi: float, fn: Callable) -> None:
        self.n = n
        self.lo = lo
        self.hi = hi
        self.step = (hi - lo) / (n - 1)
        self.data = [[fn(lo + c * self.step, lo + r * self.step) for c in range(n)]
                     for r in range(n)]
        self.fn = fn

    def bilinear(self, x: float, y: float):
        """双线性采样（等价 GPU 的 GL_LINEAR），返回值与场元素同类型。"""
        fx = (x - self.lo) / self.step
        fy = (y - self.lo) / self.step
        c0 = min(self.n - 2, max(0, int(math.floor(fx))))
        r0 = min(self.n - 2, max(0, int(math.floor(fy))))
        tx = min(1.0, max(0.0, fx - c0))
        ty = min(1.0, max(0.0, fy - r0))
        v00, v01 = self.data[r0][c0], self.data[r0][c0 + 1]
        v10, v11 = self.data[r0 + 1][c0], self.data[r0 + 1][c0 + 1]
        if isinstance(v00, tuple):
            return tuple(
                (v00[k] * (1 - tx) + v01[k] * tx) * (1 - ty)
                + (v10[k] * (1 - tx) + v11[k] * tx) * ty
                for k in range(len(v00)))
        return ((v00 * (1 - tx) + v01 * tx) * (1 - ty)
                + (v10 * (1 - tx) + v11 * tx) * ty)


def reconstruct_errors(field: Field, lo: float, hi: float,
                       out_n: int, mode: str) -> Tuple[int, float]:
    """在 out_n×out_n 上重建，统计与真值不一致的像素数与最大边界偏差。"""
    step = (hi - lo) / (out_n - 1)
    wrong = 0
    max_dev = 0.0
    for r in range(out_n):
        y = lo + r * step
        for c in range(out_n):
            x = lo + c * step
            s = field.bilinear(x, y)
            got = inside_from_msdf(s) if mode == "msdf" else inside_from_sdf(s)
            want = inside_truth(x, y)
            if got != want:
                wrong += 1
                # 偏差 = 该点到真实边界（两条半轴）的距离
                dx, dy = x - CORNER_X, y - CORNER_Y
                dev = (min(abs(dx), abs(dy)) if (dx > 0) != (dy > 0)
                       else math.hypot(dx, dy))
                max_dev = max(max_dev, dev)
    return wrong, max_dev
