#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 合批（batch build）与 Canvas 重建（rebuild）——核心算法。

依据 Unity 官方教程《Optimizing Unity UI》（Unity Learn，Unity Technologies）实读：
  · Canvas 是 native 组件，把它下辖的 mesh 合并成 batch 并生成渲染命令；
    结果被缓存，直到 Canvas 被标记 dirty —— 这一动作叫 **rebatch / batch build**
  · 计算 batch 需要「按 depth 排序 mesh，并检查重叠、共享材质等」，该操作是多线程的
  · Sub-canvas（嵌套 Canvas）隔离父子：脏孩子不会迫使父级重建，反之亦然
  · Rebuild 由 CanvasUpdateRegistry.PerformUpdate 驱动，WillRenderCanvases 每帧一次，
    三步：① 脏 Layout 重建 ② Clipping（Mask）剔除 ③ 脏 Graphic 重建
  · Layout 重建**按层级深度排序**（越靠近根越先算），Graphic 重建**不排序**
  · UI 几何一律在 Transparent queue，自后向前 alpha 混合 ——
    「被完全遮挡的像素一样会被采样」，overdraw 极易吃掉 fill-rate
  · alpha=0 的元素仍会提交 GPU；不需要绘制的元素可直接删掉 Graphic，raycast 照常工作

本文件只放算法；断言见 ui_batching.py。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


class Element:
    """一个 UI 可绘制元素。

    material/texture：合批键（Unity 里材质 + 纹理相同才可能进同一批）
    depth           ：排序深度（层级顺序 / 渲染顺序）
    rect            ：(x, y, w, h)，用于重叠判定与 overdraw 统计
    canvas          ：所属 Canvas 的 id（None 表示挂在最近的父 Canvas 上）
    graphic         ：是否有 Graphic 组件（False 时仍可 raycast，但不产生绘制）
    alpha           ：透明度；注意 0 仍然提交 GPU
    """

    def __init__(self, name: str, material: str = "default", texture: str = "white",
                 depth: int = 0, rect: Optional[Tuple[float, float, float, float]] = None,
                 canvas: Optional[str] = None, graphic: bool = True,
                 alpha: float = 1.0, is_mask: bool = False) -> None:
        self.name = name
        self.material = material
        self.texture = texture
        self.depth = depth
        self.rect = rect or (0, 0, 0, 0)
        self.canvas = canvas
        self.graphic = graphic
        self.alpha = alpha
        # 重建相关状态
        self.dirty_layout = False
        self.dirty_vertices = False
        self.dirty_material = False
        self.hierarchy_depth = 0     # 距根有几层 parent（Layout 排序用）
        self.is_mask = is_mask

    @property
    def batch_key(self) -> Tuple[str, str]:
        return (self.material, self.texture)

    def area(self) -> float:
        _, _, w, h = self.rect
        return w * h


def overlaps(a: Element, b: Element) -> bool:
    """两个矩形是否有正面积的交叠（用于判断合批是否被"夹断"）。"""
    ax, ay, aw, ah = a.rect
    bx, by, bw, bh = b.rect
    ox = min(ax + aw, bx + bw) - max(ax, bx)
    oy = min(ay + ah, by + bh) - max(ay, by)
    return ox > 0 and oy > 0


def build_batches(elements: Sequence[Element]) -> List[List[Element]]:
    """Canvas 的 rebatch：按 depth 排序后贪心成批。

    同一批的元素必须共享 (material, texture)；遇到不同的键就开新批。
    这是"按 depth 排序 + 检查共享材质"的最小忠实模型：
    → 交错排列（A1-B-A2，B 的材质不同）会把 A1 与 A2 拆成两批。
    """
    ordered = sorted(elements, key=lambda e: e.depth)
    batches: List[List[Element]] = []
    for e in ordered:
        if not e.graphic:
            continue                       # 无 Graphic 组件 → 不产生绘制
        if batches and batches[-1][0].batch_key == e.batch_key:
            batches[-1].append(e)
        else:
            batches.append([e])
    return batches


def draw_calls(elements: Sequence[Element]) -> int:
    """draw call 数 == 批数。"""
    return len(build_batches(elements))


def overdraw_pixels(elements: Sequence[Element], screen_w: float,
                    screen_h: float) -> Tuple[float, float]:
    """统计「采样像素总数 / 屏幕面积」= 平均 overdraw 倍数。

    依据："each pixel rasterized from a polygon will be sampled,
    even if it is wholly covered by other, opaque polygons."
    """
    total = sum(e.area() for e in elements if e.graphic)
    screen = screen_w * screen_h
    return total, (total / screen if screen else 0.0)


# ------------------------------------------------------------ 重建

class CanvasRegistry:
    """CanvasUpdateRegistry 的最小模型：每帧 PerformUpdate 三步。"""

    def __init__(self) -> None:
        self.elements: List[Element] = []
        self.masks: List[Element] = []
        self.canvas_children: Dict[str, List[str]] = {}   # canvas id → 其子 canvas id
        self.canvas_parent: Dict[str, str] = {}
        self.register_order: List[Element] = []           # IndexedSet 的插入顺序

    def add(self, e: Element) -> Element:
        self.elements.append(e)
        self.register_order.append(e)
        if e.is_mask:
            self.masks.append(e)
        if e.canvas and e.canvas not in self.canvas_children:
            self.canvas_children[e.canvas] = []
        return e

    def mark_layout_dirty(self, e: Element) -> None:
        e.dirty_layout = True

    def mark_graphic_dirty(self, e: Element) -> None:
        e.dirty_vertices = True

    def perform_update(self) -> Dict[str, List[str]]:
        """§PerformUpdate 三步：Layout → Clipping → Graphic。"""
        # ① Layout：按层级深度排序，越靠近根（parent 越少）越先
        dirty_layout = [e for e in self.elements if e.dirty_layout]
        dirty_layout.sort(key=lambda e: e.hierarchy_depth)
        order_layout = [e.name for e in dirty_layout]
        for e in dirty_layout:
            e.dirty_layout = False
        # ② Clipping：Mask 剔除（这里只记录参与剔除的 mask 数量）
        order_clip = [m.name for m in self.masks]
        # ③ Graphic：不排序，按注册（IndexedSet 插入）顺序
        dirty_graphic = [e for e in self.register_order if e.dirty_vertices]
        order_graphic = [e.name for e in dirty_graphic]
        for e in dirty_graphic:
            e.dirty_vertices = False
        return {"layout": order_layout, "clip": order_clip, "graphic": order_graphic}

    def rebatch_scope(self, dirty: Element) -> List[str]:
        """一次 dirty 会迫使哪些元素参与 rebatch。

        Sub-canvas 隔离：脏元素只让它**直属 Canvas** 下的元素重算；
        没有 Sub-canvas 时，整个 Canvas 都要重算。
        """
        scope = [e.name for e in self.elements
                 if (e.canvas or "root") == (dirty.canvas or "root")]
        return scope
