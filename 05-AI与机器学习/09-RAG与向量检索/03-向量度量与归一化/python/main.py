"""向量度量空间演示：三种度量的分歧、归一化的代价、SIMD 求和顺序。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metrics import (  # noqa: E402
    cosine, inner_product, inner_product_as_simd, inner_product_distance,
    l2sqr, lcg_points, naive_accumulate, normalize, rank_by_cosine,
    rank_by_ip, rank_by_l2, simd_route,
)


def main():
    raw = lcg_points(40, 6, seed=11)
    unit = [normalize(v) for v in raw]
    q_raw = lcg_points(1, 6, seed=12)[0]
    q_unit = normalize(q_raw)

    print("== 归一化前后：三种度量的 top-5 是否一致 ==")
    for tag, pts, q in [("未归一化", raw, q_raw), ("已归一化", unit, q_unit)]:
        rl = rank_by_l2(q, pts)[:5]
        ri = rank_by_ip(q, pts)[:5]
        rc = rank_by_cosine(q, pts)[:5]
        print("  %-6s  L2=%s" % (tag, rl))
        print("  %-6s  IP=%s" % ("", ri))
        print("  %-6s  COS=%s   三者一致=%s"
              % ("", rc, rl == ri == rc))

    print("\n== 长度主导的反例 ==")
    query = [1.0, 0.0]
    long_same, short_opp = [10.0, 0.0], [-0.5, 0.0]
    print("  q=%s" % query)
    print("  L2  q-> 短反向 %-12s = %.4f" % (short_opp, l2sqr(query, short_opp)))
    print("  L2  q-> 长同向 %-12s = %.4f" % (long_same, l2sqr(query, long_same)))
    print("  IP  q-> 短反向 %-12s = %.4f" % (short_opp, inner_product(query, short_opp)))
    print("  IP  q-> 长同向 %-12s = %.4f" % (long_same, inner_product(query, long_same)))
    print("  => L2 选短反向，IP 选长同向")

    print("\n== hnswlib 的 InnerProductDistance = 1 - IP ==")
    for pair in [([1.0, 0.0], [0.0, 1.0]), ([2.0, 0.0], [1.0, 0.0]),
                 ([0.0, 0.0], [0.0, 0.0])]:
        print("  %s · %s -> IP=%.3f  距离=%.3f"
              % (pair[0], pair[1], inner_product(*pair),
                 inner_product_distance(*pair)))
    print("  注：距离可以为负，且它不是余弦距离（没做归一化）")

    print("\n== SIMD 派发：由 dim 的整除性决定 ==")
    for dim in (3, 4, 5, 7, 8, 12, 16, 17, 20, 32, 64):
        print("  dim=%3d -> %s" % (dim, simd_route(dim)))

    print("\n== 求和顺序的代价（同一份数据三条路径） ==")
    cat = [1e16, 1.0, 1.0, 1.0, -1e16, 1.0, 1.0, 1.0]
    ones = [1.0] * 8
    print("  朴素累加(C++ 标量) %.1f" % inner_product(cat, ones))
    print("  SIMD4  分块       %.1f" % inner_product_as_simd(cat, ones, "SIMD4Ext"))
    print("  SIMD16 分块       %.1f" % inner_product_as_simd(cat, ones, "SIMD16Ext"))
    print("  Python sum()      %.1f  <- 3.12+ 对 float 用补偿求和，与朴素累加不同"
          % sum(cat))
    print("  naive_accumulate  %.1f" % naive_accumulate(cat))


if __name__ == "__main__":
    main()
