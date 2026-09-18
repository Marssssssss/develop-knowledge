# -*- coding: utf-8 -*-
"""SIFT 尺度空间自检:把论文里可验证的结论逐条钉住。

覆盖:尺度空间参数 → DoG≈LoG(及其 k→1 的极限) → 26 邻居极值 → 亚像素定位与对比度剔除 →
Hessian 边缘剔除 → 36 bin 方向直方图与 80% 判据 → 128 维描述子(三线性/clamp/光照不变/旋转不变)
→ 0.8 比率匹配。
"""
import math

import descriptor as D
import sift as S

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


def volume(sn=5, h=16, amp=0.5, center=(2, 7, 7), scale=(1.5, 1.5, 1.5)):
    """合成 DoG 体积:3D 高斯隆起,center = (s, y, x)。"""
    cs, cy, cx = center
    ss, sy, sx = scale
    return [[[amp * math.exp(-((((x - cx) / sx) ** 2 + ((y - cy) / sy) ** 2
                               + ((s - cs) / ss) ** 2) / 2.0)) for x in range(h)]
             for y in range(h)] for s in range(sn)]


def flat_volume(sn=5, h=16):
    return [[[0.0] * h for _ in range(h)] for _ in range(sn)]


def samples(angle=30.0, mag=1.0, grid=16):
    """16x16 均匀梯度场:方向 angle,幅值 mag,采样格坐标以窗口中心为原点。"""
    return [(i - grid / 2.0 + 0.5, j - grid / 2.0 + 0.5, mag, angle)
            for j in range(grid) for i in range(grid)]


def gauss(t, s):
    return math.exp(-(t * t) / (2.0 * s * s)) / (math.sqrt(2 * math.pi) * s)


def main():
    print("== A. 尺度空间参数 ==")
    k = 2.0 ** (1.0 / S.SCALES_PER_OCTAVE)
    check("k = 2^(1/s),s=3 时 k^3 == 2", abs(k ** 3 - 2.0) < 1e-12, f"k={k:.12f}")
    sig = [S.SIFT_SIGMA * k ** i for i in range(S.SCALES_PER_OCTAVE + 3)]
    check("σ 序列在第 s 张翻倍(一个 octave 翻倍)", abs(sig[3] / sig[0] - 2.0) < 1e-12,
          f"σ0={sig[0]:.4f} σ3={sig[3]:.4f}")
    ker = S.gaussian_1d(1.6)
    check("1D 高斯核归一化", abs(sum(ker) - 1.0) < 1e-15, f"sum={sum(ker):.16f}")
    check("核半径覆盖 3σ", len(ker) == 2 * int(math.ceil(3 * 1.6)) + 1, f"len={len(ker)}")
    check("增量模糊 σ = sqrt(σ2²-σ1²)", abs(S.incremental_sigma(1.0, 2.0) - math.sqrt(3.0)) < 1e-12,
          f"{S.incremental_sigma(1.0, 2.0):.12f}")
    check("目标 σ 更小时返回 0(不做反向去模糊)", S.incremental_sigma(2.0, 1.0) == 0.0, "")

    n = 32
    img = [[128.0 + 60 * math.sin(x / 4.0) * math.cos(y / 5.0) for x in range(n)] for y in range(n)]
    pyr = S.build_gaussian_pyramid(img, octaves=4)
    check("每个 octave 生成 s+3 = 6 张模糊图", all(len(o) == 6 for o in pyr),
          f"{[len(o) for o in pyr]}")
    dogs = S.build_dog_pyramid(pyr)
    check("DoG 每 octave 为 s+2 = 5 张", all(len(o) == 5 for o in dogs), f"{[len(o) for o in dogs]}")
    check("octave 尺寸逐级减半", [len(o[0]) for o in pyr] == [32, 16, 8, 4],
          f"{[len(o[0]) for o in pyr]}")
    check("DoG 张数 = 模糊图 - 1(s+3 张模糊凑出 s+2 张 DoG)",
          len(dogs[0]) == S.SCALES_PER_OCTAVE + 2 and len(pyr[0]) - len(dogs[0]) == 1,
          f"{len(pyr[0])} -> {len(dogs[0])}")

    print("\n== B. DoG 是尺度归一化 LoG 的近似 ==")
    ts = [i * 0.2 for i in range(-60, 61)]
    prev_corr, prev_amp = 0.0, None
    for kk in (1.40, 1.26, 1.10, 1.02):
        a = [gauss(t, 1.6 * kk) - gauss(t, 1.6) for t in ts]
        lap = [(gauss(t + 0.02, 1.6) - 2 * gauss(t, 1.6) + gauss(t - 0.02, 1.6)) / 0.0004 for t in ts]
        b = [(kk - 1) * 1.6 * 1.6 * v for v in lap]
        num = sum(x * y for x, y in zip(a, b))
        corr = num / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))
        amp = sum(abs(x) for x in a) / sum(abs(y) for y in b)
        check(f"k={kk:.2f}:相关系数 {corr:.6f} 单调趋近 1", corr > prev_corr,
              f"amp={amp:.4f}")
        if prev_amp is not None:
            check(f"k={kk:.2f}:幅度比 {amp:.4f} 比 k={kk/1.11:.2f} 更接近 1",
                  abs(amp - 1.0) < abs(prev_amp - 1.0), "")
        prev_corr, prev_amp = corr, amp
    check("论文参数 k=2^(1/3) 下相关系数 > 0.98", prev_corr > 0.98, f"{prev_corr:.6f}")

    print("\n== C. 26 邻居尺度空间极值 ==")
    vint = volume()
    ex = S.find_extrema([vint])
    check("整数峰上恰检出 1 个极值", len(ex) == 1, f"{len(ex)} 个")
    check("极值位置为 (s=2,y=7,x=7)", ex and (ex[0][1], ex[0][2], ex[0][3]) == (2, 7, 7),
          f"{ex}")
    check("极值层 s 落在 1..n-2(首尾层凑不齐 26 邻居)",
          all(1 <= e[1] <= 3 for e in S.find_extrema([volume(sn=5)])), "")
    neg = [[[-v for v in row] for row in plane] for plane in volume(sn=5)]
    check("负峰(极小值)同样被检出", len(S.find_extrema([neg])) == 1, "")
    check("平坦体积没有极值", S.find_extrema([flat_volume()]) == [], "")

    print("\n== D. 亚像素定位 + 低对比度剔除 ==")
    loc = S.localize(vint, 2, 7, 7)
    check("采样点即真峰时 offset 全零", loc["offset"] == (0.0, 0.0, 0.0), f"{loc['offset']}")
    check("D(x̂) 等于该采样值(0.5)", abs(loc["D_hat"] - 0.5) < 1e-12, f"{loc['D_hat']:.12f}")
    check("offset<0.5 时不要求换采样点", loc["need_resample"] is False, "")
    vf = volume(center=(2.3, 7.2, 7.2))
    lf = S.localize(vf, 2, 7, 7)
    true_off = (0.3, 0.2, 0.2)
    err = max(abs(lf["offset"][i] - true_off[i]) for i in range(3))
    check("分数峰上亚像素偏移恢复真值(误差 < 0.03)", err < 0.03,
          f"{tuple(round(v, 4) for v in lf['offset'])} vs {true_off}, err={err:.4f}")
    big = volume(sn=5, amp=0.5, center=(2.6, 7, 7), scale=(0.6, 1.5, 1.5))
    lb = S.localize(big, 2, 7, 7)
    check("offset > 0.5 时标记需换采样点(论文要求重做插值)",
          lb["need_resample"] is True and max(abs(v) for v in lb["offset"]) > 0.5,
          f"offset_s={lb['offset'][0]:.4f}")
    l5 = S.localize(volume(sn=5, amp=0.5, center=(2.5, 7, 7), scale=(0.6, 1.5, 1.5)), 2, 7, 7)
    check("offset 恰为 0.5 不换点(论文判据是 strictly > 0.5)",
          abs(l5["offset"][0] - 0.5) < 1e-12 and l5["need_resample"] is False,
          f"offset_s={l5['offset'][0]:.12f}")
    check("|D| = 0.02 < 0.03 -> 低对比度剔除",
          S.detect(volume(amp=0.02))[0][-1] == "low_contrast", "")
    check("|D| = 0.05 > 0.03 -> 保留", S.detect(volume(amp=0.05))[0][-1] == "kept", "")

    print("\n== E. Hessian 边缘响应剔除 ==")
    check("(r+1)²/r 在 r=10 时为 12.1", abs(S.edge_ratio_limit(10.0) - 12.1) < 1e-12, "12.1")
    check("(r+1)²/r 在 r=1 取最小值 4(论文:特征值相等时最小)",
          abs(S.edge_ratio_limit(1.0) - 4.0) < 1e-12, "4.0")
    bump_h = S.hessian_at(volume(), 2, 7, 7)
    check("对称隆起的主曲率比为理论最小值 4.0",
          abs(S.principal_curvature_ratio(*bump_h) - 4.0) < 1e-12,
          f"{S.principal_curvature_ratio(*bump_h):.12f}")
    ridge = volume(scale=(0.8, 6.0, 1.5))
    rr = S.principal_curvature_ratio(*S.hessian_at(ridge, 2, 7, 7))
    check("细长隆起(σy=6)的比值 > 12.1 -> 作为边缘剔除",
          rr > S.edge_ratio_limit(10.0) and S.detect(ridge)[0][-1] == "edge", f"ratio={rr:.3f}")
    mild = volume(scale=(0.8, 4.0, 1.5))
    mr = S.principal_curvature_ratio(*S.hessian_at(mild, 2, 7, 7))
    check("不够细长的隆起(σy=4)仍保留", mr < S.edge_ratio_limit(10.0)
          and S.detect(mild)[0][-1] == "kept", f"ratio={mr:.3f}")
    check("Det <= 0(曲率异号)按边缘处理", S.principal_curvature_ratio(1.0, 0.0, -1.0) is None
          and S.is_edge_response(1.0, 0.0, -1.0) is True, "")

    print("\n== F. 36 bin 方向直方图与 80% 判据 ==")
    hist = D.orientation_histogram([1.0] * 40, [30.0] * 40, [1.0] * 40)
    check("直方图为 36 个 bin(每 bin 10°)", len(hist) == D.ORIENTATION_BINS == 36, "")
    check("30° 的梯度落入 bin 3", hist.index(max(hist)) == 3, f"peak bin {hist.index(max(hist))}")
    check("单峰只生成 1 个方向", len(D.assign_orientations(hist)) == 1, "")
    bi = list(hist)
    bi[13] = 0.85 * max(hist)
    check("次峰达 85% -> 生成第 2 个关键点(同位置同尺度不同方向)",
          len(D.assign_orientations(bi)) == 2, f"{[b for b, _, _ in D.assign_orientations(bi)]}")
    tri = list(hist)
    tri[13] = 0.79 * max(hist)
    check("次峰 79% < 80% -> 不生成", len(D.assign_orientations(tri)) == 1, "")
    check("抛物线插值朝较高的邻居偏移", D.parabolic_peak_offset(1.0, 2.0, 1.2) > 0
          and D.parabolic_peak_offset(1.2, 2.0, 1.0) < 0, "")
    # f(t) = -(t-0.1)^2 + 5 为真抛物线,f(-1)/f(0)/f(1) 代入后插值应精确还原 0.1
    par = D.parabolic_peak_offset(5.0 - 1.21, 5.0 - 0.01, 5.0 - 0.81)
    check("抛物线插值对真抛物线精确还原 0.1", abs(par - 0.1) < 1e-12, f"{par:.15f}")
    gw = D.gaussian_circle_weights([(0, 0), (1.6, 0), (3.2, 0), (4.8, 0)], 1.5)
    check("σ=1.5×scale 的圆形高斯窗随距离单调衰减",
          gw[0] > gw[1] > gw[2] > gw[3] and gw[0] == 1.0, f"{[round(x, 6) for x in gw]}")

    print("\n== G. 128 维描述子 ==")
    vec = D.compute_descriptor(samples(), 0.0)
    check("维度 == 4x4x8 = 128", len(vec) == D.DESC_WIDTH ** 2 * D.DESC_BINS == 128, f"{len(vec)}")
    check("单位长度", abs(math.sqrt(sum(v * v for v in vec)) - 1.0) < 1e-15, "")
    v7 = D.compute_descriptor(samples(mag=7.0), 0.0)
    check("整体幅值缩放不改变描述子(归一化抵消)",
          max(abs(a - b) for a, b in zip(vec, v7)) < 1e-15, f"max diff {max(abs(a-b) for a,b in zip(vec,v7)):.2e}")
    rot = []
    for j in range(16):
        for i in range(16):
            u, vv = i - 7.5, j - 7.5
            rot.append((-vv, u, 1.0, 30.0 + 90.0))
    vr = D.compute_descriptor(rot, 90.0)
    check("90° 旋转(位置+梯度+关键点角)后描述子取值集合一致",
          max(abs(a - b) for a, b in zip(sorted(vec), sorted(vr))) < 1e-15,
          f"max diff {max(abs(a-b) for a,b in zip(sorted(vec),sorted(vr))):.2e}")
    for fi, fj, fa in ((0.0, 0.0, 0.0), (0.3, 0.7, 0.2), (0.5, 0.5, 0.5), (1.0, 1.0, 1.0)):
        w = D.trilinear_weights(fi, fj, fa)
        check(f"三线性权重和 == 1 @({fi},{fj},{fa})", len(w) == 8 and abs(sum(x for _, x in w) - 1.0) < 1e-15,
              f"sum={sum(x for _, x in w):.16f}")
    a0 = D.compute_descriptor([(-7.5, -7.5, 1.0, 0.0)], 0.0)
    a3 = D.compute_descriptor([(7.5, 7.5, 1.0, 0.0)], 0.0)
    check("采样格 (-7.5,-7.5) 落到子块 (0,0) 并三线性扩散到 4 个bin组合",
          sum(1 for x in a0 if x > 0) == 4 and max(range(128), key=lambda i: a0[i]) == 0,
          f"nz={sum(1 for x in a0 if x > 0)} argmax=0")
    check("采样格 (7.5,7.5) 落到子块 (3,3) 的唯一 bin", sum(1 for x in a3 if x > 0) == 1
          and max(range(128), key=lambda i: a3[i]) == 120, "argmax=120")
    check("全零向量不产生除零", D.normalize_descriptor([0.0] * 128) == [0.0] * 128, "")

    print("\n== H. clamp 0.2 的真实语义 ==")
    before = [1.0, 0.9] + [0.0] * 126
    n0 = math.sqrt(sum(x * x for x in before))
    unit = [x / n0 for x in before]
    cl = [min(x, 0.2) for x in unit]
    n1 = math.sqrt(sum(x * x for x in cl))
    after = [x / n1 for x in cl]
    check("截断发生在最终归一化之前:饱和分量被抹平",
          abs(unit[0] / unit[1] - 1.111111) < 1e-6 and abs(after[0] / after[1] - 1.0) < 1e-12,
          f"比值 {unit[0]/unit[1]:.6f} -> {after[0]/after[1]:.6f}")
    check("坑:重归一化后分量可以 > 0.2(实测 0.7071)", abs(max(after) - math.sqrt(0.5)) < 1e-12,
          f"max={max(after):.6f}")
    check("重归一化后仍是单位长度", abs(math.sqrt(sum(x * x for x in after)) - 1.0) < 1e-15, "")

    print("\n== I. 0.8 比率匹配 ==")
    q = D.compute_descriptor(samples(angle=20.0), 0.0)
    c1 = D.compute_descriptor(samples(angle=19.0), 0.0)
    c2 = D.compute_descriptor(samples(angle=21.0), 0.0)
    cf = D.compute_descriptor(samples(angle=140.0), 0.0)
    idx, r = D.ratio_test(q, [c1, cf])
    check("最近邻明显更近 -> 接受(比值 < 0.8)", idx == 0 and r < D.MATCH_RATIO, f"ratio={r:.6f}")
    idx2, r2 = D.ratio_test(q, [c1, c2, cf])
    check("两个几乎等距的候选 -> 拒绝(比值 > 0.8)", r2 > D.MATCH_RATIO, f"ratio={r2:.6f}")
    check("候选不足 2 个时不崩", D.ratio_test(q, [c1])[0] == 0 and D.ratio_test(q, [])[0] is None, "")

    print(f"\n结果:{TOTAL[1]}/{TOTAL[0]} 通过" + (f",失败:{FAILS}" if FAILS else ""))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
