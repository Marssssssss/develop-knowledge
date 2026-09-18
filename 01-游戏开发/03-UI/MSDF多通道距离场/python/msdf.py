#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MSDF 多通道距离场 —— 自检入口（依据 Chlumský 2015 硕士论文）。

运行：python3 msdf.py
"""
from __future__ import annotations

import itertools
import math
import random

from msdf_core import (
    CONCAVE_QUADRANTS, CONVEX_QUADRANTS, Field, can_express, corner_msdf,
    corner_sdf, inside_from_msdf, inside_from_sdf, inside_truth,
    median3, median_ref, min_dimension_for_corner, pseudo_distance,
    quadrant_inside, reconstruct_errors, smooth_patterns,
    true_distance_to_segment,
)

_CHECKS = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global _CHECKS
    _CHECKS += 1
    if not cond:
        raise AssertionError("FAIL: %s %s" % (label, detail))
    print("  ok  %-56s %s" % (label, detail))


def main() -> None:
    print("[1] §5.1.4 median 的 min/max 写法 == 真正的中位数")
    ok = True
    for a, b, c in itertools.permutations((0.2, 0.9, 0.5)):
        ok = ok and abs(median3(a, b, c) - median_ref(a, b, c)) < 1e-12
    check("6 种排列全部与排序取中一致", ok, "")
    rnd = random.Random(7)
    ok = all(abs(median3(*t) - median_ref(*t)) < 1e-12
             for t in ((rnd.random(), rnd.random(), rnd.random()) for _ in range(200)))
    check("200 组随机三元组一致", ok, "")
    check("median 与顺序无关（故三通道可互换）",
          median3(0.1, 0.9, 0.5) == median3(0.9, 0.5, 0.1) == 0.5, "")

    print("[2] §3.2.2 Figure 3.7：角点四象限的 median 判定")
    ci = quadrant_inside(CONVEX_QUADRANTS)
    vi = quadrant_inside(CONCAVE_QUADRANTS)
    check("凸角：median 后恰有 1 个象限为内", sum(ci) == 1, str(ci))
    check("凹角：median 后恰有 3 个象限为内", sum(vi) == 3, str(vi))
    check("凸角的内部象限是 (1,1,0) 那个（两通道为内、第三通道为外）",
          ci[CONVEX_QUADRANTS.index((1, 1, 0))] == 1, str(CONVEX_QUADRANTS))
    check("凹角的唯一外部象限是 (1,0,0)",
          vi[CONCAVE_QUADRANTS.index((1, 0, 0))] == 0, str(CONCAVE_QUADRANTS))

    print("[3] §3.2.2 最小维度 n=3 的可执行复现")
    pats = smooth_patterns()
    check("平滑通道的象限模式共 6 种（全0/全1 + 4 个半平面）", len(pats) == 6,
          str([format(p, '04b') for p in pats]))
    convex_target = 0b0001      # 只有一个象限在内部
    concave_target = 0b1110     # 三个象限在内部
    n_cvx = min_dimension_for_corner(convex_target)
    n_ccv = min_dimension_for_corner(concave_target)
    check("凸角：n=1 不可表达、n=2 可表达（两个半平面求交）",
          not can_express(convex_target, 1, pats) and n_cvx == 2,
          "min n(凸)=%d" % n_cvx)
    check("凹角：n=1、n=2 都不可表达，n=3 才可行",
          not can_express(concave_target, 1, pats)
          and not can_express(concave_target, 2, pats) and n_ccv == 3,
          "min n(凹)=%d" % n_ccv)
    check("两种角都要能表达 → 最小维度 n = max(2, 3) = 3（与论文结论一致）",
          max(n_cvx, n_ccv) == 3, "")

    print("[4] §2.5 伪距离：抹掉最近点距离里的脊线")
    # 边 AB = (0,0)→(1,0)；点 (0.5, 3) 的投影落在边内 → 两者相等
    check("投影落在边内时伪距离 == 真实距离",
          abs(pseudo_distance(0.5, 3.0, 0.0, 0.0, 1.0, 0.0)
              - true_distance_to_segment(0.5, 3.0, 0.0, 0.0, 1.0, 0.0)) < 1e-12, "")
    # 点 (5, 3) 的投影在延长线上 → 真实距离取到端点，伪距离仍沿直线量
    outside = pseudo_distance(5.0, 3.0, 0.0, 0.0, 1.0, 0.0)
    true_d = true_distance_to_segment(5.0, 3.0, 0.0, 0.0, 1.0, 0.0)
    check("投影落在延长线上时两者不再相等（伪距离 = 3，真实 = 到端点 (1,0) 的 5）",
          abs(outside - 3.0) < 1e-12 and abs(true_d - 5.0) < 1e-12,
          "pseudo=%.3f true=%.3f" % (outside, true_d))
    check("伪距离沿边方向是线性的（故双线性插值精确）",
          all(abs(pseudo_distance(t, 1.0, 0.0, 0.0, 1.0, 0.0)
                  - (pseudo_distance(0.0, 1.0, 0.0, 0.0, 1.0, 0.0) * (1 - t)
                     + pseudo_distance(1.0, 1.0, 0.0, 0.0, 1.0, 0.0) * t)) < 1e-12
              for t in (0.0, 0.25, 0.5, 0.75, 1.0)), "")

    print("[5] §1.1.3 单通道 SDF 在角点处的非线性")
    check("第三象限（x<0,y<0）走 −√(x²+y²)：取两点平均 ≠ 中点值",
          abs((corner_sdf(-2, 0) + corner_sdf(0, -2)) / 2 - corner_sdf(-1, -1)) > 0.1,
          "avg=%.3f mid=%.3f" % ((corner_sdf(-2, 0) + corner_sdf(0, -2)) / 2,
                                 corner_sdf(-1, -1)))
    check("MSDF 两通道都是线性函数：取平均 == 中点值",
          all(abs((corner_msdf(-2, 0)[k] + corner_msdf(0, -2)[k]) / 2
                  - corner_msdf(-1, -1)[k]) < 1e-12 for k in range(3)), "")

    print("[6] 重建质量：粗网格 8× 放大后与真值比对（域 [−4,4]²，输出 65²）")
    lo, hi, out_n = -4.0, 4.0, 65
    rows = []
    for n in (5, 9, 17, 33):
        sdf_err, sdf_dev = reconstruct_errors(
            Field(n, lo, hi, corner_sdf), lo, hi, out_n, "sdf")
        msdf_err, msdf_dev = reconstruct_errors(
            Field(n, lo, hi, corner_msdf), lo, hi, out_n, "msdf")
        rows.append((n, sdf_err, sdf_dev, msdf_err, msdf_dev))
        print("    网格 %2d²  SDF 错 %4d 像素 (最大偏差 %.3f) | MSDF 错 %d 像素 (%.3f)"
              % (n, sdf_err, sdf_dev, msdf_err, msdf_dev))
    check("MSDF 在任意网格分辨率下都零误差（线性场 + 双线性 = 精确）",
          all(r[3] == 0 for r in rows), str([r[3] for r in rows]))
    check("SDF 在粗网格下必然有错像素（尖角被抹圆）",
          all(r[1] > 0 for r in rows), str([r[1] for r in rows]))
    check("SDF 误差随网格加密而下降（收敛到真值，但需要分辨率换质量）",
          rows[0][1] >= rows[-1][1] and rows[-1][1] < rows[0][1],
          "%d → %d" % (rows[0][1], rows[-1][1]))
    check("5² 网格下 MSDF 相对 SDF 的错像素比为 0 : %d" % rows[0][1],
          rows[0][3] == 0 and rows[0][1] > 0, "")

    print("[7] 端到端：median 重建 == 两条半平面的交（尖角不丢）")
    bad = []
    for r in range(out_n):
        y = lo + r * (hi - lo) / (out_n - 1)
        for c in range(out_n):
            x = lo + c * (hi - lo) / (out_n - 1)
            if inside_from_msdf(corner_msdf(x, y)) != inside_truth(x, y):
                bad.append((x, y))
    check("连续域上 median 判定与真值完全一致（0 处不符）", not bad, str(bad[:3]))
    sdf_bad = 0
    grid = Field(9, lo, hi, corner_sdf)
    for r in range(out_n):
        y = lo + r * (hi - lo) / (out_n - 1)
        for c in range(out_n):
            x = lo + c * (hi - lo) / (out_n - 1)
            if inside_from_sdf(grid.bilinear(x, y)) != inside_truth(x, y):
                sdf_bad += 1
    check("同一输出分辨率下 SDF（9² 网格）有 %d 处不符" % sdf_bad, sdf_bad > 0, "")

    print("\n全部 %d 项断言通过" % _CHECKS)


if __name__ == "__main__":
    main()
