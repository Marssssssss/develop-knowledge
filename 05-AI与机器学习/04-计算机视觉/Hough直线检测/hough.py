# -*- coding: utf-8 -*-
"""Hough 变换直线检测 — 极坐标参数空间投票的最小实现(纯标准库)。

依据 OpenCV 官方教程(Hough Line Transform):
- 直线极坐标表示 ρ = x·cosθ + y·sinθ,避开 y=mx+b 垂直线斜率无穷大问题
- 每个边缘点对所有 θ 投一票 (ρ,θ);同一条直线上的点在参数空间
  汇聚成峰 → 在累加器中找峰值即得直线
- ρ 精度 1 像素、θ 精度 1 度;ρ 最大值 = 图像对角线长度

4 个 demo:
1. 水平直线 → (ρ=y0, θ=90°)
2. 对角线 y=x → (ρ=0, θ=135°)
3. 方块轮廓 → 4 个峰(两条 θ=0° + 两条 θ=90°)
4. 断线(有缺口)仍可检出 — Hough 对部分缺失鲁棒
"""
import math

THETA_NUM = 180          # θ 分辨率 1°,覆盖 [0°, 180°)
COS = [math.cos(math.radians(t)) for t in range(THETA_NUM)]
SIN = [math.sin(math.radians(t)) for t in range(THETA_NUM)]


def hough_accumulate(points, width, height):
    """对每个边缘点沿 θ 扫描投票,返回 (累加器, rho_offset)。

    acc[ri][ti] = 票数;ri = round(ρ) + rho_offset,使负 ρ 也有下标。
    """
    diag = int(math.hypot(width, height)) + 1
    rho_offset = diag                       # ρ ∈ [-diag, diag]
    acc = [[0] * THETA_NUM for _ in range(2 * diag + 1)]
    for (x, y) in points:
        for t in range(THETA_NUM):
            r = x * COS[t] + y * SIN[t]     # 经过 (x,y) 的所有直线构成正弦曲线
            ri = int(round(r)) + rho_offset
            if 0 <= ri < len(acc):
                acc[ri][t] += 1
    return acc, rho_offset


def find_peaks(acc, rho_offset, threshold, top=8):
    """提取票数 >= threshold 的 (ρ,θ,票数) 峰,参数空间内做简单 NMS。

    同一条真实直线会在相邻 (ρ,θ) 格子里形成一片高票区,需抑制邻域:
    按票数降序接受,已接受峰的 ±2ρ / ±2θ 邻域内不再接受新峰。
    """
    cells = []
    for ri, row in enumerate(acc):
        for ti, votes in enumerate(row):
            if votes >= threshold:
                cells.append((votes, ri, ti))
    cells.sort(reverse=True)
    peaks = []
    for votes, ri, ti in cells:
        if all(abs(ri - p[0]) > 2 or abs(ti - p[1]) > 2 for p in peaks):
            peaks.append((ri, ti, votes))
        if len(peaks) >= top:
            break
    return [(ri - rho_offset, ti, votes) for ri, ti, votes in peaks]


def hough_lines(points, width, height, threshold):
    """标准 Hough 直线检测主入口,返回 [(ρ, θ°, 票数)]。"""
    acc, off = hough_accumulate(points, width, height)
    return find_peaks(acc, off, threshold)


def describe(rho, theta_deg):
    """把 (ρ,θ) 翻译成人话:垂直/水平/斜率。"""
    if theta_deg == 0:
        return "垂直线 x=%d" % rho
    if theta_deg == 90:
        return "水平线 y=%d" % rho
    # ρ = x·cosθ + y·sinθ → y = (ρ - x·cosθ)/sinθ
    k = -math.cos(math.radians(theta_deg)) / math.sin(math.radians(theta_deg))
    b = rho / math.sin(math.radians(theta_deg))
    return "斜线 y=%.2f·x%+.2f" % (k, b)


def render_points(points, width, height):
    grid = [["." for _ in range(width)] for _ in range(height)]
    for (x, y) in points:
        if 0 <= x < width and 0 <= y < height:
            grid[y][x] = "#"
    return "\n".join("".join(r) for r in grid)


def demo1_horizontal():
    print("=== demo 1: 水平直线 y=6 → (ρ=6, θ=90°) ===")
    w, h = 12, 12
    pts = [(x, 6) for x in range(w)]
    print(render_points(pts, w, h), "<- 边缘点(OpenCV 教程 100x100 水平线例子的缩影)")
    peaks = hough_lines(pts, w, h, threshold=w // 2)
    print("峰值:", [(r, t, v) for r, t, v in peaks])
    best = peaks[0]
    assert best[0] == 6 and best[1] == 90, "应为 (ρ=6, θ=90°)"
    print("PASS:", describe(*best[:2]), "得票 %d/%d\n" % (best[2], w))


def demo2_diagonal():
    print("=== demo 2: 对角线 y=x → (ρ=0, θ=135°) ===")
    w, h = 12, 12
    pts = [(i, i) for i in range(w)]
    print(render_points(pts, w, h))
    peaks = hough_lines(pts, w, h, threshold=w // 2)
    best = peaks[0]
    print("峰值:", [(r, t, v) for r, t, v in peaks])
    # ρ = i·(cos135+sin135) = 0,恒过原点
    assert best[0] == 0 and best[1] == 135, "对角线应为 (ρ=0, θ=135°)"
    print("PASS:", describe(*best[:2]), "\n")


def demo3_square():
    print("=== demo 3: 方块轮廓 → 4 条边各自成峰 ===")
    w, h = 12, 12
    x0, x1, y0, y1 = 3, 9, 2, 10
    pts = set()
    for x in range(x0, x1 + 1):            # 上下两边(水平)
        pts.add((x, y0))
        pts.add((x, y1))
    for y in range(y0, y1 + 1):            # 左右两边(垂直)
        pts.add((x0, y))
        pts.add((x1, y))
    pts = sorted(pts)
    print(render_points(pts, w, h))
    peaks = hough_lines(pts, w, h, threshold=5, top=4)
    print("峰值:", [(r, t, v) for r, t, v in peaks])
    lines = {(r, t) for r, t, _ in peaks}
    for expect in [(x0, 0), (x1, 0), (y0, 90), (y1, 90)]:
        assert expect in lines, "缺少 %s" % (expect,)
    print("PASS: 两条垂直(θ=0,ρ=%d/%d) + 两条水平(θ=90,ρ=%d/%d)\n"
          % (x0, x1, y0, y1))


def demo4_broken_line():
    print("=== demo 4: 断线仍可检出(Hough 对缺失鲁棒) ===")
    w, h = 14, 10
    full = [(x, 5) for x in range(w)]
    broken = [p for p in full if not (4 <= p[0] <= 7)]   # 中间挖掉 4 个点
    print(render_points(broken, w, h), "<- 中段缺失 4 点")
    # 缺口导致总票数下降,阈值须低于完好直线;这正是 threshold 参数的意义:
    # 它代表"认定一条直线所需的最少共线点数"(即最小长度)
    peaks = hough_lines(broken, w, h, threshold=5)
    best = peaks[0]
    print("峰值:", [(r, t, v) for r, t, v in peaks])
    assert best[0] == 5 and best[1] == 90, "断线仍应收敛到 (ρ=5, θ=90°)"
    print("PASS:", describe(*best[:2]), "得票 %d(=剩余点数)\n" % best[2])


def main():
    demo1_horizontal()
    demo2_diagonal()
    demo3_square()
    demo4_broken_line()
    print("all 4 demos PASS")


if __name__ == "__main__":
    main()
