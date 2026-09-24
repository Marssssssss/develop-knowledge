"""697 BKD 树多维点索引 —— 演示入口。

运行：python main.py
先跑自检：python selfcheck_bkd.py
"""

import random

from bkd import BKDConfig, num_leaves_of, get_num_left_leaf_nodes
from bkdtree import build_tree, intersect, pack_index, _bounds, leaves_of

W = 62


def head(title):
    print("\n" + "=" * W)
    print(title)
    print("=" * W)


def main():
    rnd = random.Random(2024)

    head("1. BKDConfig")
    cfg = BKDConfig(num_dims=2, num_index_dims=2, bytes_per_dim=8,
                    max_points_in_leaf_node=32)
    print("  numDims=%d numIndexDims=%d bytesPerDim=%d maxPointsInLeafNode=%d"
          % (cfg.num_dims, cfg.num_index_dims, cfg.bytes_per_dim,
             cfg.max_points_in_leaf_node))
    print("  packedBytesLength=%d packedIndexBytesLength=%d bytesPerDoc=%d"
          % (cfg.packed_bytes_length(), cfg.packed_index_bytes_length(),
             cfg.bytes_per_doc()))
    print("  isDefault=%s" % cfg.is_default())

    head("2. 建树（1000 个二维点）")
    n = 1000
    pts = [(rnd.randrange(1 << 20), rnd.randrange(1 << 20)) for _ in range(n)]
    data = list(zip(range(n), pts))
    tree = build_tree(data, cfg)
    lv = leaves_of(tree)
    print("  点数=%d  叶子数=%d (numLeaves=%d)  内节点=%d"
          % (n, len(lv), num_leaves_of(n, cfg.max_points_in_leaf_node),
             len(lv) - 1))
    print("  最大叶子装点=%d  平均=%.1f"
          % (max(len(x.points) for x in lv),
             sum(len(x.points) for x in lv) / len(lv)))
    print("  根切分: dim=%d value=%d  左子树叶子=%d"
          % (tree.split_dim, tree.split_value,
             len(leaves_of(tree.left))))
    print("  getNumLeftLeafNodes(%d)=%d"
          % (len(lv), get_num_left_leaf_nodes(len(lv))))

    head("3. 索引前缀编码")
    codes = pack_index(tree, cfg)
    naive = len(codes) * cfg.bytes_per_dim
    packed = sum(1 + len(c["suffix"]) for c in codes)
    print("  内节点=%d  朴素=%d 字节  前缀编码=%d 字节  省 %.1f%%"
          % (len(codes), naive, packed, 100.0 * (naive - packed) / naive))
    full = sum(1 for c in codes if c["prefix"] == cfg.bytes_per_dim)
    print("  与父值完全相同（prefix==bytesPerDim，delta=0）的节点=%d" % full)

    head("4. 范围查询：BKD 剪枝 vs 暴力扫描")
    mins, maxs = _bounds(pts, 2)
    print("  %-22s %8s %8s %8s %8s %8s"
          % ("查询区间", "命中", "访问叶", "剪枝", "扫点", "暴力扫"))
    for lo0, hi0 in [(0, (1 << 20) - 1), (100000, 900000),
                     (200000, 400000), (300000, 310000)]:
        qmin = (lo0, lo0)
        qmax = (hi0, hi0)
        hits, st = intersect(tree, cfg, mins, maxs, qmin, qmax)
        brute = sum(1 for v in pts
                    if qmin[0] <= v[0] <= qmax[0] and qmin[1] <= v[1] <= qmax[1])
        assert len(hits) == brute
        print("  [%6d,%6d]^2        %8d %8d %8d %8d %8d"
              % (lo0, hi0, len(hits), st["leaves"], st["pruned"],
                 st["scanned"], n))

    head("5. 一维（numIndexDims=1）+ 小叶子")
    cfg1 = BKDConfig(1, 1, 4, 8)
    pts1 = [(rnd.randrange(1 << 16),) for _ in range(200)]
    d1 = list(zip(range(200), pts1))
    t1 = build_tree(d1, cfg1)
    m1, x1 = _bounds(pts1, 1)
    h1, s1 = intersect(t1, cfg1, m1, x1, (1000,), (3000,))
    want1 = sorted(d for d, v in d1 if 1000 <= v[0] <= 3000)
    print("  叶子数=%d  命中=%d（暴力 %d）  扫点=%d / 200  剪枝=%d"
          % (len(leaves_of(t1)), len(h1), len(want1), s1["scanned"], s1["pruned"]))
    assert h1 == want1

    print("\nOK")


if __name__ == "__main__":
    main()
