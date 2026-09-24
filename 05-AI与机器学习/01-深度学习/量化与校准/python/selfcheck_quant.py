"""量化与校准自检：qparams 三分支 + 量化/反量化 + 三类 observer + 直方图非线性搜索。"""

import math
import random

from quant import (
    EPS,
    HistogramObserver,
    MinMaxObserver,
    MovingAverageMinMaxObserver,
    PerChannelMinMaxObserver,
    calculate_qmin_qmax,
    calculate_qparams,
    check_min_max_valid,
    dequantize,
    fake_quantize,
    fake_quantize_forward,
    quantize_per_tensor,
)

TOL = 1e-9
FAILED = []
PASSED = 0


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
        print("  ok  %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def close(name, a, b, tol=1e-9):
    ok("%s (%r ≈ %r)" % (name, a, b), abs(a - b) <= tol * max(1.0, abs(a), abs(b)))


def allclose(name, xs, ys, tol=1e-9):
    ok("%s (逐项 %d 个)" % (name, len(xs)),
       len(xs) == len(ys) and all(abs(a - b) <= tol * max(1.0, abs(a), abs(b)) for a, b in zip(xs, ys)))


def main():
    # ---- 1. qmin/qmax 档位表 ----
    ok("quint8 默认 (0,255)", calculate_qmin_qmax(None, None, False, "quint8", False) == (0, 255))
    ok("quint8 + reduce_range → (0,127)", calculate_qmin_qmax(None, None, False, "quint8", True) == (0, 127))
    ok("qint8 默认 (-128,127)", calculate_qmin_qmax(None, None, False, "qint8", False) == (-128, 127))
    ok("qint8 + reduce_range → (-64,63)", calculate_qmin_qmax(None, None, False, "qint8", True) == (-64, 63))
    ok("qint32 → (-(2^31), 2^31-1)", calculate_qmin_qmax(None, None, False, "qint32", False) == (-(2 ** 31), 2 ** 31 - 1))
    ok("uint16 → (0, 65535)", calculate_qmin_qmax(None, None, False, "uint16", False) == (0, 2 ** 16 - 1))
    ok("未知 dtype 兜底 (0,15)", calculate_qmin_qmax(None, None, False, "int4", False) == (0, 15))
    ok("自定义 qrange + reduce_range 走整除折半", calculate_qmin_qmax(0, 100, True, "quint8", True) == (0, 50))
    ok("自定义 qrange 不折半时原样返回", calculate_qmin_qmax(0, 100, True, "quint8", False) == (0, 100))
    try:
        calculate_qmin_qmax(0, 300, True, "qint8", False)
        ok("自定义 qrange 超 256 应当报错", False)
    except AssertionError as e:
        ok("自定义 qrange 长度 > 256 报 AssertionError", "256" in str(e))

    # ---- 2. check_min_max_valid ----
    ok("None → False", check_min_max_valid(None, 1.0) is False)
    ok("(inf, -inf) → False（未跑过 observer）", check_min_max_valid(float("inf"), float("-inf")) is False)
    ok("正常 → True", check_min_max_valid(-1.0, 1.0) is True)
    ok("min == max 也算合法（由 eps 分支兜）", check_min_max_valid(0.5, 0.5) is True)
    try:
        check_min_max_valid(2.0, 1.0)
        ok("min > max 应当报错", False)
    except AssertionError:
        ok("min > max 抛 AssertionError", True)

    # ---- 3. affine 分支 ----
    s, z = calculate_qparams(-1.0, 1.0, "per_tensor_affine", 0, 255)
    close("affine：scale = 2/255", s, 2.0 / 255.0)
    ok("affine：zp = 0 − round(−1/scale) = 128", z == 128)
    s2, z2 = calculate_qparams(0.0, 1.0, "per_tensor_affine", 0, 255)
    close("affine（ReLU 后 0..1）：scale = 1/255", s2, 1.0 / 255.0)
    ok("affine（ReLU 后）：zp = 0", z2 == 0)
    s3, z3 = calculate_qparams(-1.0, 1.0, "per_tensor_symmetric", 0, 255)
    close("symmetric（−1..1）：scale = 1/(255/2) = 2/255", s3, 2.0 / 255.0)
    ok("symmetric：quint8 的 zp 恒 128", z3 == 128)
    s4, z4 = calculate_qparams(0.0, 1.0, "per_tensor_symmetric", 0, 255)
    close("symmetric（ReLU 后）：仍按 ±1 取 scale，比 affine 大一倍", s4, 2.0 * s2)
    ok("symmetric（ReLU 后）：zp 仍是 128 → 负半边浪费", z4 == 128)
    s5, z5 = calculate_qparams(-1.0, 0.5, "per_tensor_symmetric", 0, 255)
    close("symmetric 取两侧绝对值较大者（1 > 0.5）", s5, 2.0 / 255.0)
    s6, z6 = calculate_qparams(-0.25, 0.5, "per_tensor_symmetric", 0, 255)
    close("symmetric：min 侧更大时按 −min_neg 算（0.5/(255/2)）", s6, 0.5 / 127.5)

    # 0 恒被纳入量程（min_neg/max_pos 与 0 取 min/max）
    s7, z7 = calculate_qparams(0.3, 0.3, "per_tensor_affine", 0, 255)
    close("min==max=0.3：因 min_neg 被压到 0，scale = 0.3/255 而非 0", s7, 0.3 / 255.0)
    ok("此时 zp = 0（量程从 0 起）", z7 == 0)
    s8, _z8 = calculate_qparams(0.0, 0.0, "per_tensor_affine", 0, 255)
    ok("全零张量 → scale 被 eps 兜住", s8 == EPS)

    # 无效 min/max → (1.0, 0)
    s9, z9 = calculate_qparams(float("inf"), float("-inf"), "per_tensor_affine", 0, 255)
    ok("未跑 observer → scale 1.0", s9 == 1.0)
    ok("未跑 observer → zp 0", z9 == 0)

    # zp 的 clamp：affine 下 scale = (max_pos − min_neg)/255
    _s10, z10 = calculate_qparams(-100.0, 0.0, "per_tensor_affine", 0, 255)
    ok("max_pos=0 时 min_neg/scale 恰为 −255 → zp = 255（未越界）", z10 == 255)
    _s10b, z10b = calculate_qparams(-100.0, 1.0, "per_tensor_affine", 0, 255)
    ok("量程 101 → −100/scale = −252.47 → zp = 252（不需要 clamp）", z10b == 252)
    _s11, z11 = calculate_qparams(-1.0, 0.0, "per_tensor_affine", 0, 255)
    ok("数据全 ≤ 0 时 zp 顶到 qmax=255", z11 == 255)

    # float qparams 分支
    s12, z12 = calculate_qparams(0.2, 0.8, "per_channel_affine_float_qparams", 0, 255)
    close("float qparams：scale = (max−min)/255（不用 0 截断）", s12, 0.6 / 255.0)
    close("float qparams：zp = −min/scale（浮点，不取整）", z12, -0.2 / s12)
    s13, _z13 = calculate_qparams(0.5, 0.5, "per_channel_affine_float_qparams", 0, 255)
    ok("float qparams：scale ≤ eps 时退化为 1.0", s13 == 1.0)

    # ---- 4. 量化 / 反量化 ----
    xq = quantize_per_tensor([-1.0, 0.0, 1.0], 2.0 / 255.0, 128, 0, 255)
    ok("量化：−1→0, 0→128, 1→255", xq == [0, 128, 255])
    ok("饱和：2.0 → clamp 到 255", quantize_per_tensor([2.0], 2.0 / 255.0, 128, 0, 255) == [255])
    ok("banker's rounding（torch.round）：0.5 格 → 0", quantize_per_tensor([0.5 * (2.0 / 255.0)], 2.0 / 255.0, 0, 0, 255) == [0])
    ok("banker's rounding：1.5 格 → 2", quantize_per_tensor([1.5 * (2.0 / 255.0)], 2.0 / 255.0, 0, 0, 255) == [2])
    ok("banker's rounding：2.5 格 → 2（不是 3）", quantize_per_tensor([2.5 * (2.0 / 255.0)], 2.0 / 255.0, 0, 0, 255) == [2])
    xs = [i * 0.01 - 0.5 for i in range(101)]
    fq = fake_quantize(xs, 1.0 / 255.0, 128, 0, 255)
    ok("伪量化误差 ≤ scale/2", all(abs(a - b) <= 0.5 / 255.0 + 1e-12 for a, b in zip(xs, fq)))
    ok("伪量化把连续值压成 256 档", len(set(round(v, 12) for v in fq)) <= 256)
    dq = dequantize([0, 128, 255], 2.0 / 255.0, 128)
    allclose("反量化：q=0/128/255 → (q−128)·2/255",
             dq, [(q - 128) * 2.0 / 255.0 for q in (0, 128, 255)])
    close("256 档网格覆盖 [−256/255, 1]（负端多出一格）", dq[0], -256.0 / 255.0)
    ok("fake_quant_enabled=False → 原样透传", fake_quantize_forward(xs, 1.0, 0, 0, 255, fake_quant_enabled=False) == xs)
    ok("fake_quant_enabled=True → 被压到网格上", fake_quantize_forward([0.12345678], 1.0, 0, 0, 255) != [0.12345678])

    # ---- 5. MinMaxObserver ----
    obs = MinMaxObserver()
    ok("初始 (inf, -inf)", obs.min_val == float("inf") and obs.max_val == float("-inf"))
    ok("空张量直接返回", obs.forward([]) == [])
    obs.forward([-0.3, 0.7])
    obs.forward([-0.9, 0.2])
    close("running min = −0.9", obs.min_val, -0.9)
    close("running max = 0.7", obs.max_val, 0.7)
    s_o, z_o = obs.calculate_qparams()
    close("observer 的 scale 与直接算一致", s_o, calculate_qparams(-0.9, 0.7, "per_tensor_affine", 0, 255)[0])

    # ---- 6. MovingAverageMinMaxObserver ----
    ma = MovingAverageMinMaxObserver(averaging_constant=0.5)
    ma.forward([-1.0, 1.0])
    close("首帧直接取 min（不插值）", ma.min_val, -1.0)
    close("首帧直接取 max", ma.max_val, 1.0)
    ma.forward([-3.0, 3.0])
    close("EMA：min = −1 + 0.5·(−3 − (−1)) = −2", ma.min_val, -2.0)
    close("EMA：max = 1 + 0.5·(3 − 1) = 2", ma.max_val, 2.0)
    ma2 = MovingAverageMinMaxObserver(averaging_constant=0.01)
    ma2.forward([0.0, 10.0])
    ma2.forward([0.0, 20.0])
    close("c=0.01：max 只动 1% → 10.1", ma2.max_val, 10.1)
    ok("EMA 结果落在两次观测之间（负控：不是 running min/max）", 10.0 < ma2.max_val < 20.0)

    # ---- 7. PerChannelMinMaxObserver ----
    pc = PerChannelMinMaxObserver()
    pc.forward([[-1.0, 1.0], [-0.2, 0.2]])
    ok("逐通道各一组 min/max", len(pc.min_vals) == 2 and len(pc.max_vals) == 2)
    close("通道 0 min = −1", pc.min_vals[0], -1.0)
    close("通道 1 max = 0.2", pc.max_vals[1], 0.2)
    qc = pc.calculate_qparams()
    ok("返回 2 组 (scale, zp)", len(qc) == 2)
    close("通道 0 的 scale 是通道 1 的 5 倍（量程 2 vs 0.4）", qc[0][0] / qc[1][0], 5.0)
    pc.forward([[0.0], [-5.0, 5.0]])
    close("跨 batch 累积：通道 1 min = −5", pc.min_vals[1], -5.0)

    # ---- 8. HistogramObserver ----
    ho = HistogramObserver(bins=64, min_val=0.0, max_val=1.0)
    ok("dst_nbins = 2^8 = 256", ho.dst_nbins == 256)
    rnd = random.Random(20260924)
    vals = [rnd.gauss(0.5, 0.05) for _ in range(4000)] + [0.0, 1.0]   # 主体集中 + 两个离群点
    ho.fill(vals)
    ok("直方图计数总和 = 样本数", sum(ho.histogram) == len(vals))
    ok("离群值落在首尾桶", ho.histogram[0] >= 1 and ho.histogram[63] >= 1)
    close("_get_norm：density·(b³−a³)/3", ho._get_norm(0.0, 2.0, 3.0), 3.0 * (8.0 / 3.0))
    close("_get_norm(−1,1,2.5) = 2.5·(1−(−1))/3（x² 的积分）", ho._get_norm(-1.0, 1.0, 2.5), 2.5 * 2.0 / 3.0)
    close("_get_norm(0,1,d) 的 2 倍 = _get_norm(−1,1,d)（x² 偶对称）",
          2 * ho._get_norm(0.0, 1.0, 2.5), ho._get_norm(-1.0, 1.0, 2.5))
    close("_get_norm(a,a,d) = 0（零长度区间）", ho._get_norm(0.7, 0.7, 3.0), 0.0)
    ok("整段不裁剪时量化误差 > 0", ho._compute_quantization_error(0, 63) > 0.0)
    ok("单桶区间仍有非零误差（被排除的桶会被 clamp 到边界档）", ho._compute_quantization_error(10, 10) > 0.0)
    ok("裁掉 1 桶后误差反而变大（被裁桶被 clamp 到边界）",
       ho._compute_quantization_error(0, 62) < ho._compute_quantization_error(0, 60))
    nmin, nmax, sb, eb = ho._non_linear_param_search()
    ok("两端各 1 个离群点：只裁掉最右 1 桶（end_bin=62）", sb == 0 and eb == 62)
    close("new_max = min + bin_width·(end_bin+1) = 63/64", nmax, 63.0 / 64.0)
    ok("裁剪后的区间严格变窄", nmax - nmin < 1.0)
    ok("裁剪后仍包住主体（0.5）", nmin < 0.5 < nmax)

    ho_u = HistogramObserver(bins=32, min_val=0.0, max_val=1.0)
    ho_u.fill([i / 1000.0 for i in range(1001)])
    _umin, _umax, usb, ueb = ho_u._non_linear_param_search()
    ok("均匀分布：只走 1 格就 break（usb=0, ueb=bins−2）", usb == 0 and ueb == 30)

    # 单侧离群簇：只裁掉另一侧的空区间
    rnd7 = random.Random(7)
    body = [rnd7.gauss(0.5, 0.05) for _ in range(4000)]
    hr = HistogramObserver(bins=64, min_val=0.0, max_val=1.0)
    hr.fill(body + [1.0] * 200)
    rmin, rmax, rsb, reb = hr._non_linear_param_search()
    ok("离群簇在右端：裁掉左侧空区间（start_bin>0），右端保留", rsb > 0 and reb == 63)
    close("右簇用例 new_min = 20/64", rmin, 20.0 / 64.0)
    close("右簇用例 new_max = 1.0（未裁）", rmax, 1.0)
    hl = HistogramObserver(bins=64, min_val=0.0, max_val=1.0)
    hl.fill(body + [0.0] * 200)
    lmin, lmax, lsb, leb = hl._non_linear_param_search()
    ok("离群簇在左端：裁掉右侧空区间（end_bin<bins−1），左端保留", lsb == 0 and leb < 63)
    close("左簇用例 new_min = 0.0（未裁）", lmin, 0.0)
    close("左簇用例 new_max = 41/64", lmax, 41.0 / 64.0)

    print()
    print("断言总数 %d，失败 %d" % (PASSED + len(FAILED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  失败：%s" % f)
        raise SystemExit(1)
    print("量化与校准自检全部通过")


if __name__ == "__main__":
    main()
