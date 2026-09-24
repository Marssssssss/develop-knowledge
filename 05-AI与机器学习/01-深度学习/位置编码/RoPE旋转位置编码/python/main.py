"""RoPE 演示入口：把纸上公式与 transformers 源码里的数值对上。"""

import math

from rope import (
    default_inv_freq,
    dot,
    dynamic_ntk_base,
    get_mscale,
    llama3_inv_freq,
    rotate_half,
    rotated_score,
    rotary_emb,
    yarn_inv_freq,
)


def main():
    base, dim = 10000.0, 8
    inv = default_inv_freq(base, dim)
    print("== default inv_freq (base=10000, dim=8) ==")
    print(["%.6g" % v for v in inv])
    print("波长 2π/inv_freq:", ["%.4g" % (2 * math.pi / v) for v in inv])

    print("\n== position 0 的 cos/sin（应为全 1 / 全 0）==")
    cos, sin = rotary_emb(inv, [[0, 1, 2]])
    print("p=0 cos:", ["%.3f" % v for v in cos[0][0][:4]])
    print("p=0 sin:", ["%.3f" % v for v in sin[0][0][:4]])
    print("p=2 cos 前半:", ["%.6f" % v for v in cos[0][2][:4]])
    print("p=2 cos 后半:", ["%.6f" % v for v in cos[0][2][4:]])

    print("\n== rotate_half ==")
    x = [1.0, 2.0, 3.0, 4.0]
    print("x=", x, "→ rotate_half=", rotate_half(x), "→ 两次=", rotate_half(rotate_half(x)))

    print("\n== 相对位置：分数只依赖 (n - m) ==")
    q = [0.3, -1.2, 0.7, 2.1, -0.4, 0.9, 1.5, -0.8, 0.2, 0.6, -1.1, 0.4, 1.9, -0.3, 0.5, 0.8]
    k = [-0.6, 0.4, 1.3, 0.1, 2.0, -0.9, 0.3, 1.1, -1.4, 0.7, 0.2, -0.5, 0.8, 1.6, -0.2, 0.9]
    inv16 = default_inv_freq(base, 16)
    for m, n in ((0, 4), (5, 9), (100, 104), (0, 5)):
        print("  (m=%3d, n=%3d) score = %+.9f" % (m, n, rotated_score(q, k, inv16, m, n)))
    print("  |q| =", "%.9f" % math.sqrt(dot(q, q)))

    print("\n== 外推：dynamic NTK 的 base 随 seq_len 变化（max=8192, factor=4）==")
    for sl in (8192, 16384, 32768, 65536):
        print("  seq_len=%6d → base = %.1f" % (sl, dynamic_ntk_base(base, 64, 4.0, sl, 8192)))

    print("\n== YaRN（dim=64, factor=32, original_max=8192）==")
    yinv, yatt = yarn_inv_freq(base, 64, 32.0, 8192)
    d64 = default_inv_freq(base, 64)
    print("  attention_factor = %.9f (get_mscale(32))" % yatt)
    for i in (0, 12, 20, 25, 31):
        print("  i=%2d  default=%.6e  yarn=%.6e  比值=%.4f" % (i, d64[i], yinv[i], yinv[i] / d64[i]))
    print("  mscale 对照：get_mscale(1)=%.1f, get_mscale(32)=%.6f" % (get_mscale(1.0), get_mscale(32.0)))

    print("\n== llama3（dim=64, factor=8, low=1, high=4, old=8192）==")
    l3 = llama3_inv_freq(base, 64, 8.0, 1.0, 4.0, 8192)
    for i in (0, 20, 22, 24, 31):
        wl = 2 * math.pi / d64[i]
        print("  i=%2d  波长=%9.1f  default=%.6e  llama3=%.6e  比值=%.4f" % (i, wl, d64[i], l3[i], l3[i] / d64[i]))


if __name__ == "__main__":
    main()
