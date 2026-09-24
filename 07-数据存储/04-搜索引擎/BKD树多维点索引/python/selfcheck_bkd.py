"""697 BKD 树 —— 自检（配置 / 前缀 / 树形 / 建树 / 查询）。

索引编码部分见 selfcheck_pack.py。运行：python selfcheck_bkd.py
"""

import random
import sys

from bkd import (BKDConfig, MAX_DIMS, MAX_INDEX_DIMS,
                 DEFAULT_MAX_POINTS_IN_LEAF_NODE, SPLITS_BEFORE_EXACT_BOUNDS,
                 choose_split_dim, common_prefix_length, common_prefix_length_fast,
                 get_num_left_leaf_nodes, needs_exact_bounds, num_leaves_of)
from bkdtree import (build_tree, intersect, _bounds, leaves_of,
                     _to_bytes_list)
from checkutil import eq, ok, raises, report, N_OK


# ---------------------------------------------------------------- 1. BKDConfig
def t_config():
    eq("DEFAULT_CONFIGS 数量", len(BKDConfig.DEFAULT_CONFIGS), 9)
    for nd, ni, b in BKDConfig.DEFAULT_CONFIGS:
        ok("默认配置 is_default %s" % ((nd, ni, b),),
           BKDConfig(nd, ni, b).is_default())
    ok("自定义组合非默认", not BKDConfig(3, 2, 4).is_default())

    c = BKDConfig(7, 4, 4)
    eq("packedBytesLength", c.packed_bytes_length(), 28)
    eq("packedIndexBytesLength", c.packed_index_bytes_length(), 16)
    eq("bytesPerDoc", c.bytes_per_doc(), 32)
    eq("默认 maxPointsInLeafNode", c.max_points_in_leaf_node,
       DEFAULT_MAX_POINTS_IN_LEAF_NODE)

    eq("MAX_DIMS", MAX_DIMS, 16)
    eq("MAX_INDEX_DIMS", MAX_INDEX_DIMS, 8)
    eq("SPLITS_BEFORE_EXACT_BOUNDS", SPLITS_BEFORE_EXACT_BOUNDS, 4)

    raises("numDims=17", lambda: BKDConfig(17, 1, 4))
    raises("numDims=0", lambda: BKDConfig(0, 1, 4))
    raises("numIndexDims=9", lambda: BKDConfig(9, 9, 4))
    raises("numIndexDims>numDims", lambda: BKDConfig(2, 3, 4))
    raises("bytesPerDim=0", lambda: BKDConfig(1, 1, 0))
    raises("maxPoints=0", lambda: BKDConfig(1, 1, 4, 0))


# ------------------------------------------------------- 2. commonPrefixLength
def t_cpl():
    eq("bpd=2 首字节同", common_prefix_length(0x1234, 0x1256, 2), 1)
    eq("bpd=2 首字节异", common_prefix_length(0x0034, 0x0134, 2), 0)
    eq("bpd=2 全同", common_prefix_length(0x1234, 0x1234, 2), 2)
    eq("bpd=4 前缀 2", common_prefix_length(0x11223344, 0x1122FFFF, 4), 2)
    eq("bpd=1 同", common_prefix_length(0x41, 0x41, 1), 1)

    rnd = random.Random(7)
    for _ in range(500):
        bpd = rnd.choice([1, 2, 3, 4, 8])
        m = 1 << (8 * bpd)
        a, b = rnd.randrange(m), rnd.randrange(m)
        s = common_prefix_length(a, b, bpd)
        f = common_prefix_length_fast(a, b, bpd)
        ok("slow==fast", s == f, (a, b, bpd, s, f))
        if a == b:
            ok("相等时前缀=bytesPerDim", s == bpd)
        else:
            ok("不等时该位字节必不同",
               _to_bytes_list(a, bpd)[s] != _to_bytes_list(b, bpd)[s])


# -------------------------------------------------------------- 3. 叶子数划分
def t_num_leaves():
    eq("num_leaves_of(0,512)", num_leaves_of(0, 512), 0)
    eq("num_leaves_of(1,512)", num_leaves_of(1, 512), 1)
    eq("num_leaves_of(512,512)", num_leaves_of(512, 512), 1)
    eq("num_leaves_of(513,512)", num_leaves_of(513, 512), 2)
    eq("num_leaves_of(7,3)", num_leaves_of(7, 3), 3)

    # 手算：lastFullLevel=31-nlz(n)，满层一半 + 尽量多塞不平衡部分
    for n, want in [(2, 1), (3, 2), (4, 2), (5, 3), (6, 4), (7, 4),
                    (8, 4), (9, 5), (17, 9), (33, 17)]:
        eq("getNumLeftLeafNodes(%d)" % n, get_num_left_leaf_nodes(n), want)
    for n in range(2, 300):
        left = get_num_left_leaf_nodes(n)
        right = n - left
        ok("numLeft >= numRight", left >= right, (n, left, right))
        ok("numLeft <= 2*numRight", left <= 2 * right, (n, left, right))


# ---------------------------------------------------------------- 4. split 维度
def t_split_dim():
    # 「2 倍」规则优先：dim0 被切次数 < max/2 时，即使跨度更小也选 dim0
    eq("2 倍规则压过跨度", choose_split_dim([0, 0], [10, 100], [0, 2], 2), 0)
    # 该维度取值全等时跳过
    eq("全等维度被跳过", choose_split_dim([5, 0], [5, 100], [0, 2], 2), 1)
    # 无维度满足 2 倍规则 -> 取跨度最大
    eq("退化为最大跨度", choose_split_dim([0, 0], [10, 100], [2, 2], 2), 1)
    eq("跨度相等取首个", choose_split_dim([0, 0], [10, 10], [2, 2], 2), 0)
    eq("一维", choose_split_dim([0, 0], [1, 9], [0, 0], 1), 0)


def t_exact_bounds():
    ok("根节点永不算精确包围盒", not needs_exact_bounds(8, 8, 3, [1, 1, 1]))
    ok("numIndexDims<=2 永不算", not needs_exact_bounds(4, 8, 2, [1, 1]))
    ok("非根+3维+切分次数是 4 的倍数 -> 算", needs_exact_bounds(4, 8, 3, [1, 1, 2]))
    ok("非根+3维+切分次数非 4 的倍数 -> 不算",
       not needs_exact_bounds(4, 8, 3, [1, 1, 1]))
    ok("4 维同样生效", needs_exact_bounds(4, 8, 4, [0, 0, 0, 0]))


# ------------------------------------------------------------------ 5. 建树
def points_of(node):
    if node.kind == "leaf":
        return list(node.points)
    return points_of(node.left) + points_of(node.right)


def check_partition(node):
    """每个内节点：左半所有点的 splitDim <= splitValue <= 右半所有点。"""
    if node.kind == "leaf":
        return
    d = node.split_dim
    ok("左半不超过 splitValue",
       all(p[1][d] <= node.split_value for p in points_of(node.left)),
       (d, node.split_value))
    ok("右半不小于 splitValue",
       all(p[1][d] >= node.split_value for p in points_of(node.right)),
       (d, node.split_value))
    check_partition(node.left)
    check_partition(node.right)


def t_build():
    rnd = random.Random(20240924)
    for trial in range(40):
        nd = rnd.choice([1, 2, 3, 4])
        ni = rnd.choice([1, 2, 3]) if nd >= 3 else nd
        bpd = rnd.choice([1, 2, 4])
        maxleaf = rnd.choice([2, 3, 4, 8, 512])
        cfg = BKDConfig(nd, ni, bpd, maxleaf)
        npts = rnd.randrange(1, 80)
        m = 1 << (8 * bpd)
        pts = [tuple(rnd.randrange(m) for _ in range(nd)) for _ in range(npts)]
        data = list(zip(range(npts), pts))
        tree = build_tree(data, cfg)

        lv = leaves_of(tree)
        eq("叶子数 == numLeaves (%d)" % trial, len(lv),
           num_leaves_of(npts, maxleaf))
        ok("每个叶子装点不超过上限",
           all(len(l.points) <= maxleaf for l in lv),
           max(len(l.points) for l in lv))
        eq("叶子点集并集 == 原始点集",
           sorted(dv for l in lv for dv in l.points), sorted(data))
        check_partition(tree)

        for lf in lv:
            ok("cpl[sortedDim] < bytesPerDim 或全等点",
               lf.common_prefix_lengths[lf.sorted_dim] < bpd
               or len(set(p[1] for p in lf.points)) == 1,
               lf.common_prefix_lengths)
            eq("leafCardinality == 不同点数",
               lf.leaf_cardinality, len(set(p[1] for p in lf.points)))
            ok("叶子按 sortedDim 有序",
               all(lf.points[i][1][lf.sorted_dim] <=
                   lf.points[i + 1][1][lf.sorted_dim]
                   for i in range(len(lf.points) - 1)))


# -------------------------------------------------------------- 6. 范围查询
def t_intersect():
    rnd = random.Random(99)
    for trial in range(40):
        nd = rnd.choice([1, 2, 3])
        ni = min(nd, rnd.choice([1, 2, 3]))
        bpd = rnd.choice([1, 2, 4])
        cfg = BKDConfig(nd, ni, bpd, rnd.choice([2, 3, 4, 512]))
        npts = rnd.randrange(1, 80)
        m = 1 << (8 * bpd)
        pts = [tuple(rnd.randrange(m) for _ in range(nd)) for _ in range(npts)]
        data = list(zip(range(npts), pts))
        tree = build_tree(data, cfg)
        mins, maxs = _bounds(pts, nd)
        for _q in range(6):
            lo = [rnd.randrange(m) for _ in range(nd)]
            hi = [min(m - 1, lo[d] + rnd.randrange(m)) for d in range(nd)]
            hits, st = intersect(tree, cfg, mins, maxs, lo, hi)
            want = sorted(d for d, v in data
                          if all(lo[k] <= v[k] <= hi[k] for k in range(nd)))
            eq("命中集合 == 暴力枚举", hits, want)
            ok("访问节点数 <= 全树节点",
               st["inner"] + st["leaves"] <= 2 * len(leaves_of(tree)) - 1, st)
        hits, st = intersect(tree, cfg, mins, maxs, [0] * nd, [m - 1] * nd)
        eq("全空间查询命中全部", hits, sorted(d for d, _ in data))
        eq("全空间不剪任何枝", st["pruned"], 0)
        hits, st = intersect(tree, cfg, mins, maxs, [m - 1] * nd, [0] * nd)
        eq("空区间无命中", hits, [])

    # 剪枝确实省了扫描
    rnd2 = random.Random(5)
    npts = 400
    cfg = BKDConfig(2, 2, 4, 16)
    pts = [(rnd2.randrange(100000), rnd2.randrange(100000)) for _ in range(npts)]
    data = list(zip(range(npts), pts))
    tree = build_tree(data, cfg)
    mins, maxs = _bounds(pts, 2)
    hits, st = intersect(tree, cfg, mins, maxs, (1000, 1000), (2000, 2000))
    want = sorted(d for d, v in data
                  if 1000 <= v[0] <= 2000 and 1000 <= v[1] <= 2000)
    eq("窄查询命中", hits, want)
    ok("窄查询扫的点远少于全量", st["scanned"] < npts // 2, st)
    ok("窄查询有剪枝", st["pruned"] > 0, st)


def main():
    t_config()
    t_cpl()
    t_num_leaves()
    t_split_dim()
    t_exact_bounds()
    t_build()
    t_intersect()
    report("selfcheck_bkd")


if __name__ == "__main__":
    sys.exit(main())
