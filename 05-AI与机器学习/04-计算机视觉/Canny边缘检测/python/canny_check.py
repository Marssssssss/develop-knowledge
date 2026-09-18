# -*- coding: utf-8 -*-
"""Canny 边缘检测自检。断言的是「结构性与解析性结论」,不是某张图的观感。

覆盖:核的正确性/可分离性 → 四个阶段的判据 → 量化边界 → 平局处理 → 三档阈值行为 →
L1/L2 幅值关系 → 已知坑(阈值 0 全图成边)。
"""
import math

import canny as C

TOTAL = [0, 0]
FAILS = []


def check(name, cond, detail=""):
    TOTAL[0] += 1
    if cond:
        TOTAL[1] += 1
        print(f"  [PASS] {name}  {detail}")
    else:
        FAILS.append(name)
        print(f"  [FAIL] {name}  {detail}")


def step_image(h=9, w=9, x0=3, lo=0.0, hi=200.0):
    """竖直阶跃:第 x0 列起为高值。"""
    return [[hi if x >= x0 else lo for x in range(w)] for _ in range(h)]


def diag_image(n=16, lo=0.0, hi=200.0):
    """45 度阶跃:x+y >= n 为高值。"""
    return [[hi if x + y >= n else lo for x in range(n)] for y in range(n)]


def mag_grid(entries, n=7):
    """按 {(x,y): value} 造一幅"梯度幅值图"(直接送进 hysteresis,跳过前 3 阶段)。"""
    g = [[0.0] * n for _ in range(n)]
    for (x, y), v in entries.items():
        g[y][x] = v
    return g


def main():
    print("== A. 高斯核:整数核的出处与归一化 ==")
    s = sum(sum(r) for r in C.GAUSSIAN_5X5_INT)
    check("教程给的 5x5 整数核总和 == 159(即除数)", s == 159, f"sum={s}")
    check("整数核左右/上下对称", all(r == tuple(reversed(r)) for r in C.GAUSSIAN_5X5_INT)
          and C.GAUSSIAN_5X5_INT[0] == C.GAUSSIAN_5X5_INT[4], "镜像对称")

    k14 = C.gaussian_kernel_2d(5, 1.4)
    check("连续高斯核归一化(sum==1)", abs(sum(sum(r) for r in k14) - 1.0) < 1e-15,
          f"{sum(sum(r) for r in k14):.16f}")
    worst = 0.0
    for i in range(5):
        for j in range(5):
            ref = C.GAUSSIAN_5X5_INT[i][j] / 159.0
            worst = max(worst, abs(k14[i][j] - ref) / ref)
    check("1/159 整数核是 sigma≈1.4 高斯的整数近似", worst < 0.09, f"最大相对偏差 {worst:.4f}")
    g1 = [math.exp(-(i * i) / (2 * 1.4 * 1.4)) for i in (-2, -1, 0, 1, 2)]
    tot = sum(g1)
    g1 = [v / tot for v in g1]
    sep = max(abs(k14[i][j] - g1[i] * g1[j]) for i in range(5) for j in range(5))
    check("2D 核可分离(等于 1D 核外积)", sep < 1e-15, f"max diff {sep:.2e}")
    try:
        C.gaussian_kernel_2d(4, 1.4)
        check("偶数尺寸核被拒绝", False, "未抛异常")
    except ValueError as exc:
        check("偶数尺寸核被拒绝", True, str(exc))

    print("\n== B. Sobel 梯度与幅值 ==")
    step = step_image()
    gx, gy = C.sobel_gradients(step)
    nz_gy = max(abs(v) for row in gy for v in row)
    check("竖直阶跃上 Gy 恒为 0", nz_gy == 0.0, f"max|Gy|={nz_gy}")
    check("阶跃两侧的 |Gx| 相等(4*(high-low)=800)", abs(gx[4][3]) == 800.0 and abs(gx[4][2]) == 800.0,
          f"|Gx(2)|={abs(gx[4][2]):.1f} |Gx(3)|={abs(gx[4][3]):.1f}")
    check("远离阶跃处 |Gx| 衰减(平滑所致)", abs(gx[4][8]) < 1.0, f"|Gx(8)|={abs(gx[4][8]):.3f}")

    d = diag_image(16)
    dx, dy = C.sobel_gradients(d)
    onedge = [(x, y) for x in range(1, 15) for y in range(1, 15) if x + y == 15]
    eq = [(x, y) for x, y in onedge if abs(abs(dx[y][x]) - abs(dy[y][x])) < 1e-12]
    check("45 度阶跃上 |Gx| == |Gy|(方向恰为 45)", len(eq) == len(onedge) and len(onedge) > 10,
          f"{len(eq)}/{len(onedge)} 个点相等")
    l1 = C.gradient_magnitude(dx, dy, l2gradient=False)
    l2 = C.gradient_magnitude(dx, dy, l2gradient=True)
    check("L2 幅值恒 <= L1 幅值(sqrt 不等式)", all(l2[y][x] <= l1[y][x] + 1e-9
                                                 for y in range(16) for x in range(16)), "")
    x, y = onedge[0]
    check("45 度处 L1/L2 == sqrt(2)", abs(l1[y][x] / l2[y][x] - math.sqrt(2.0)) < 1e-12,
          f"{l1[y][x]:.4f}/{l2[y][x]:.4f}={l1[y][x]/l2[y][x]:.12f}")
    check("L2gradient=False 是 OpenCV 的默认(Sobel 教程式)", C.gradient_magnitude.__defaults__ == (False,),
          str(C.gradient_magnitude.__defaults__))
    check("aperture != 3 被拒绝", _raises(lambda: C.sobel_gradients(step, aperture=5)), "")

    print("\n== C. 梯度方向量化到 0/45/90/135 ==")
    for ang, want in ((0.0, 0), (22.4, 0), (22.5, 45), (67.4, 45), (67.5, 90), (112.4, 90),
                      (112.5, 135), (157.4, 135), (157.5, 0), (179.9, 0)):
        check(f"角度 {ang} -> {want}", C.direction_sector(ang) == want, f"got {C.direction_sector(ang)}")
    check("(1,0) 方向为 0 度", C.direction_sector(C.gradient_angle(1.0, 0.0)) == 0, "")
    check("(1,1) 方向为 45 度", C.direction_sector(C.gradient_angle(1.0, 1.0)) == 45, "")
    check("(0,1) 方向为 90 度", C.direction_sector(C.gradient_angle(0.0, 1.0)) == 90, "")
    check("(-1,1) 方向为 135 度", C.direction_sector(C.gradient_angle(-1.0, 1.0)) == 135, "")
    check("(1,-1) 与 (-1,1) 同向(方向+180 归一)", C.direction_sector(C.gradient_angle(1.0, -1.0)) == 135, "")

    print("\n== D. 非极大值抑制:阶跃边缘必须收成 1 像素宽 ==")
    _, gy0 = C.sobel_gradients(C.blur(step, 5, 1.4))
    gx0, _ = C.sobel_gradients(C.blur(step, 5, 1.4))
    mag = C.gradient_magnitude(gx0, gy0)
    sectors = [[C.direction_sector(C.gradient_angle(gx0[y][x], gy0[y][x])) for x in range(9)]
               for y in range(9)]
    thin = C.non_max_suppression(mag, sectors)
    per_row = [sum(1 for x in range(9) if thin[y][x] > 0) for y in range(9)]
    cols = sorted({x for y in range(9) for x in range(9) if thin[y][x] > 0})
    check("阶跃的 NMS 结果每行恰 1 个边缘像素", per_row == [1] * 9, f"{per_row}")
    check("存活列落在两个等高峰之一", cols in ([2], [3]), f"存活列 {cols}")
    raw = C.non_max_suppression(mag, sectors, eps_ratio=0.0)
    ghost = [x for x in range(9) if raw[8][x] > 0]
    check("坑:关掉残差护栏后平坦区出现伪边(浮点残差 1e-14 级)", len(ghost) > 1,
          f"第 8 行伪边列 {ghost},幅值 {mag[8][ghost[-1]]:.3e}")
    m2, m3 = mag[4][2], mag[4][3]
    check("两个候选峰严格相等(对称阶跃 -> 数学平局)", abs(m2 - m3) < 1e-9,
          f"|G|(2)={m2:.4f} |G|(3)={m3:.4f}")
    asym = [[0.0] * 9 for _ in range(9)]
    for y in range(9):
        for x in range(9):
            asym[y][x] = 200.0 if x >= 4 else (60.0 if x == 3 else 0.0)
    ax, ay = C.sobel_gradients(C.blur(asym, 5, 1.4))
    amag = C.gradient_magnitude(ax, ay)
    asec = [[C.direction_sector(C.gradient_angle(ax[y][x], ay[y][x])) for x in range(9)]
            for y in range(9)]
    athin = C.non_max_suppression(amag, asec)
    acols = sorted({x for y in range(9) for x in range(9) if athin[y][x] > 0})
    amax = max(range(9), key=lambda x: amag[4][x])
    check("非对称阶跃上唯一极大点被保留(确定性)", acols == [amax], f"argmax={amax} 存活={acols}")
    check("NMS 只输出原幅值(不放大不插值)",
          all(athin[y][x] == 0.0 or athin[y][x] == amag[y][x] for y in range(9) for x in range(9)), "")

    print("\n== E. 双阈值 + 滞后(OpenCV 教程的三个场景) ==")
    grid = mag_grid({(1, 1): 100.0, (2, 1): 40.0, (3, 1): 40.0, (4, 1): 40.0,   # 与强边连通的弱边
                     (6, 6): 40.0,                                            # 孤立弱边(教程里的 B)
                     (1, 5): 30.0, (5, 1): 10.0})                             # 另一个孤立弱边 + 低于 low
    edges = C.hysteresis(grid, low=20.0, high=60.0)
    keep = sorted((x, y) for y in range(7) for x in range(7) if edges[y][x])
    check("强边 + 与其连通的弱边被保留", keep == [(1, 1), (2, 1), (3, 1), (4, 1)], f"{keep}")
    check("孤立弱边(> low 但不连强边)被丢弃", edges[6][6] == 0, "教程里的 edge B")
    check("低于 low 的弱边被丢弃", edges[1][5] == 0 and edges[5][1] == 0, "")
    no_seed = C.hysteresis(mag_grid({(1, 1): 40.0, (2, 1): 40.0}), low=20.0, high=60.0)
    check("没有强边种子时全图无边缘", all(v == 0 for row in no_seed for v in row), "")
    e_low = C.hysteresis(grid, low=20.0, high=50.0)
    sub = all(e_low[y][x] >= edges[y][x] for y in range(7) for x in range(7))
    check("提高 high 只会减少边缘(单调)", sub, "edges(high=60) 是 edges(high=50) 的子集")
    check("low > high 被拒绝", _raises(lambda: C.hysteresis(grid, 60.0, 20.0)), "")

    print("\n== F. 整条流水线 ==")
    e = C.canny(step, low=100.0, high=300.0)
    check("阶跃图 > high(=300) 时边缘恰为 1 列 x 9 行", sum(sum(r) for r in e) == 9, f"{sum(sum(r) for r in e)}")
    e_hi = C.canny(step, low=100.0, high=500.0)
    check("high 抬到 800 之上后无边缘(弱边无处连通)", sum(sum(r) for r in e_hi) == 0, "")
    flat = [[128.0] * 8 for _ in range(8)]
    check("常量图无边缘", sum(sum(r) for r in C.canny(flat, 10.0, 30.0)) == 0, "")
    deg = C.canny(flat, 0.0, 0.0)
    check("坑:阈值取 0 会把常量图整幅判成边缘(判据是 >=)", sum(sum(r) for r in deg) == 64,
          f"{sum(sum(r) for r in deg)}/64")
    lo, hi = C.auto_thresholds([[float(v)] for v in range(100)])
    check("中位数启发式 low/high 公式", abs(lo - 0.67 * 49.5) < 1e-9 and abs(hi - 1.33 * 49.5) < 1e-9,
          f"low={lo:.4f} high={hi:.4f}")
    check("OpenCV 教程的 ratio=3 落在 Canny 建议的 2:1~3:1 内", 2.0 <= 3.0 <= 3.0, "high = low*3")

    print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
    return 0 if not FAILS else 1


def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
