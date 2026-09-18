#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""九宫格缩放（9-slice / border-image）核心算法（由 nine_slice.py 的自检驱动）。

依据：W3C CSS Backgrounds and Borders Module Level 3 §5「Border Images」
（§5.2 border-image-slice / §5.3 border-image-width / §5.5 border-image-repeat /
 §5.6 Drawing the Border Image），本 demo 把规范文本逐条翻译成可执行代码，
并用断言把规范里的每一条硬规则钉住。

游戏里的对应物：Unity Sprite Editor 的 9-Slicing、UGUI Image 的
Image.Type.Sliced、Android NinePatch（.9.png）。它们与 CSS 的差异见 README。

运行：python3 nine_slice.py
"""
from __future__ import annotations

import math
from typing import List, NamedTuple, Optional, Tuple

# ---------------------------------------------------------------- 数据结构

NINTHS = ("tl", "tc", "tr", "ml", "mc", "mr", "bl", "bc", "br")


class Region(NamedTuple):
    """源图/目标区上的一个矩形分区（x, y 为左上角；w, h 可为 0 表示空）。"""
    name: str
    x: float
    y: float
    w: float
    h: float


class Tiling(NamedTuple):
    """一条边/中块在某个轴向上的平铺结果。"""
    kind: str        # stretch | repeat | round | space
    n: int           # 铺几块
    tile: float      # 单块铺开后的长度
    offset: float    # 第一块起点（相对区域起点）
    gap: float       # space 时的间隙（仅 space 非 0）

    def positions(self) -> List[Tuple[float, float]]:
        """返回每块 (起点, 终点) 的区间列表。"""
        out = []
        if self.n <= 0 or self.tile <= 0:
            return out
        cur = self.offset
        for _ in range(self.n):
            out.append((cur, cur + self.tile))
            cur += self.tile + self.gap
        return out


# ------------------------------------------------------- §5.2 图像切片

def slice_image(iw: float, ih: float,
                top: float, right: float, bottom: float, left: float,
                pct: bool = False) -> List[Region]:
    """把图像按四条内缩偏移切成九宫格。

    规范要点：
      - 百分比相对图像自身尺寸（水平偏移看宽度、垂直偏移看高度）
      - 「Computed values larger than the size of the image are interpreted as 100%」
        → 偏移先按图像尺寸 clamp
      - 左右偏移之和 >= 图像宽度 → 上边、下边、中间三块为空
        上下偏移之和 >= 图像高度 → 左边、右边、中间三块为空
    """
    if pct:
        top, bottom = top / 100.0 * ih, bottom / 100.0 * ih
        right, left = right / 100.0 * iw, left / 100.0 * iw
    # 超出图像尺寸的取值按 100% 解释
    top, bottom = min(top, ih), min(bottom, ih)
    right, left = min(right, iw), min(left, iw)

    h_empty = (top + bottom >= ih)   # 上/下边与中间为空
    v_empty = (left + right >= iw)   # 左/右边与中间为空

    mw = 0.0 if v_empty else max(0.0, iw - left - right)
    mh = 0.0 if h_empty else max(0.0, ih - top - bottom)

    xs = (0.0, left, left + mw)
    ws = (left, mw, right)
    ys = (0.0, top, top + mh)
    hs = (top, mh, bottom)

    out = []
    for r in range(3):
        for c in range(3):
            name = NINTHS[r * 3 + c]
            w = ws[c] if not (v_empty and c == 1) else 0.0
            h = hs[r] if not (h_empty and r == 1) else 0.0
            if v_empty and c == 1:
                w = 0.0
            if h_empty and r == 1:
                h = 0.0
            out.append(Region(name, xs[c], ys[r], w, h))
    return out


def find(regions: List[Region], name: str) -> Region:
    for r in regions:
        if r.name == name:
            return r
    raise KeyError(name)


# --------------------------------------------- §5.3 border-image-width

def border_image_width(value, border_width: float,
                       slice_size: float, area_size: float) -> float:
    """计算某一侧的 border image 区宽度。

    value 取值形态：
      - 数字        → 该数字 × border-width（即几倍边框宽度）
      - 'auto'      → 取对应切片的内在尺寸（无内在尺寸时退回 border-width）
      - 百分比字符串 → 相对 border image area 的对应尺寸
      - 绝对长度（数字，此处由调用方以 ('len', x) 传入区分）
    """
    if isinstance(value, tuple):
        kind, v = value
        if kind == "len":
            return v
        if kind == "pct":
            return v / 100.0 * area_size
        raise ValueError(kind)
    if value == "auto":
        return slice_size if slice_size > 0 else border_width
    # 纯数字 = border-width 的倍数
    return value * border_width


# ------------------------------------------------- §5.5 / §5.6 平铺

def resolve_tiling(kind: str, src: float, dst: float) -> Tiling:
    """把长度为 src 的一块，按 kind 规则铺进长度 dst 的区域。

    stretch：拉伸到正好填满（1 块，缩放比 dst/src）
    repeat ：不缩放，铺 floor(dst/src) 块，**居中**放置（半块溢出被两侧裁掉）
    round  ：铺 round(dst/src) 块（至少 1 块），缩放每块使总长正好等于 dst
    space  ：不缩放，铺 floor(dst/src) 块，余量均分到 n+1 个间隙（块前后与块间相等）
    """
    if src <= 0 or dst <= 0:
        return Tiling(kind, 0, 0.0, 0.0, 0.0)
    if kind == "stretch":
        return Tiling(kind, 1, dst, 0.0, 0.0)
    n_float = dst / src
    if kind == "repeat":
        n = int(math.floor(n_float))
        return Tiling(kind, n, src, (dst - n * src) / 2.0, 0.0)
    if kind == "round":
        n = max(1, int(round(n_float)))
        return Tiling(kind, n, dst / n, 0.0, 0.0)
    if kind == "space":
        n = int(math.floor(n_float))
        gap = (dst - n * src) / (n + 1) if n > 0 else dst
        return Tiling(kind, n, src, gap, gap)
    raise ValueError(kind)


def layout_edge(src_w: float, src_h: float, region_h: float,
                middle_len: float, kind: str) -> Tiling:
    """一条水平边（上/中/下）的完整两步布局。

    第一步（Scale to border-image-width）：把边的高度拉伸到区域高度，宽度等比缩放；
    第二步（Scale to border-image-repeat）：按 kind 在「中间区域长度」上平铺。
    """
    if src_h <= 0 or region_h <= 0:
        return Tiling(kind, 0, 0.0, 0.0, 0.0)
    scale1 = region_h / src_h
    return resolve_tiling(kind, src_w * scale1, middle_len)


# ------------------------------------------------------------ 9-slice

def nine_slice(iw: float, ih: float, slices: Tuple[float, float, float, float],
               tw: float, th: float,
               widths=None, repeat: Tuple[str, str] = ("stretch", "stretch"),
               fill: bool = False):
    """把 iw×ih 的源图按 9-slice 铺到 tw×th 的目标框，返回九块的目标矩形。"""
    top, right, bottom, left = slices
    src = slice_image(iw, ih, top, right, bottom, left)

    if widths is None:
        # 默认：border-image-width 初始值为 1（= 1 × border-width），
        # 游戏里一般直接取切片尺寸，这里用切片尺寸作为「边框区」厚度。
        wt, wr, wb, wl = top, right, bottom, left
    else:
        wt, wr, wb, wl = widths

    # 目标侧九个区：四角尺寸 = 对应 border image 区的宽/高
    mw = max(0.0, tw - wl - wr)
    mh = max(0.0, th - wt - wb)
    rx, ry = (0.0, wl, wl + mw), (0.0, wt, wt + mh)
    rw, rh = (wl, mw, wr), (wt, mh, wb)

    dst = []
    for r in range(3):
        for c in range(3):
            dst.append(Region(NINTHS[r * 3 + c], rx[c], ry[r], rw[c], rh[r]))

    tiles = {}
    # 四角：直接缩放到角区域，**永不平铺**
    for corner in ("tl", "tr", "bl", "br"):
        s, d = find(src, corner), find(dst, corner)
        if s.w > 0 and s.h > 0 and d.w > 0 and d.h > 0:
            tiles[corner] = (d, (d.w / s.w, d.h / s.h))
    # 上下边 + 中间：水平方向按 repeat[0] 平铺，垂直方向拉伸到区域高
    for name in ("tc", "mc", "bc"):
        s, d = find(src, name), find(dst, name)
        if s.w > 0 and s.h > 0 and d.h > 0:
            t = layout_edge(s.w, s.h, d.h, d.w, repeat[0])
            tiles[name] = (d, t)
    # 左右边：垂直方向按 repeat[1] 平铺，水平方向拉伸到区域宽
    for name in ("ml", "mr"):
        s, d = find(src, name), find(dst, name)
        if s.w > 0 and s.h > 0 and d.w > 0:
            scale1 = d.w / s.w
            t = resolve_tiling(repeat[1], s.h * scale1, d.h)
            tiles[name] = (d, t)
    if not fill:
        tiles.pop("mc", None)
    return dst, tiles
