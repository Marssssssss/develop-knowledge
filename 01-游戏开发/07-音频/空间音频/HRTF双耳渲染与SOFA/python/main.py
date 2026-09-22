"""演示入口：SOFA 坐标约定、最近邻插值、双耳线索（ITD/ILD）与渲染闭环。"""

import math

from hrtf import (HRTF, c2s, getfilter, ild_db, itd_seconds, nearest_index,
                  render_mono, s2c)

POS = [[0, 0, 1], [90, 0, 1], [180, 0, 1], [270, 0, 1]]          # 前/左/后/右
IRS = [
    [[1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0]],
    [[0.8, 0.2, 0.0, 0.0], [0.4, 0.1, 0.0, 0.0]],
    [[0.6, 0.0, 0.0, 0.0], [0.6, 0.0, 0.0, 0.0]],
    [[0.4, 0.1, 0.0, 0.0], [0.8, 0.2, 0.0, 0.0]],
]
DELAYS = [[0, 0], [0, 14], [0, 0], [14, 0]]      # 样本（getfilter_short 口径）
LABEL = ["前", "左", "后", "右"]


def main():
    print("== SOFA 坐标：X 前 / Y 左 / Z 上 ==")
    for phi, theta in [(0, 0), (90, 0), (180, 0), (270, 0), (0, 90), (0, -90)]:
        print("  (az=%4d, el=%3d) -> x=%+.3f y=%+.3f z=%+.3f"
              % ((phi, theta) + tuple(s2c([phi, theta, 1]))))
    print("  反算 (0,-1,0) -> az=%.1f el=%.1f r=%.2f" % tuple(c2s([0, -1, 0])))

    hrtf = HRTF(POS, IRS, 48000.0, [[d[0] / 48000.0, d[1] / 48000.0] for d in DELAYS])

    print("\n== 最近邻 + 反距离插值（请求 az=45, el=0）==")
    q = s2c([45, 0, 1])
    print("  请求笛卡尔 (%.3f, %.3f, %.3f) -> 最近测量点 %s"
          % (q[0], q[1], q[2], LABEL[nearest_index(hrtf, q)]))
    nb = [0, 2, -1, -1, -1, -1]   # 一对候选：前(0) / 后(2)，取更近者参与
    l, r, dl, dr = getfilter(hrtf, q, nb, True, "short")
    print("  插值左耳 IR: " + " ".join("%+.4f" % v for v in l))
    print("  插值右耳 IR: " + " ".join("%+.4f" % v for v in r))
    print("  delay = (%d, %d) 样本" % (dl, dr))
    ln, _, _, _ = getfilter(hrtf, q, nb, False, "short")
    print("  绕过插值(nointerp) 左耳: " + " ".join("%+.4f" % v for v in ln))

    print("\n== 四个方向的双耳线索 ==")
    for i, name in enumerate(LABEL):
        _, _, dls, drs = getfilter(hrtf, hrtf.source_cartesian[i], None, True, "short")
        itd = itd_seconds(dls, drs, hrtf.sampling_rate)
        ild = ild_db(IRS[i][0], IRS[i][1])
        print("  %s: delay=(%2d,%2d) ITD=%+7.1f us  ILD=%+6.2f dB"
              % (name, dls, drs, itd * 1e6, ild))

    print("\n== 单冲激的双耳渲染（左侧源）==")
    out_l, out_r = render_mono(hrtf, hrtf.source_cartesian[1], [1.0])
    print("  输出长度 %d = 1 + N-1 + max(delay)" % len(out_l))
    print("  左耳: " + " ".join("%.2f" % v for v in out_l[:18]))
    print("  右耳: " + " ".join("%.2f" % v for v in out_r[:18]))
    print("  能量比右/左 = %.4f（-6dB 的头部阴影）"
          % (math.sqrt(sum(v * v for v in out_r)) / math.sqrt(sum(v * v for v in out_l))))


if __name__ == "__main__":
    main()
