"""HNSW 演示入口：建图 -> 查询 -> 打印可观测的机制指标。"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hnsw import HierarchicalNSW  # noqa: E402
from hnsw_prim import (  # noqa: E402
    brute_knn, get_random_level, lcg_points, lcg_uniforms,
)


def main():
    data = lcg_points(300, 4, seed=90210)
    g = HierarchicalNSW(dim=4, M=16, ef_construction=64, ef=16,
                        uniform_source=lcg_uniforms(600, seed=555))
    for v in data:
        g.add_point(v)

    print("== 层级分布（M=16, mult=1/log M） ==")
    hist = {}
    for lv in g.levels:
        hist[lv] = hist.get(lv, 0) + 1
    for lv in sorted(hist):
        print("  level %d : %3d 点" % (lv, hist[lv]))
    print("  理论 P(level>=1) = 1/M = %.4f，实测 %.4f"
          % (1 / 16.0, sum(v for k, v in hist.items() if k >= 1) / 300.0))
    print("  边界演示: getRandomLevel(1/M)=%d, getRandomLevel(1/M^2)=%d"
          % (get_random_level(1 / 16.0, 1 / math.log(16.0)),
             get_random_level(1 / 256.0, 1 / math.log(16.0))))

    print("\n== 出度上限 ==")
    d0 = max(len(g.neighbors(i, 0)) for i in range(len(data)))
    up = [i for i in range(len(data)) if g.levels[i] > 0]
    du = max((len(g.neighbors(i, lv)) for i in up for lv in range(1, g.levels[i] + 1)),
             default=0)
    print("  底层最大出度 %d (maxM0=2M=%d)" % (d0, 2 * 16))
    print("  高层最大出度 %d (maxM=M=%d)" % (du, 16))

    print("\n== ef 与召回（k=10，注意 ef 会被 max(ef_,k) 抬到 >=10） ==")
    qs = lcg_points(20, 4, seed=4242)
    for ef in (1, 4, 16, 64, 300):
        hit = tot = 0
        for q in qs:
            got = set(n for _, n in g.search_knn(q, 10, ef=ef))
            want = set(n for _, n in brute_knn(q, data, 10))
            hit += len(got & want)
            tot += 10
        print("  ef=%4d  recall@10 = %.3f" % (ef, hit / float(tot)))

    print("\n== k=1 时 ef 才真正生效（不被 k 抬高） ==")
    for ef in (1, 2, 8, 300):
        hit = 0
        for q in qs:
            got = g.search_knn(q, 1, ef=ef)[0][1]
            hit += 1 if got == brute_knn(q, data, 1)[0][1] else 0
        print("  ef=%4d  recall@1 = %.3f" % (ef, hit / float(len(qs))))

    print("\n== 距离计算次数（ef 的代价） ==")
    for ef in (10, 64, 300):
        g.dist_computations = 0
        for q in qs:
            g.search_knn(q, 10, ef=ef)
        print("  ef=%4d  20 次查询共 %d 次距离计算" % (ef, g.dist_computations))


if __name__ == "__main__":
    main()
