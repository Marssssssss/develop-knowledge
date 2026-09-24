"""量化与校准演示入口：对称 vs 仿射的代价、三类 observer 的统计口径、直方图裁剪。"""

import random

from quant import (
    HistogramObserver,
    MinMaxObserver,
    MovingAverageMinMaxObserver,
    PerChannelMinMaxObserver,
    calculate_qparams,
    calculate_qmin_qmax,
    fake_quantize,
)


def main():
    print("== 对称 vs 仿射（quint8, 数据 0..1）==")
    for qs in ("per_tensor_affine", "per_tensor_symmetric"):
        s, z = calculate_qparams(0.0, 1.0, qs, 0, 255)
        print("  %-28s scale=%.9f zp=%d" % (qs, s, z))
    sa, _ = calculate_qparams(0.0, 1.0, "per_tensor_affine", 0, 255)
    ss, _ = calculate_qparams(0.0, 1.0, "per_tensor_symmetric", 0, 255)
    print("  对称量化把量化分辨率浪费了一半：scale 比 %s = %.4f"
          % ("仿射大", ss / sa))

    print("\n== dtype 档位表（reduce_range 折半）==")
    for dt in ("quint8", "qint8", "qint32", "uint16", "int16", "int4"):
        a = calculate_qmin_qmax(None, None, False, dt, False)
        b = calculate_qmin_qmax(None, None, False, dt, True)
        print("  %-8s 默认 %s / reduce_range %s" % (dt, a, b))

    print("\n== 伪量化误差（scale=1/255）==")
    xs = [i * 0.0037 for i in range(64)]
    fq = fake_quantize(xs, 1.0 / 255.0, 0, 0, 255)
    errs = [abs(a - b) for a, b in zip(xs, fq)]
    print("  最大误差 %.9f，理论上限 scale/2 = %.9f" % (max(errs), 0.5 / 255.0))

    print("\n== observer 的统计口径（同一串数据 min=−1, max=1 后接 −3, 3）==")
    mm = MinMaxObserver()
    mm.forward([-1.0, 1.0])
    mm.forward([-3.0, 3.0])
    print("  MinMax          : (%.4f, %.4f)" % (mm.min_val, mm.max_val))
    ma = MovingAverageMinMaxObserver(averaging_constant=0.5)
    ma.forward([-1.0, 1.0])
    ma.forward([-3.0, 3.0])
    print("  MovingAvg c=0.5 : (%.4f, %.4f)" % (ma.min_val, ma.max_val))

    print("\n== 逐通道量化（通道量程差 5 倍）==")
    pc = PerChannelMinMaxObserver()
    pc.forward([[-1.0, 1.0], [-0.2, 0.2]])
    for i, (s, z) in enumerate(pc.calculate_qparams()):
        print("  通道 %d: scale=%.9f zp=%d" % (i, s, z))

    print("\n== HistogramObserver 的非线性搜索（bins=64, dst_nbins=256）==")
    rnd = random.Random(7)
    body = [rnd.gauss(0.5, 0.05) for _ in range(4000)]
    for tag, vals in (("主体 + 右端 200 个离群", body + [1.0] * 200),
                      ("主体 + 左端 200 个离群", body + [0.0] * 200),
                      ("均匀分布", [i / 1000.0 for i in range(1001)])):
        h = HistogramObserver(bins=64, min_val=0.0, max_val=1.0)
        h.fill(vals)
        nmin, nmax, sb, eb = h._non_linear_param_search()
        print("  %-22s → new_min=%.6f new_max=%.6f (start=%d, end=%d)" % (tag, nmin, nmax, sb, eb))


if __name__ == "__main__":
    main()
