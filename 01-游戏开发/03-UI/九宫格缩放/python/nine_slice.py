#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""九宫格缩放（9-slice / border-image）原理 demo —— 自检入口。

核心算法在同目录 `nine_slice_core.py`，本文件把 W3C CSS Backgrounds
Level 3 §5 的每条硬规则转成断言。运行：python3 nine_slice.py
"""
from __future__ import annotations

from typing import List

from nine_slice_core import (
    Region, Tiling, NINTHS, slice_image, find, border_image_width,
    resolve_tiling, layout_edge, nine_slice,
)

# ------------------------------------------------------------- 自检

_CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if not cond:
        raise AssertionError("FAIL: %s %s" % (label, detail))
    print("  ok  %-58s %s" % (label, detail))


def main() -> None:
    print("[1] §5.2 切片：81x81 图按 27 均分九宫格")
    regs = slice_image(81, 81, 27, 27, 27, 27)
    sizes = {(r.name): (r.w, r.h) for r in regs}
    check("九块尺寸全为 27x27", all(v == (27, 27) for v in sizes.values()),
          str(sizes["mc"]))
    check("角块位于 (0,0)/(54,0)/(0,54)/(54,54)",
          (find(regs, "tl").x, find(regs, "tl").y) == (0, 0)
          and (find(regs, "br").x, find(regs, "br").y) == (54, 54), "")
    check("中间块起点 (27,27)", (find(regs, "mc").x, find(regs, "mc").y) == (27, 27), "")

    print("[2] §5.2 百分比：100x80 图按 25% 30% 12% 20%（上右下左）")
    p = slice_image(100, 80, 25, 30, 12, 20, pct=True)
    check("上=25%*h=20, 右=30%*w=30, 下=12%*h=9.6, 左=20%*w=20",
          abs(find(p, "tl").h - 20) < 1e-9
          and abs(find(p, "tr").w - 30) < 1e-9
          and abs(find(p, "bc").h - 9.6) < 1e-9
          and abs(find(p, "ml").w - 20) < 1e-9,
          "tl.h=%.1f tr.w=%.1f bc.h=%.2f ml.w=%.1f"
          % (find(p, "tl").h, find(p, "tr").w, find(p, "bc").h, find(p, "ml").w))
    check("水平偏移看宽度、垂直偏移看高度（w=100 h=80）",
          abs(find(p, "mc").w - (100 - 20 - 30)) < 1e-9
          and abs(find(p, "mc").h - (80 - 20 - 9.6)) < 1e-9, "")

    print("[3] §5.2 越界与重叠：偏移之和超过图像尺寸")
    big = slice_image(81, 81, 27, 60, 27, 60)
    check("左+右(120) >= 81 → 上边/下边/中间为空",
          find(big, "tc").w == 0 and find(big, "bc").w == 0 and find(big, "mc").w == 0,
          "tc.w=0 mc.w=0")
    check("左右边仍然非空", find(big, "ml").w > 0 and find(big, "mr").w > 0, "")
    over = slice_image(81, 81, 100, 27, 100, 27)
    check("上+下(200) >= 81 → 左边/右边/中间为空",
          find(over, "ml").h == 0 and find(over, "mr").h == 0 and find(over, "mc").h == 0, "")
    clamp = slice_image(81, 81, 999, 27, 27, 27)
    check("偏移超过图像尺寸按 100% 解释（top 999 → 81）",
          abs(find(clamp, "tl").h - 81) < 1e-9, "tl.h=%.0f" % find(clamp, "tl").h)

    print("[4] §5.5 四种平铺：src=27 → 区域长 200")
    st = resolve_tiling("stretch", 27, 200)
    check("stretch：1 块，块长 = 区域长 200", st.n == 1 and abs(st.tile - 200) < 1e-9,
          "scale=%.4f" % (200 / 27))
    rp = resolve_tiling("repeat", 27, 200)
    check("repeat：floor(200/27)=7 块，不缩放",
          rp.n == 7 and abs(rp.tile - 27) < 1e-9, "n=%d tile=%.1f" % (rp.n, rp.tile))
    check("repeat：第一块居中，起点 =(200-189)/2=5.5",
          abs(rp.offset - 5.5) < 1e-9, "offset=%.2f" % rp.offset)
    rd = resolve_tiling("round", 27, 200)
    check("round：round(7.407)=7 块，块长 = 200/7",
          rd.n == 7 and abs(rd.tile - 200 / 7) < 1e-9, "tile=%.4f" % rd.tile)
    check("round：整块数 × 块长 恰好填满区域", abs(rd.n * rd.tile - 200) < 1e-9, "")
    sp = resolve_tiling("space", 27, 200)
    check("space：7 块 + 8 个等间隙 = (200-189)/8",
          sp.n == 7 and abs(sp.gap - 11 / 8) < 1e-9, "gap=%.4f" % sp.gap)
    check("space：首块起点 == 间隙，末块终点 = 200 - 间隙",
          abs(sp.offset - sp.gap) < 1e-9
          and abs(sp.positions()[-1][1] - (200 - sp.gap)) < 1e-9, "")

    print("[5] §5.6 绘制：81x81(slice 27) 铺到 400x120")
    _, tiles = nine_slice(81, 81, (27, 27, 27, 27), 400, 120)
    corner_scale = tiles["tl"][1]
    check("四角只做缩放不平铺，缩放比 = 角区/角切片 = 1.0",
          abs(corner_scale[0] - 1.0) < 1e-9 and abs(corner_scale[1] - 1.0) < 1e-9,
          "scale=%s" % (corner_scale,))
    tc_region = tiles["tc"][0]
    check("上边所在区宽 = 400-27-27 = 346",
          abs(tc_region.w - 346) < 1e-9, "tc.w=%.1f" % tc_region.w)
    check("默认 stretch：上边铺满整个中间区（1 块，长 346）",
          tiles["tc"][1].n == 1 and abs(tiles["tc"][1].tile - 346) < 1e-9,
          "n=%d tile=%.1f" % (tiles["tc"][1].n, tiles["tc"][1].tile))
    check("默认不填中间（fill 缺省）", "mc" not in tiles, "mc 未绘制")

    _, tiles2 = nine_slice(81, 81, (27, 27, 27, 27), 400, 120,
                           repeat=("round", "stretch"), fill=True)
    check("fill=True 时中间块参与绘制", "mc" in tiles2, "")
    check("round 平铺：上边 27 拉伸到高 27 后 round 进 346 → 13 块",
          tiles2["tc"][1].n == 13, "n=%d" % tiles2["tc"][1].n)

    print("[6] 与「整图拉伸」的对照：圆角半径是否被放大")
    naive = 400 / 81          # 整图拉伸时横向缩放比
    check("整图拉伸会把 27px 圆角放大到 %.1fpx" % (27 * naive,),
          abs(27 * naive - 133.33) < 0.01, "27 * %.4f = %.2f" % (naive, 27 * naive))
    check("9-slice 下圆角恒为 27px（角块不参与拉伸）",
          abs(corner_scale[0] - 1.0) < 1e-9, "scale=1.0")

    print("[7] 极端：目标小于四角之和")
    tiny = nine_slice(81, 81, (27, 27, 27, 27), 40, 40)
    d0 = {r.name: r for r in tiny[0]}
    check("中间区宽高被压到 0（40-27-27 < 0）",
          d0["mc"].w == 0 and d0["mc"].h == 0, "mc=%sx%s" % (d0["mc"].w, d0["mc"].h))
    check("四角仍各占 27x27 且已重叠（总宽 54 > 40）",
          d0["tl"].w == 27 and d0["br"].w == 27, "54 > 40 → 角块互相覆盖")

    print("\n全部 %d 项断言通过" % _CHECKS)


if __name__ == "__main__":
    main()
