# -*- coding: utf-8 -*-
"""Harris 角点检测自检。断言的解析结论优先于图像观感。

覆盖:结构张量的闭式特征值 → R<0 的解析边界与 k 的作用 → 角点/边缘/平坦三分 →
Shi-Tomasi 的判据差异 → 响应对对比度的 4 次方缩放 → blockSize/窗函数 → 响应平台与簇合并 →
旋转不变/尺度不敏感 → 亚像素精化误差。
"""
import math

import harris as H

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


def checkerboard(n=33, v=200.0):
    """四象限棋盘:中心像素 (n//2, n//2) 是角点,内边界是边,象限内部是平坦区。"""
    c = n // 2
    return [[(v if ((x >= c) ^ (y >= c)) else 0.0) for x in range(n)] for y in range(n)]


def l_corner(n=16, px=8, v=200.0):
    """左上方 1 像素宽的 L 形亮角。"""
    g = [[0.0] * n for _ in range(n)]
    for k in range(3):
        g[px][px + k] = v
        g[px + k][px] = v
    return g


def upscale(m, f):
    n = len(m) * f
    return [[m[y // f][x // f] for x in range(n)] for y in range(n)]


def rot90(m):
    """逆时针 90 度:new[i][j] = old[n-1-j][i],即 old(x,y) 落到 new[y][n-1-x]。"""
    return [list(r) for r in zip(*m[::-1])]


def r_max_for_k(k):
    """R > 0 的解析上界:r < (1-2k+sqrt(1-4k)) / (2k),r = λ1/λ2 >= 1。"""
    return (1.0 - 2.0 * k + math.sqrt(1.0 - 4.0 * k)) / (2.0 * k)


def main():
    print("== A. 结构张量的闭式响应 ==")
    cases = [((4.0, 0.0, 4.0), 13.44, "λ=(4,4) 各向同性 -> 角点"),
             ((4.0, 0.0, 1.0), 3.0, "λ=(4,1) det=4 tr=5 -> R=4-0.04*25"),
             ((100.0, 0.0, 1.0), -308.04, "λ=(100,1) 强边缘 -> R<0")]
    for (a, b, c), want, note in cases:
        got = H.response_from_tensor(a, b, c, 0.04)
        check(f"R({note})", abs(got - want) < 1e-9, f"{got:.4f} vs {want}")
    l1, l2 = H.eigenvalues(4.0, 0.0, 1.0)
    check("特征值闭式解", abs(l1 - 4.0) < 1e-12 and abs(l2 - 1.0) < 1e-12, f"({l1},{l2})")
    check("λ1>=λ2 且 tr/det 一致", l1 >= l2 and abs(l1 + l2 - 5.0) < 1e-12
          and abs(l1 * l2 - 4.0) < 1e-12, "")
    c, s = math.cos(0.7), math.sin(0.7)
    ra = c * c * 4.0 + s * s * 1.0
    rb = 2 * c * s * (4.0 - 1.0) / 2.0 * 2.0 / 2.0 + (c * c - s * s) * 0.0
    rcc = s * s * 4.0 + c * c * 1.0
    ra, rb, rcc = round(ra, 12), round(rb, 12), round(rcc, 12)
    l1r, l2r = H.eigenvalues(ra, rb, rcc)
    check("M 旋转后特征值不变(旋转不变性)", abs(l1r - l1) < 1e-9 and abs(l2r - l2) < 1e-9,
          f"({l1r:.9f},{l2r:.9f})")

    print("\n== B. R<0 的解析边界与 k 的作用 ==")
    r04 = r_max_for_k(0.04)
    check("k=0.04 的解析上界 r_max", abs(r04 - 22.956439) < 1e-5, f"{r04:.6f}")
    check("r=22 < r_max -> R>0", H.response_from_tensor(22.0, 0.0, 1.0, 0.04) > 0,
          f"{H.response_from_tensor(22.0, 0.0, 1.0, 0.04):.4f}")
    check("r=23 > r_max -> R<0", H.response_from_tensor(23.0, 0.0, 1.0, 0.04) < 0,
          f"{H.response_from_tensor(23.0, 0.0, 1.0, 0.04):.4f}")
    r02, r06 = r_max_for_k(0.02), r_max_for_k(0.06)
    check("k 越大对边缘抑制越强(r_max 单调下降)", r06 < r04 < r02,
          f"k=0.02:{r02:.4f} k=0.04:{r04:.4f} k=0.06:{r06:.4f}")
    check("r=20 的结构:k=0.04 判为角点、k=0.06 判为边缘(跨过 r_max)",
          H.response_from_tensor(20.0, 0.0, 1.0, 0.04) > 0 > H.response_from_tensor(20.0, 0.0, 1.0, 0.06),
          f"{H.response_from_tensor(20.0, 0.0, 1.0, 0.04):.3f} vs {H.response_from_tensor(20.0, 0.0, 1.0, 0.06):.3f}")
    r1 = [H.response_from_tensor(1.0, 0.0, 1.0, k) for k in (0.02, 0.04, 0.06)]
    check("各向同性角点 R=λ²(1-4k) 随 k 单调下降", r1[0] > r1[1] > r1[2], f"{r1}")

    print("\n== C. 角点 / 边缘 / 平坦三分 ==")
    img = checkerboard()
    resp = H.response_image(img, 2, 3, 0.04)
    sxx, sxy, syy = H.structure_tensor(*H.sobel_gradients(img), 2)
    rc, re, rf = resp[16][16], resp[8][16], resp[4][4]
    check("角点响应 > 0", rc > 0, f"{rc:.4g}")
    check("边缘响应 < 0", re < 0, f"{re:.4g}")
    check("平坦区响应 == 0", rf == 0.0, f"{rf}")
    lc = H.eigenvalues(sxx[16][16], sxy[16][16], syy[16][16])
    le = H.eigenvalues(sxx[8][16], sxy[8][16], syy[8][16])
    check("理想棋盘角点 λ1==λ2(比值为 1)", abs(lc[0] / lc[1] - 1.0) < 1e-9, f"ratio={lc[0]/lc[1]:.12f}")
    check("边缘的 λ2 == 0(结构张量退化成秩 1)", abs(le[1]) < 1e-9, f"λ2={le[1]:.3e}")
    n = len(img)
    psd = min(sxx[y][x] * syy[y][x] - sxy[y][x] ** 2 for y in range(n) for x in range(n))
    check("结构张量半正定 (Cauchy-Schwarz: Sxx*Syy-Sxy² >= 0)", psd >= 0.0, f"min det={psd}")
    lmin = min(H.eigenvalues(sxx[y][x], sxy[y][x], syy[y][x])[1] for y in range(n) for x in range(n))
    check("λmin >= 0 全图成立", lmin >= 0.0, f"min λ2={lmin:.3e}")

    print("\n== D. Shi-Tomasi(λmin)与 Harris 判据并不等价 ==")
    st = H.response_image(img, 2, 3, 0.04, method="shi_tomasi")
    check("λ=(100,1):Harris R<0 而 λmin=1>0", H.response_from_tensor(100.0, 0.0, 1.0, 0.04) < 0
          and H.response_from_tensor(100.0, 0.0, 1.0, 0.04, method="shi_tomasi") > 0,
          f"R={H.response_from_tensor(100.0, 0.0, 1.0, 0.04):.3f} λmin=1.0")
    check("理想边缘上 λmin == 0", st[8][16] == 0.0, f"{st[8][16]}")
    check("角点上 λmin == λ(=179200)", abs(st[16][16] - lc[1]) < 1e-6, f"{st[16][16]:.1f}")
    check("λmin 全图 >= 0(永不产生负响应,故必须显式给阈值而非判 >0)",
          min(min(r) for r in st) >= 0.0, f"min={min(min(r) for r in st):.3e}")

    print("\n== E. 响应尺度:随对比度的 4 次方缩放 ==")
    img2 = [[v * 2.0 for v in row] for row in img]
    resp2 = H.response_image(img2, 2, 3, 0.04)
    ratio = resp2[16][16] / resp[16][16]
    check("对比度 x2 -> 响应 x16(det 与 tr² 都乘 c⁴)", abs(ratio - 16.0) < 1e-9, f"ratio={ratio:.12f}")
    n1 = len(H.cluster_candidates(resp, 0.01))
    n2 = len(H.cluster_candidates(resp2, 0.01))
    check("相对阈值 0.01*max 在缩放后给出同样的角点集合", n1 == n2, f"{n1} vs {n2}")
    t_fixed = 1.2 * resp[16][16]
    s1 = sum(1 for y in range(33) for x in range(33) if resp[y][x] > t_fixed)
    s2 = sum(1 for y in range(33) for x in range(33) if resp2[y][x] > t_fixed)
    check("固定绝对阈值不可移植(高 1.2 倍后原图零检出、亮图 36 个)", s1 == 0 < s2,
          f"{s1} vs {s2} 个像素")

    print("\n== F. blockSize 与窗函数 ==")
    rs = [H.response_image(img, b, 3, 0.04)[16][16] for b in (1, 2, 5, 8)]
    check("归一化权重下 blockSize 越大响应越小", rs[0] > rs[1] > rs[2] > rs[3],
          " > ".join(f"{v:.3e}" for v in rs))
    for mode in ("rect", "gaussian"):
        tot = sum(sum(r) for r in H.window_weights(3, mode))
        check(f"{mode} 窗权重归一化", abs(tot - 1.0) < 1e-12, f"sum={tot:.16f}")
    rg = H.response_image(img, 2, 3, 0.04, mode="gaussian")
    check("两种窗都在同一点判出角点、同一条边判出边缘",
          rg[16][16] > 0 and rg[8][16] < 0, f"角点 {rg[16][16]:.4g} 边缘 {rg[8][16]:.4g}")
    check("非法窗函数被拒绝", _raises(lambda: H.response_image(img, 2, 3, 0.04, mode="hann")), "")
    check("ksize != 3 被拒绝", _raises(lambda: H.response_image(img, 2, 5, 0.04)), "")

    print("\n== G. 响应平台与簇合并 ==")
    naive = H.find_corners(resp, 0.01, 1)
    check("朴素 NMS 在理想角点上留下 16 个候选(4x4 平台)", len(naive) == 16, f"{len(naive)} 个")
    cl = H.cluster_candidates(resp, 0.01)
    check("8 连通合并后只剩 1 个簇", len(cl) == 1, f"{len(cl)} 个")
    check("0.01*max 阈值下该簇含 36 个像素(4x4 平台 + 一圈 1.57e10)", cl[0][3] == 36, f"{cl[0][3]} 像素")
    cl99 = H.cluster_candidates(resp, 0.99)
    check("阈值抬到 0.99*max 后只剩 4x4=16 的平台像素", cl99[0][3] == 16, f"{cl99[0][3]} 像素")
    cx, cy = cl[0][0], cl[0][1]
    check("簇质心落在 (15.5,15.5),比边界像素 (16,16) 偏半像素", abs(cx - 15.5) < 1e-12
          and abs(cy - 15.5) < 1e-12, f"({cx},{cy})")
    clg = H.cluster_candidates(rg, 0.01)
    check("高斯窗把平台收得更紧(平台像素数下降)", clg[0][3] < cl[0][3], f"{clg[0][3]} < {cl[0][3]}")

    print("\n== H. 旋转不变、尺度不敏感 ==")
    lcimg = l_corner()
    rl = H.response_image(lcimg, 2, 3, 0.04)
    n = len(lcimg)
    rr = H.response_image(rot90(lcimg), 2, 3, 0.04)
    worst = max(abs(rr[y][n - 1 - x] - rl[y][x]) for y in range(n) for x in range(n))
    check("90 度旋转后响应逐像素一致(旋转不变)", worst < 1e-9, f"max diff {worst:.3e}")
    check("L 角上恰 1 个角点", len(H.cluster_candidates(rl, 0.01)) == 1, "")
    up = H.response_image(upscale(lcimg, 4), 2, 3, 0.04)
    cu = H.cluster_candidates(up, 0.01)
    check("同 blockSize 下 4x 放大后角点数量变化(尺度不敏感)", len(cu) != 1, f"{len(cu)} 个")
    ux, uy = int(cu[0][0]), int(cu[0][1])
    check("放大后的响应峰不落在放大后的原位置(32,32)", (ux, uy) != (32, 32), f"argmax=({ux},{uy})")

    print("\n== I. 亚像素精化 ==")
    for c in (0.0, 0.1, 0.3, 0.5):
        f = lambda t: -((t - c) ** 2)
        d = H.quadratic_peak_offset(f(-1.0), f(0.0), f(1.0))
        check(f"抛物线真值 {c} 精确恢复", abs(d - c) < 1e-12, f"est={d:.12f}")
    worst = 0.0
    for c in (0.05, 0.1, 0.2, 0.3, 0.4, 0.45):
        f = lambda t: math.exp(-((t - c) ** 2) / 2.0)
        worst = max(worst, abs(H.quadratic_peak_offset(f(-1.0), f(0.0), f(1.0)) - c))
    check("单位 sigma 高斯峰上的偏差 < 0.05(O(h²) 系统偏差)", worst < 0.05, f"max err {worst:.4f}")

    def soft_corner(n=24, center=8.5, v=200.0, s=1.2):
        sig = lambda t: 1.0 / (1.0 + math.exp(-t))
        return [[v * sig((x - center) / s) * sig((y - center) / s) for x in range(n)] for y in range(n)]

    rs2 = H.response_image(soft_corner(), 2, 3, 0.04)
    mx = max((rs2[y][x], x, y) for y in range(24) for x in range(24))
    fx, fy = H.subpixel_peak(rs2, mx[1], mx[2])
    check("真图上精化位移 < 0.5 像素", abs(fx - mx[1]) < 0.5 and abs(fy - mx[2]) < 0.5,
          f"({mx[1]},{mx[2]}) -> ({fx:.4f},{fy:.4f})")
    check("边界像素上精化退化为恒等(不越界)", H.subpixel_peak(rs2, 0, 0) == (0.0, 0.0), "")

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
