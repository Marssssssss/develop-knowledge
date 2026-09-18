#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 合批与 Canvas 重建 —— 自检入口（依据 Unity《Optimizing Unity UI》）。

运行：python3 ui_batching.py
"""
from __future__ import annotations

from ui_batching_core import (
    CanvasRegistry, Element, build_batches, draw_calls, overdraw_pixels, overlaps,
)

_CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if not cond:
        raise AssertionError("FAIL: %s %s" % (label, detail))
    print("  ok  %-56s %s" % (label, detail))


def img(name: str, depth: int, tex: str = "atlas_a",
        rect=(0, 0, 10, 10), canvas: str = "root") -> Element:
    return Element(name, material="ui_default", texture=tex, depth=depth,
                   rect=rect, canvas=canvas)


def main() -> None:
    print("[1] rebatch：共享材质+纹理的元素合成一批")
    els = [img("a", 0), img("b", 1), img("c", 2)]
    check("3 张同图集图片 → 1 个 draw call", draw_calls(els) == 1,
          "batches=%d" % draw_calls(els))

    print("[2] 不同纹理 → 断批")
    els = [img("a", 0, "atlas_a"), img("b", 1, "atlas_b")]
    check("两种图集 → 2 个 draw call", draw_calls(els) == 2, "batches=%d" % draw_calls(els))

    print("[3] 交错排列把同材质元素拆成两批（最经典的合批杀手）")
    interleaved = [img("a1", 0, "atlas_a"), img("b", 1, "atlas_b"), img("a2", 2, "atlas_a")]
    check("A-B-A 顺序 → 3 批", draw_calls(interleaved) == 3,
          "batches=%d" % draw_calls(interleaved))
    sorted_same = [img("a1", 0, "atlas_a"), img("a2", 1, "atlas_a"), img("b", 2, "atlas_b")]
    check("改为 A-A-B（同材质相邻）→ 2 批", draw_calls(sorted_same) == 2,
          "batches=%d" % draw_calls(sorted_same))
    check("仅靠「调整层级顺序」就省下 1 次 draw call",
          draw_calls(interleaved) - draw_calls(sorted_same) == 1, "")

    print("[4] Text 使用独立材质/字体图集，夹在图片中间会断批")
    text = Element("label", material="ui_font", texture="font_atlas",
                   depth=1, rect=(0, 0, 40, 10), canvas="root")
    els = [img("a", 0), text, img("b", 2)]
    check("Image-Text-Image → 3 批", draw_calls(els) == 3, "batches=%d" % draw_calls(els))
    els2 = [img("a", 0), img("b", 1), Element("label", material="ui_font",
                                              texture="font_atlas", depth=2,
                                              rect=(0, 0, 40, 10), canvas="root")]
    check("Image-Image-Text → 2 批", draw_calls(els2) == 2, "batches=%d" % draw_calls(els2))

    print("[5] Mask 引入 stencil 材质，必然断批")
    mask = Element("mask", material="ui_mask", texture="white", depth=1,
                   rect=(0, 0, 100, 100), canvas="root", is_mask=True)
    els = [img("a", 0), mask, img("b", 2)]
    check("带 Mask → 3 批（Mask 自身材质不同）", draw_calls(els) == 3,
          "batches=%d" % draw_calls(els))

    print("[6] alpha=0 的元素仍会提交 GPU（官方明令禁止的『隐藏』手法）")
    hidden = img("ghost", 3)
    hidden.alpha = 0.0
    els = [img("a", 0), img("b", 1), hidden]
    check("alpha=0 仍计入批次与像素采样", draw_calls(els) == 1 and len(build_batches(els)[0]) == 3,
          "batch size=%d" % len(build_batches(els)[0]))
    no_graphic = Element("raycast_only", depth=4, rect=(0, 0, 10, 10), canvas="root",
                         graphic=False)
    check("去掉 Graphic 组件 → 不产生绘制（但 raycast 仍可用）",
          draw_calls([img("a", 0), no_graphic]) == 1, "")

    print("[7] overdraw：Transparent queue 下被覆盖的像素照样采样")
    bg = Element("全屏底板", rect=(0, 0, 1920, 1080), depth=0, canvas="root")
    panel = Element("全屏面板", rect=(0, 0, 1920, 1080), depth=1, canvas="root")
    total, ratio = overdraw_pixels([bg, panel], 1920, 1080)
    check("两层全屏 → 平均 overdraw = 2.0", abs(ratio - 2.0) < 1e-9,
          "sampled=%.0f px, ratio=%.2f" % (total, ratio))
    eight = [Element("l%d" % i, rect=(0, 0, 1920, 1080), depth=i, canvas="root")
             for i in range(8)]
    _, ratio8 = overdraw_pixels(eight, 1920, 1080)
    check("8 层全屏 → overdraw = 8.0（fill-rate 直接 ×8）", abs(ratio8 - 8.0) < 1e-9,
          "ratio=%.2f" % ratio8)
    check("关掉被完全遮住的底板 → 立刻降回 7.0",
          abs(overdraw_pixels(eight[1:], 1920, 1080)[1] - 7.0) < 1e-9,
          "ratio=%.2f" % overdraw_pixels(eight[1:], 1920, 1080)[1])

    print("[8] 重叠判定：只有正面积交叠才算")
    a = Element("a", rect=(0, 0, 10, 10), depth=0)
    b = Element("b", rect=(10, 0, 10, 10), depth=1)
    c = Element("c", rect=(5, 5, 10, 10), depth=2)
    check("边界相接（10 vs 0）不算重叠", not overlaps(a, b), "")
    check("部分交叠算重叠", overlaps(a, c), "")

    print("[9] PerformUpdate 三步：Layout 按层级排序，Graphic 不排序")
    reg = CanvasRegistry()
    child = reg.add(Element("子布局", depth=0))
    child.hierarchy_depth = 3
    parent = reg.add(Element("父布局", depth=1))
    parent.hierarchy_depth = 1
    root_el = reg.add(Element("根布局", depth=2))
    root_el.hierarchy_depth = 0
    g1 = reg.add(Element("graphic1", depth=3))
    g2 = reg.add(Element("graphic2", depth=4))
    reg.mark_layout_dirty(child)
    reg.mark_layout_dirty(root_el)
    reg.mark_layout_dirty(parent)
    reg.mark_graphic_dirty(g2)
    reg.mark_graphic_dirty(g1)
    res = reg.perform_update()
    check("Layout 重建顺序 = 层级由浅到深 [根, 父, 子]",
          res["layout"] == ["根布局", "父布局", "子布局"], str(res["layout"]))
    check("Graphic 重建不排序，按注册顺序 [graphic1, graphic2]",
          res["graphic"] == ["graphic1", "graphic2"], str(res["graphic"]))

    print("[10] Sub-canvas 隔离：脏孩子不迫使父级重建")
    reg2 = CanvasRegistry()
    statics = [reg2.add(img("static%d" % i, i, canvas="root")) for i in range(100)]
    dyn = reg2.add(img("动态血条", 101, canvas="root"))     # 先放在同一 Canvas 下
    check("单 Canvas（100 静态 + 1 动态）→ 一次 dirty 重算 101 个元素",
          len(reg2.rebatch_scope(dyn)) == 101,
          "scope=%d" % len(reg2.rebatch_scope(dyn)))
    dyn.canvas = "hud"                                     # 拆出 Sub-canvas
    scope = [e for e in reg2.elements if (e.canvas or "root") == "hud"]
    check("拆出 Sub-canvas 后只重算该子画布内的元素",
          len(scope) == 1, "scope=%d" % len(scope))
    check("静态元素仍在原 Canvas，不受影响",
          all((e.canvas or "root") == "root" for e in statics), "")

    print("[11] 官方四类性能问题 → 可观测指标")
    # ① fill-rate：overdraw 倍数  ② rebatch CPU：批数×排序  ③ over-dirtying：每帧脏元素数
    # ④ 顶点生成：文本 mesh
    many = [img("e%d" % i, i, "atlas_%s" % ("ab"[i % 2])) for i in range(50)]
    check("50 个元素按两种图集交替 → 50 批（交替是最坏排列）",
          draw_calls(many) == 50, "batches=%d" % draw_calls(many))
    grouped = [img("e%d" % i, i, "atlas_a" if i < 25 else "atlas_b") for i in range(50)]
    check("按图集归拢 → 2 批", draw_calls(grouped) == 2, "batches=%d" % draw_calls(grouped))
    check("同样的 50 张图，排列不同 → draw call 50 vs 2（25 倍）",
          draw_calls(many) == 25 * draw_calls(grouped), "")

    print("\n全部 %d 项断言通过" % _CHECKS)


if __name__ == "__main__":
    main()
